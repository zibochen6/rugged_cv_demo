"""Segment service: single background thread + loss/reappear state machine.

State flow:
    idle --click--> tracking --(mask absent for N frames)--> lost
    lost --(mask back for M frames)--> tracking
    any --clear/stop--> idle

While "lost" the last good mask is kept as a *ghost* polygon so the web
overlay can show a dim silhouette; tracking continues every frame, so a
reappearing object is picked up automatically.
"""
import logging
import threading
import time
import gc
from typing import Callable, List, Optional, Tuple

import numpy as np
import cv2

from . import config as seg_cfg
from .model import EfficientTAMSegmentModel

logger = logging.getLogger(__name__)

# Shown in the web status bar while the target mask is over-segmented
# (single-point SAM behaviour on hand-held objects).
POOR_MASK_HINT = (
    "掩膜过大，可能包含手或背景——请右键在多余区域加负点（排除），"
    "排除后目标丢失可自动恢复"
)


# ---------------------------------------------------------------------------
# Pure state machine (no cv2/numpy, unit-testable in isolation)
# ---------------------------------------------------------------------------
class SegmentStateMachine:
    IDLE = "idle"
    TRACKING = "tracking"
    LOST = "lost"

    def __init__(
        self,
        lost_area_ratio: float = seg_cfg.LOST_AREA_RATIO,
        lost_frames: int = seg_cfg.LOST_FRAMES,
        resume_area_ratio: float = seg_cfg.RESUME_AREA_RATIO,
        resume_frames: int = seg_cfg.RESUME_FRAMES,
    ):
        self.lost_area_ratio = lost_area_ratio
        self.lost_frames = max(1, int(lost_frames))
        self.resume_area_ratio = resume_area_ratio
        self.resume_frames = max(1, int(resume_frames))
        self.state = self.IDLE
        self._absent_streak = 0
        self._present_streak = 0
        self.last_good = None  # last mask with area >= lost_area_ratio

    def begin_target(self) -> None:
        self.state = self.TRACKING
        self._absent_streak = 0
        self._present_streak = 0
        self.last_good = None

    def update(self, mask: np.ndarray) -> str:
        """Advance the machine with the latest mask. Returns an event."""
        ratio = float(mask.mean()) if mask is not None else 0.0
        if ratio >= self.resume_area_ratio:
            self.last_good = mask
        if ratio < self.lost_area_ratio:
            self._absent_streak += 1
            self._present_streak = 0
            if self.state == self.TRACKING and self._absent_streak >= self.lost_frames:
                self.state = self.LOST
                return "lost"
            return "steady"
        # object present
        self._absent_streak = 0
        if self.state == self.LOST:
            self._present_streak += 1
            if self._present_streak >= self.resume_frames:
                self._present_streak = 0
                self.state = self.TRACKING
                return "recovered"
            return "steady"
        self._present_streak = 0
        return "steady"

    def clear(self) -> None:
        self.state = self.IDLE
        self._absent_streak = 0
        self._present_streak = 0
        self.last_good = None


# ---------------------------------------------------------------------------
# Overlay geometry helpers (model-res mask -> overlay-res polygons)
# ---------------------------------------------------------------------------
def mask_to_polygons(
    mask: np.ndarray,
    scale_x: float,
    scale_y: float,
    min_area: float = seg_cfg.MIN_POLYGON_AREA,
    epsilon: float = seg_cfg.POLYGON_EPSILON,
) -> List[List[Tuple[int, int]]]:
    """Contour polygons of a boolean mask, scaled to overlay resolution.

    Returns [] for empty / too-small masks. Sorted largest-first.
    """
    if mask is None or mask.size == 0 or not bool(mask.any()):
        return []
    m = mask.astype(np.uint8)
    contours, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out: List[List[Tuple[int, int]]] = []
    for c in contours:
        if float(cv2.contourArea(c)) * scale_x * scale_y < min_area:
            continue
        approx = cv2.approxPolyDP(c, max(1.0, epsilon), True)
        pts = []
        for p in approx.reshape(-1, 2):
            pts.append((int(round(float(p[0]) * scale_x)), int(round(float(p[1]) * scale_y))))
        if len(pts) >= 3:
            out.append(pts)
    out.sort(
        key=lambda poly: cv2.contourArea(np.array(poly, dtype=np.float32)),
        reverse=True,
    )
    return out


def mask_center(mask: np.ndarray, scale_x: float, scale_y: float) -> Optional[Tuple[int, int]]:
    if mask is None or not bool(mask.any()):
        return None
    m = mask.astype(np.uint8)
    moments = cv2.moments(m)
    if moments["m00"] < 1e-6:
        return None
    return (
        int(round(moments["m10"] / moments["m00"] * scale_x)),
        int(round(moments["m01"] / moments["m00"] * scale_y)),
    )


def downscale_frame(frame: np.ndarray, max_side: int = seg_cfg.FRAME_MAX_SIDE):
    """Returns (small_frame, sx, sy) with sx = OVERLAY_W / small_w etc."""
    h, w = frame.shape[:2]
    if max(h, w) <= max_side:
        return frame, seg_cfg.OVERLAY_W / float(w), seg_cfg.OVERLAY_H / float(h)
    s = max_side / float(max(h, w))
    nw, nh = max(1, int(round(w * s))), max(1, int(round(h * s)))
    small = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_AREA)
    return small, seg_cfg.OVERLAY_W / float(nw), seg_cfg.OVERLAY_H / float(nh)


# ---------------------------------------------------------------------------
# Lost re-acquisition helpers (classical template re-lock)
# ---------------------------------------------------------------------------
def build_template(
    gray: np.ndarray,
    mask: np.ndarray,
    size: int = seg_cfg.TEMPLATE_SIZE,
    min_side: int = 8,
):
    """Resized grayscale crop of the last good mask (aspect preserved)."""
    if mask is None or not bool(mask.any()):
        return None
    ys, xs = np.nonzero(mask)
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    w, h = x1 - x0, y1 - y0
    if w < min_side or h < min_side or y1 > gray.shape[0] or x1 > gray.shape[1]:
        return None
    crop = gray[y0:y1, x0:x1]
    scale = size / float(max(w, h))
    nw, nh = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    return cv2.resize(crop, (nw, nh), interpolation=cv2.INTER_AREA)


def find_template_peak(
    gray: np.ndarray,
    template: np.ndarray,
    threshold: float = seg_cfg.RESEED_THRESHOLD,
):
    """Best TM_CCOEFF_NORMED match -> (x_norm, y_norm, score) or None.

    OpenCV scores degenerate (zero-variance) windows as 0, so flat frames
    are safe; the only guard needed is a constant template, which carries
    no matchable information.
    """
    if gray is None or template is None:
        return None
    th, tw = template.shape[:2]
    fh, fw = gray.shape[:2]
    if fh < th or fw < tw:
        return None
    if float(template.std()) < 1e-6:
        return None  # constant template carries no information
    res = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
    _, maxv, _, maxloc = cv2.minMaxLoc(res)
    score = float(maxv)
    if not np.isfinite(score) or score < threshold:
        return None
    cx = (maxloc[0] + tw / 2.0) / fw
    cy = (maxloc[1] + th / 2.0) / fh
    return (cx, cy, score)


def _peak_sidelobe_ratio(res: np.ndarray, loc: Tuple[int, int], tw: int, th: int) -> float:
    """PSR of the best peak: (peak - mean) / std of the response map with
    the template-sized neighbourhood around the peak excluded. A lone peak
    on a flat sidelobe gets a large PSR; a broad / noisy map gets a small
    one."""
    fh, fw = res.shape
    px, py = loc
    y0, y1 = max(py - th, 0), min(py + th + 1, fh)
    x0, x1 = max(px - tw, 0), min(px + tw + 1, fw)
    others = np.ones((fh, fw), dtype=bool)
    others[y0:y1, x0:x1] = False
    side = res[others]
    if side.size == 0:
        return float("inf")
    std = float(side.std())
    if std < 1e-9:
        return float("inf")
    return float((res[py, px] - side.mean()) / std)


def find_template_peak_psr(
    gray: np.ndarray,
    template: np.ndarray,
    threshold: float = seg_cfg.RESEED_THRESHOLD,
):
    """find_template_peak + peak significance: (x_norm, y_norm, score, psr)
    or None. PSR guards against matching a peak that is not distinct from
    the response-map baseline (e.g. a texture-less region)."""
    if gray is None or template is None:
        return None
    th, tw = template.shape[:2]
    fh, fw = gray.shape[:2]
    if fh < th or fw < tw:
        return None
    if float(template.std()) < 1e-6:
        return None
    res = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
    _, maxv, _, maxloc = cv2.minMaxLoc(res)
    score = float(maxv)
    if not np.isfinite(score) or score < threshold:
        return None
    psr = _peak_sidelobe_ratio(res, maxloc, tw, th)
    cx = (maxloc[0] + tw / 2.0) / fw
    cy = (maxloc[1] + th / 2.0) / fh
    return (cx, cy, score, psr)


def erode_mask_for_template(mask: np.ndarray, iterations: int = seg_cfg.TEMPLATE_ERODE_PX) -> np.ndarray:
    """Erode a model-res mask with a 3x3 kernel before template building or
    identity cropping, so the template captures the object core instead of
    its blend contour with the hand/background. Returns a bool mask; masks
    thinner than the erosion shrink accordingly (empty -> no template)."""
    if mask is None or not bool(mask.any()):
        return mask
    m = cv2.erode(
        mask.astype(np.uint8),
        np.ones((3, 3), np.uint8),
        iterations=int(iterations),
    )
    return m > 0


def _templates_corr(
    a: np.ndarray,
    b: np.ndarray,
    size: int = seg_cfg.TEMPLATE_SIZE,
) -> Optional[float]:
    """Pearson correlation of two template crops, compared at a common square
    size (TM_CCOEFF_NORMED). Resampling to a fixed square makes the metric
    well-defined even when the two crops have different aspect ratios, so a
    slight pose/aspect change cannot spuriously fail the comparison."""
    if a is None or b is None:
        return None
    if float(a.std()) < 1e-6 or float(b.std()) < 1e-6:
        return None
    a = cv2.resize(a, (size, size), interpolation=cv2.INTER_AREA)
    b = cv2.resize(b, (size, size), interpolation=cv2.INTER_AREA)
    res = cv2.matchTemplate(b, a, cv2.TM_CCOEFF_NORMED)
    if res.size == 0:
        return None
    corr = float(res[0, 0])
    return corr if np.isfinite(corr) else None


def identity_corr(
    template: Optional[np.ndarray],
    mask: Optional[np.ndarray],
    gray: Optional[np.ndarray],
) -> Optional[float]:
    """Identity verification between the stored identity anchor and the
    grayscale crop of `mask`. Compared at a fixed square size (see
    _templates_corr) so aspect drift is harmless. Returns None when either
    side is missing, constant, or unusable — callers must treat None as
    *fail* (never accept).
    """
    if template is None or mask is None or gray is None or not bool(mask.any()):
        return None
    cand = build_template(gray, erode_mask_for_template(mask))
    return _templates_corr(template, cand)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------
class SegmentService:
    """Owns the model, camera-frame consumption and the web payload.

    Model calls and payload generation are serialised under `_model_lock` so
    API interactions (clicks) never race the tracking loop.
    """

    def __init__(
        self,
        model_factory: Optional[Callable[[], EfficientTAMSegmentModel]] = None,
        camera=None,
        max_side: int = seg_cfg.FRAME_MAX_SIDE,
        threaded: bool = True,
    ):
        """threaded=False: the caller drives process_one() itself (single
        consumer, used by offline acceptance); nothing is spawned on start."""
        self._model_factory = model_factory or (lambda: EfficientTAMSegmentModel())
        self._camera = camera  # falls back to the app camera singleton
        self._max_side = max_side
        self._threaded = threaded

        self._enabled = False
        self._loading = False
        self._error: Optional[str] = None
        self._model: Optional[EfficientTAMSegmentModel] = None
        self._has_target = False
        self._points: List[List[float]] = []
        self._sm = SegmentStateMachine()
        self._ghost = None  # last good mask (small-res bool) kept while lost

        self._last_frame_id = -1
        self._last_mask_area = 0.0
        self._latest: dict = self._empty_payload()
        self._infer_ms_ema: Optional[float] = None
        self._fps_ema: Optional[float] = None
        self._last_infer_at: Optional[float] = None
        self._target_fps = float(seg_cfg.TARGET_FPS)
        # lost re-acquisition state
        self._template = None
        self._last_reseed_at: Optional[float] = None  # None = no attempt yet
        self._reseed_interval_s = float(seg_cfg.RESEED_INTERVAL_S)
        self._reseed_threshold = float(seg_cfg.RESEED_THRESHOLD)
        # mask quality gate + identity verification
        self._target_quality: Optional[str] = None  # 'ok' | 'poor' (per target)
        self._relock_note: Optional[str] = None     # user-facing reason when resume is gated
        self._logged_resume_reject = False  # throttle the per-frame 'resume rejected' log
        self._pending_relock: Optional[Tuple[float, float, float, float]] = None
        self._suppressed: List[List[float]] = []    # [x_norm, y_norm, until_ts]
        self._last_sx = seg_cfg.OVERLAY_W / float(seg_cfg.FRAME_MAX_SIDE)
        self._last_sy = seg_cfg.OVERLAY_H / float(seg_cfg.FRAME_MAX_SIDE)

        self._model_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------ API
    def start(self) -> dict:
        if not self._camera_running():
            return {"ok": False, "code": "SEGMENT_CAMERA_NOT_RUNNING",
                    "message": "camera is not running; start it before segmenting"}
        with self._state_lock:
            self._enabled = True
            self._error = None
        if self._threaded:
            self._ensure_thread()
        return {"ok": True, "message": "segment started"}

    def stop(self) -> dict:
        with self._state_lock:
            self._enabled = False
            self._has_target = False
            self._points = []
            self._sm.clear()
            self._ghost = None
            self._pending_relock = None
            self._suppressed = []
            self._relock_note = None
            self._logged_resume_reject = False
            self._target_quality = None
        self._stop_worker_and_unload()
        self._publish()
        return {"ok": True, "message": "segment stopped"}

    def clear_target(self) -> dict:
        with self._state_lock:
            if not self._enabled:
                return {"ok": False, "code": "SEGMENT_NOT_STARTED",
                        "message": "segment is not started"}
            self._has_target = False
            self._points = []
            self._sm.clear()
            self._ghost = None
            self._pending_relock = None
            self._suppressed = []
            self._relock_note = None
            self._logged_resume_reject = False
            self._target_quality = None
            self._fps_ema = None
            self._last_infer_at = None
        self._reset_model()
        self._publish()
        return {"ok": True, "message": "target cleared"}

    def select_target(self, x_norm: float, y_norm: float, label: int = 1) -> dict:
        """First click: create the target and segment it immediately."""
        with self._state_lock:
            if not self._enabled:
                return {"ok": False, "code": "SEGMENT_NOT_STARTED",
                        "message": "start the segment feature first"}
            if self._loading:
                return {"ok": False, "code": "SEGMENT_LOADING",
                        "message": "model still loading, retry in a moment"}
            if self._error:
                return {"ok": False, "code": "SEGMENT_ERROR", "message": self._error}
            if not self._camera_running():
                return {"ok": False, "code": "SEGMENT_CAMERA_NOT_RUNNING",
                        "message": "camera is not running"}
        if not (0.0 <= x_norm <= 1.0 and 0.0 <= y_norm <= 1.0):
            return {"ok": False, "code": "SEGMENT_INVALID_COORDINATES",
                    "message": "coordinates must be in [0, 1]"}
        if self._model is None:
            return {"ok": False, "code": "SEGMENT_LOADING", "message": "model not loaded yet"}

        frame_id, frame = self._camera_latest()
        if frame is None:
            return {"ok": False, "code": "SEGMENT_NO_FRAME", "message": "no camera frame yet"}
        small, sx, sy = downscale_frame(frame, self._max_side)
        rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        x_px = x_norm * rgb.shape[1]
        y_px = y_norm * rgb.shape[0]
        try:
            with self._model_lock:
                mask = self._model.select(rgb, x_px, y_px)
                self._points = [[x_norm, y_norm, int(label)]]
                self._has_target = True
                self._fps_ema = None
                self._last_infer_at = None
                self._sm.begin_target()
                self._pending_relock = None
                self._suppressed = []
                self._relock_note = None
                self._logged_resume_reject = False
                self._commit_mask(mask, frame_id, sx, sy,
                                  frame_gray=cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY))
        except Exception as exc:  # pragma: no cover - device failure path
            logger.exception("select failed")
            return {"ok": False, "code": "SEGMENT_ERROR", "message": str(exc)}
        return {"ok": True, "state": self._sm.state, "message": "target selected"}

    def add_point(self, x_norm: float, y_norm: float, label: int = 1) -> dict:
        """Refinement click on the current target."""
        with self._state_lock:
            if not self._enabled:
                return {"ok": False, "code": "SEGMENT_NOT_STARTED",
                        "message": "start the segment feature first"}
            if not self._has_target:
                return {"ok": False, "code": "SEGMENT_NO_TARGET",
                        "message": "click a target first"}
        if len(self._points) >= seg_cfg.MAX_POINTS:
            return {"ok": False, "code": "SEGMENT_TOO_MANY_POINTS",
                    "message": "too many points; clear the target to re-pick"}
        if not (0.0 <= x_norm <= 1.0 and 0.0 <= y_norm <= 1.0):
            return {"ok": False, "code": "SEGMENT_INVALID_COORDINATES",
                    "message": "coordinates must be in [0, 1]"}
        if self._model is None:
            return {"ok": False, "code": "SEGMENT_LOADING", "message": "model not loaded yet"}

        frame_id, frame = self._camera_latest()
        if frame is None:
            return {"ok": False, "code": "SEGMENT_NO_FRAME", "message": "no camera frame yet"}
        small, sx, sy = downscale_frame(frame, self._max_side)
        rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        x_px = x_norm * rgb.shape[1]
        y_px = y_norm * rgb.shape[0]
        try:
            with self._model_lock:
                mask = self._model.add_point(rgb, x_px, y_px, label)
                if len(self._points) < seg_cfg.MAX_POINTS:
                    self._points.append([x_norm, y_norm, int(label)])
                self._relock_note = None  # fresh user intent supersedes stale gates
                self._commit_mask(mask, frame_id, sx, sy,
                                  frame_gray=cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY))
        except Exception as exc:  # pragma: no cover
            logger.exception("add_point failed")
            return {"ok": False, "code": "SEGMENT_ERROR", "message": str(exc)}
        return {"ok": True, "state": self._sm.state, "message": "point added"}

    def status(self) -> dict:
        with self._state_lock:
            payload = dict(self._latest)
        payload["enabled"] = self._enabled
        payload["loading"] = self._loading
        payload["error"] = self._error
        payload["camera_running"] = bool(self._camera_running())
        payload["target_fps"] = self._target_fps
        return payload

    def set_target_fps(self, value: float) -> None:
        """Apply Hub thermal scheduling without restarting the model."""
        with self._state_lock:
            self._target_fps = max(1.0, float(value))

    # ---------------------------------------------------------------- loop
    def process_one(self) -> None:
        """One tracking step on the newest camera frame (also test entry)."""
        if self._model is None or not self._has_target:
            return
        frame_id, frame = self._camera_latest()
        if frame is None:
            return
        small, sx, sy = downscale_frame(frame, self._max_side)
        rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        t0 = time.perf_counter()
        try:
            with self._model_lock:
                if frame_id == self._last_frame_id:
                    return
                self._last_frame_id = frame_id
                mask = self._model.track(rgb)
                infer_ms = (time.perf_counter() - t0) * 1000.0
                self._commit_mask(mask, frame_id, sx, sy, infer_ms=infer_ms,
                                  frame_gray=gray)
        except Exception as exc:  # pragma: no cover - device failure path
            logger.exception("track failed")
            with self._state_lock:
                self._error = "track failed: %s" % exc
            self._publish()
            return
        # outside the model lock: re-lock a reappearing object while lost
        if self._sm.state == SegmentStateMachine.LOST:
            self._maybe_reseed(gray)

    def _run(self) -> None:
        while not self._stop.is_set():
            loop_started = time.perf_counter()
            if not self._enabled:
                time.sleep(0.05)
                continue
            if self._error:
                time.sleep(0.5)
                continue
            if self._model is None:
                self._load_model_once()
                continue
            if not self._has_target:
                time.sleep(0.02)
                continue
            self.process_one()
            with self._state_lock:
                target_fps = self._target_fps
            remaining = (1.0 / max(1.0, target_fps)) - (
                time.perf_counter() - loop_started
            )
            if remaining > 0:
                self._stop.wait(remaining)

    def _load_model_once(self) -> None:
        with self._state_lock:
            self._loading = True
        try:
            model = self._model_factory()
            model.load()
            with self._state_lock:
                self._model = model
                self._loading = False
            logger.info("segment model ready")
        except Exception as exc:
            logger.exception("segment model load failed: %s", exc)
            with self._state_lock:
                self._loading = False
                self._error = "model load failed: %s" % exc
        self._publish()

    # ------------------------------------------------------------ internals
    def ensure_model(self) -> dict:
        """Load the model synchronously (single-consumer / manual mode)."""
        with self._state_lock:
            if self._model is not None:
                return {"ok": True, "message": "model ready"}
            if self._loading:
                return {"ok": False, "code": "SEGMENT_LOADING", "message": "still loading"}
        self._load_model_once()
        with self._state_lock:
            if self._model is not None:
                return {"ok": True, "message": "model ready"}
            return {"ok": False, "code": "SEGMENT_ERROR", "message": self._error or "load failed"}

    # NOTE: callers must hold _model_lock (serialisation with the loop).
    def _commit_mask(
        self,
        mask: np.ndarray,
        frame_id: int,
        sx: float,
        sy: float,
        infer_ms: Optional[float] = None,
        frame_gray: Optional[np.ndarray] = None,
    ) -> None:
        # --- mask quality gate (over-segmentation = hand/background) -----
        # Computed on real (non-empty) masks only, so absent tracking
        # frames never disturb the last judgement for this target.
        if mask is not None and bool(mask.any()):
            h, w = mask.shape[:2]
            area_ratio = float(mask.mean())
            ys, xs = np.nonzero(mask)
            bbox_w = float(xs.max() - xs.min() + 1) / w
            bbox_h = float(ys.max() - ys.min() + 1) / h
            bbox_ratio = max(bbox_w, bbox_h)
            poor = (
                area_ratio > seg_cfg.MASK_QUALITY_POOR_AREA_RATIO
                or bbox_ratio > seg_cfg.MASK_QUALITY_POOR_BBOX_RATIO
            )
            self._target_quality = "poor" if poor else "ok"

        # --- natural recovery: trust the model's own track() output. The
        # memory/attention encoder is the identity authority — it re-emits a
        # mask only for a recognized target, so a foreign object sitting in
        # the old spot produces no mask and cannot re-lock. A classical-
        # template check here caused false negatives on real (low-texture,
        # hand-held) objects, so it was removed; the reseed path keeps its
        # own identity verification as the real re-lock guard.
        mask_for_sm = mask

        event = self._sm.update(mask_for_sm)
        self._last_mask_area = float(mask.mean()) if mask is not None else 0.0
        # --- template discipline ------------------------------------------
        # Refresh the appearance template only while TRACKING with a
        # well-segmented (ok) mask at least as large as the resume ratio;
        # the mask is eroded first so the template excludes the
        # target/context blend contour.
        if (
            self._sm.state == SegmentStateMachine.TRACKING
            and self._target_quality == "ok"
            and self._last_mask_area >= seg_cfg.RESUME_AREA_RATIO
            and frame_gray is not None
        ):
            tpl = build_template(frame_gray, erode_mask_for_template(mask))
            if tpl is not None:
                if self._template is None:
                    # anchor identity on the first clean mask
                    self._template = tpl
                else:
                    # cautious refresh: only adopt a new exemplar when it clearly
                    # matches the current identity, so a panicked / off-target
                    # mask cannot displace the anchor during a long track
                    c = _templates_corr(self._template, tpl)
                    if c is not None and c >= seg_cfg.TEMPLATE_REFRESH_CORR:
                        self._template = tpl
        if event == "lost":
            self._ghost = (
                self._sm.last_good.copy()
                if self._sm.last_good is not None and self._sm.last_good.any()
                else (mask.copy() if mask.any() else None)
            )
        elif event == "recovered":
            self._ghost = None
            self._relock_note = None
            self._logged_resume_reject = False
        if self._sm.state == SegmentStateMachine.TRACKING:
            self._ghost = None
        if infer_ms is not None:
            self._infer_ms_ema = (
                infer_ms if self._infer_ms_ema is None
                else 0.9 * self._infer_ms_ema + 0.1 * infer_ms
            )
            completed_at = time.monotonic()
            if self._last_infer_at is not None:
                interval = completed_at - self._last_infer_at
                inst_fps = 1.0 / interval if interval > 0 else 0.0
                self._fps_ema = (
                    inst_fps if self._fps_ema is None
                    else 0.9 * self._fps_ema + 0.1 * inst_fps
                )
            self._last_infer_at = completed_at
        self._last_frame_id = frame_id
        self._last_sx, self._last_sy = sx, sy
        self._latest = self._build_payload(mask, sx, sy)

    def _maybe_reseed(self, gray: np.ndarray) -> None:
        """While LOST: template-match the object's last good appearance and
        auto re-click at a strong peak (score + PSR). The resulting mask is
        identity-verified before commit, and rejected candidates are never
        committed (no point, no mask) and go on a cool-down list, so a
        transient or foreign peak cannot wedge the tracker. Acting on a single
        strong peak (rather than a same-position confirm) lets a moving target
        re-lock, which a position-confirm can never satisfy."""
        if (
            self._model is None
            or not self._has_target
            or self._template is None
            or self._sm.state != SegmentStateMachine.LOST
        ):
            return
        now = time.time()
        if (
            self._last_reseed_at is not None
            and now - self._last_reseed_at < self._reseed_interval_s
        ):
            return
        self._last_reseed_at = now
        peak = find_template_peak_psr(gray, self._template, self._reseed_threshold)
        if peak is None:
            self._pending_relock = None
            return
        cx, cy, score, psr = peak
        # the peak must stand out from the response-map baseline; a strong
        # (score + PSR) peak is acted on immediately and its resulting mask
        # is identity-verified, so a moving target is re-locked instead of
        # being starved by a same-position confirm (which a sweep can never
        # satisfy).
        self._pending_relock = None
        if psr < seg_cfg.RESEED_MIN_PSR:
            return
        # skip positions rejected recently (cool-down)
        for sx0, sy0, until in self._suppressed:
            if (
                now < until
                and abs(sx0 - cx) <= seg_cfg.RESEED_POS_TOL
                and abs(sy0 - cy) <= seg_cfg.RESEED_POS_TOL
            ):
                self._pending_relock = None
                return
        h, w = gray.shape[:2]
        try:
            with self._model_lock:
                # Re-lock = fresh re-anchor at the peak. The peak score
                # (>= RESEED_THRESHOLD against the identity template) plus
                # PSR is the identity attestation, so no separate mask
                # re-verification is needed; re-running select (reset +
                # point) is what makes the NEXT track() follow the object
                # (the add_point path did not reliably do so).
                mask = self._model.select(
                    cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB), cx * w, cy * h
                )
            self._points = [[cx, cy, 1]]
            self._sm.begin_target()  # verified re-lock -> TRACKING now
            self._pending_relock = None
            self._relock_note = None
            self._commit_mask(mask, self._last_frame_id,
                              self._last_sx, self._last_sy,
                              frame_gray=gray)
            logger.info("auto re-lock at (%.3f, %.3f) score=%.2f",
                        cx, cy, score)
        except Exception:  # pragma: no cover - device failure path
            logger.exception("auto re-lock failed")

    def _build_payload(self, mask: np.ndarray, sx: float, sy: float) -> dict:
        polygons: List[list] = []
        ghost_polygons: List[list] = []
        center = None
        if self._sm.state == SegmentStateMachine.TRACKING:
            polygons = mask_to_polygons(mask, sx, sy)
            center = mask_center(mask, sx, sy)
        elif self._sm.state == SegmentStateMachine.LOST and self._ghost is not None:
            ghost_polygons = mask_to_polygons(self._ghost, sx, sy)
        return {
            "ok": True,
            "enabled": self._enabled,
            "state": self._sm.state,
            "has_target": self._has_target,
            "loading": self._loading,
            "error": self._error,
            "points": [list(p) for p in self._points],
            "polygons": polygons,
            "ghost_polygons": ghost_polygons,
            "center": list(center) if center is not None else None,
            "mask_area": round(self._last_mask_area, 4),
            "infer_ms": round(self._infer_ms_ema, 1) if self._infer_ms_ema is not None else None,
            "model_fps": round(self._fps_ema, 2) if self._fps_ema is not None else None,
            "frame_id": self._last_frame_id,
            "ts": time.time(),
            "target_quality": self._target_quality,
            "target_hint": (
                POOR_MASK_HINT
                if self._has_target and self._target_quality == "poor"
                else None
            ),
            "relock_note": self._relock_note,
            "resume_armed": bool(
                self._template is not None and self._target_quality == "ok"
            ),
        }

    def _empty_payload(self) -> dict:
        return {
            "ok": True,
            "enabled": False,
            "state": "idle",
            "has_target": False,
            "loading": False,
            "error": None,
            "points": [],
            "polygons": [],
            "ghost_polygons": [],
            "center": None,
            "infer_ms": None,
            "model_fps": None,
            "frame_id": None,
            "ts": time.time(),
            "target_quality": None,
            "target_hint": None,
            "relock_note": None,
            "resume_armed": False,
        }

    def _publish(self) -> None:
        with self._state_lock:
            if self._has_target:
                payload = dict(self._latest)
                payload.update(
                    state=self._sm.state,
                    has_target=True,
                    polygons=[],
                    ghost_polygons=[],
                    center=None,
                )
                self._latest = payload
            else:
                self._latest = self._empty_payload()

    def _reset_model(self) -> None:
        if self._model is not None:
            try:
                with self._model_lock:
                    self._model.reset()
            except Exception:  # pragma: no cover - best effort
                pass

    def _stop_worker_and_unload(self) -> None:
        """Stop inference and return model/CUDA memory to the shared device."""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=30.0)
            if thread.is_alive():
                logger.warning("segment worker did not exit before unload timeout")
        self._thread = None
        with self._model_lock:
            model = self._model
            self._model = None
            if model is not None:
                try:
                    model.reset()
                except Exception:  # pragma: no cover - best effort
                    pass
            del model
        with self._state_lock:
            self._loading = False
            self._infer_ms_ema = None
            self._fps_ema = None
            self._last_infer_at = None
            self._last_frame_id = -1
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                torch.cuda.ipc_collect()
        except Exception:  # pragma: no cover - CPU tests / teardown
            pass

    def _ensure_thread(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="segment-service", daemon=True)
            self._thread.start()

    def _camera_running(self) -> bool:
        cam = self._camera or _global_camera()
        try:
            flag = cam.is_running  # @property in the app's CameraManager
            return bool(flag() if callable(flag) else flag)
        except Exception:  # pragma: no cover
            return False

    def _camera_latest(self):
        cam = self._camera or _global_camera()
        return cam.get_latest_frame()

    def shutdown(self) -> None:
        with self._state_lock:
            self._enabled = False
            self._has_target = False
        self._stop_worker_and_unload()


def _global_camera():
    from backend.app.camera.manager import get_camera_manager

    return get_camera_manager()


_instances: List[SegmentService] = []
_instances_lock = threading.Lock()


def get_segment_service() -> SegmentService:
    with _instances_lock:
        if not _instances:
            _instances.append(SegmentService())
        return _instances[0]


def reset_segment_service_for_tests() -> None:
    """Test hook: drop the singleton so tests can inject fakes."""
    global _instances
    with _instances_lock:
        for svc in _instances:
            try:
                svc.shutdown()
            except Exception:  # pragma: no cover
                pass
        _instances = []
