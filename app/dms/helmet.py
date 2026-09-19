"""Helmet-wearing heuristic (offline) — per-person worn / not_worn / unknown.

纯离线主路径: 已有人体检测（ultralytics YOLOv8n person / TRT 引擎，见
`make_person_detector`）→ 头部 ROI → HSV 安全帽色带占比 + 肤色占比 + 暗区占比
→ 三道门控不满足一律 `unknown`（绝不猜测）→ 按 track_id 多数票平滑。

诚实标注: 这是**颜色启发式**，不是训练过的头盔分类器; 输出只有三值，没有
百分比式结论; 已知失败模式见 docs/dms_helmet_demo.md。
"""
from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
from typing import Any, Callable, Deque, Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

VERDICT_WORN = "worn"
VERDICT_NOT_WORN = "not_worn"
VERDICT_UNKNOWN = "unknown"
VERDICTS = (VERDICT_WORN, VERDICT_NOT_WORN, VERDICT_UNKNOWN)

REASON_PERSON_SMALL = "person_too_small"
REASON_ROI_CLIPPED = "head_roi_clipped"
REASON_HEAD_SMALL = "head_too_small"
REASON_WARMING_UP = "warming_up"
REASON_COLOR = "color_evidence"
REASON_SKIN = "skin_evidence"
REASON_NO_COLOR = "no_helmet_color_evidence"
REASON_INSUFFICIENT = "insufficient_evidence"
REASON_SMOOTHED = "smoothed_majority"

DARK_V = 45          # 暗区定义（契约 §3.2.3，固定值）

Box = Tuple[int, int, int, int]


@dataclass(frozen=True)
class HelmetVerdict:
    track_id: int
    bbox: Box
    head_roi: Box
    verdict: str
    reason: str
    helmet_color_ratio: float
    skin_ratio: float
    dark_ratio: float
    conf: str = "low"
    age_frames: int = 0


def compute_head_roi(bbox: Box, head_roi_frac: float, head_roi_w_frac: float,
                     head_roi_min_px: int) -> Box:
    x, y, w, h = [int(v) for v in bbox]
    head_h = int(round(min(max(h * float(head_roi_frac),
                                float(head_roi_min_px)), float(h))))
    head_w = int(round(w * float(head_roi_w_frac)))
    head_x = int(round(x + (w - head_w) / 2.0))
    return (head_x, int(y), max(1, head_w), max(1, head_h))


def _heights(roi: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    return hsv[..., 0].astype(np.int16), hsv[..., 1], hsv[..., 2]


def _band_mask(hue: np.ndarray, sat: np.ndarray, val: np.ndarray,
               band: dict) -> np.ndarray:
    spec = band.get("h", [0, 179])
    ranges: List[Sequence[int]]
    if spec and isinstance(spec[0], (list, tuple)):
        ranges = [tuple(int(v) for v in r) for r in spec]
    else:
        ranges = [tuple(int(v) for v in spec)]
    mask = np.zeros(hue.shape, dtype=bool)
    for low, high in ranges:
        mask |= (hue >= low) & (hue <= high)
    mask &= val >= int(band.get("v_min", 0))
    if "s_max" in band:
        mask &= sat <= int(band["s_max"])
    else:
        mask &= sat >= int(band.get("s_min", 0))
    return mask


def head_evidence(roi_bgr: Optional[np.ndarray], colors: dict,
                  skin: dict) -> Tuple[float, float, float]:
    """(helmet_color_ratio, skin_ratio, dark_ratio) ；空 ROI -> 全 0。"""
    if roi_bgr is None or roi_bgr.size == 0:
        return 0.0, 0.0, 0.0
    hue, sat, val = _heights(roi_bgr)
    color_mask = np.zeros(hue.shape, dtype=bool)
    for name, band in (colors or {}).items():
        if not isinstance(band, dict) or not band.get("enabled", True):
            continue
        try:
            color_mask |= _band_mask(hue, sat, val, band)
        except Exception:  # noqa: BLE001 - a bad band must not kill the loop
            continue
    ycc = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2YCrCb)
    cr, cb = ycc[..., 1], ycc[..., 2]
    cr_lo, cr_hi = [int(v) for v in (skin or {}).get("cr", [133, 173])]
    cb_lo, cb_hi = [int(v) for v in (skin or {}).get("cb", [77, 127])]
    skin_mask = ((cr >= cr_lo) & (cr <= cr_hi) & (cb >= cb_lo) & (cb <= cb_hi))
    dark_mask = val < DARK_V
    total = float(hue.size)
    return (float(np.count_nonzero(color_mask)) / total,
            float(np.count_nonzero(skin_mask)) / total,
            float(np.count_nonzero(dark_mask)) / total)


def classify_head_roi(roi_bgr: Optional[np.ndarray], cfg: Any
                      ) -> Tuple[str, str, float, float, float, str]:
    """契约 §3.2.3 的冻结判定（按序短路），只返回三值。

    返回 (verdict, reason, helmet_color_ratio, skin_ratio, dark_ratio, conf)。

    一处诚实的加固：ROI 为空（完全没有像素证据）时直接返回 unknown/
    insufficient_evidence。冻结规则里的"无彩色且不暗 -> not_worn"默认是**有有效
    ROI** 才成立，零证据下不该声称"未佩戴"。
    """
    if roi_bgr is None or roi_bgr.size == 0:
        return (VERDICT_UNKNOWN, REASON_INSUFFICIENT, 0.0, 0.0, 0.0, "low")
    colors = cfg.get("helmet.colors", {}) or {}
    skin = cfg.get("helmet.skin", {}) or {}
    color_ratio, skin_ratio, dark_ratio = head_evidence(roi_bgr, colors, skin)
    worn_color = float(cfg.get("helmet.rule.worn_color_ratio", 0.35))
    worn_skin_max = float(cfg.get("helmet.rule.worn_skin_max", 0.25))
    not_worn_skin = float(cfg.get("helmet.rule.not_worn_skin_ratio", 0.45))
    no_color = float(cfg.get("helmet.rule.no_color_ratio", 0.05))
    dark_max = float(cfg.get("helmet.rule.dark_max", 0.60))

    if color_ratio >= worn_color and skin_ratio <= worn_skin_max:
        verdict, reason = VERDICT_WORN, REASON_COLOR
    elif (skin_ratio >= not_worn_skin
          or (color_ratio <= no_color and dark_ratio <= dark_max)):
        verdict, reason = VERDICT_NOT_WORN, (
            REASON_SKIN if skin_ratio >= not_worn_skin else REASON_NO_COLOR)
    else:
        verdict, reason = VERDICT_UNKNOWN, REASON_INSUFFICIENT

    distances = (abs(color_ratio - worn_color), abs(skin_ratio - worn_skin_max),
                 abs(skin_ratio - not_worn_skin), abs(color_ratio - no_color))
    conf = "mid" if max(distances) > 0.10 else "low"
    return verdict, reason, color_ratio, skin_ratio, dark_ratio, conf


class HelmetEngine:
    """`update(frame_bgr, detections) -> list[HelmetVerdict]`（每人一条）。"""

    def __init__(self, cfg: Any, detector: Any = None,
                 on_event: Optional[Callable[[str, dict], None]] = None) -> None:
        self.cfg = cfg
        self.detector = detector
        self.on_event = on_event
        self.head_roi_frac = float(cfg.get("helmet.head_roi_frac", 0.30))
        self.head_roi_w_frac = float(cfg.get("helmet.head_roi_w_frac", 0.70))
        self.head_roi_min_px = int(cfg.get("helmet.head_roi_min_px", 12))
        self.roi_margin_px = int(cfg.get("helmet.roi_margin_px", 4))
        self.min_person_h_px = int(cfg.get("helmet.min_person_h_px", 80))
        self.min_head_area_px = int(cfg.get("helmet.min_head_area_px", 400))
        self.k_verdict = int(cfg.get("helmet.smooth.k_verdict_frames", 5))
        self.reset()

    def reset(self) -> None:
        """清空平滑窗口与 track 年龄（重新打开开关后必须从 warming_up 起）。"""
        self._history: Dict[int, Deque[str]] = {}
        self._age: Dict[int, int] = {}
        self._last_seen: Dict[int, int] = {}
        self._last_verdict: Dict[int, str] = {}
        self._frame = 0

    def update(self, frame_bgr: np.ndarray,
               detections: Optional[Sequence[Any]]) -> List[HelmetVerdict]:
        self._frame += 1
        height, width = (frame_bgr.shape[:2] if frame_bgr is not None
                         else (0, 0))
        results: List[HelmetVerdict] = []
        for det in detections or ():
            bbox = tuple(int(v) for v in getattr(det, "bbox", (0, 0, 0, 0)))
            track_id = int(getattr(det, "track_id", -1) or -1)
            results.append(self._one(frame_bgr, bbox, track_id, width, height))
        self._purge()
        return results

    # -- internals ----------------------------------------------------------
    def _one(self, frame_bgr: np.ndarray, bbox: Box, track_id: int,
             width: int, height: int) -> HelmetVerdict:
        head_roi = compute_head_roi(bbox, self.head_roi_frac,
                                    self.head_roi_w_frac, self.head_roi_min_px)
        roi_img, _ = _clip_roi(frame_bgr, head_roi)
        raw_verdict, raw_reason, color_r, skin_r, dark_r, conf = \
            classify_head_roi(roi_img, self.cfg)

        gate: Optional[str] = None
        if bbox[3] < self.min_person_h_px:
            gate = REASON_PERSON_SMALL
        elif (head_roi[0] < self.roi_margin_px
              or head_roi[1] < self.roi_margin_px
              or head_roi[0] + head_roi[2] > width - self.roi_margin_px
              or head_roi[1] + head_roi[3] > height - self.roi_margin_px):
            gate = REASON_ROI_CLIPPED
        elif head_roi[2] * head_roi[3] < self.min_head_area_px:
            gate = REASON_HEAD_SMALL

        history = self._history.setdefault(track_id, deque(maxlen=self.k_verdict))
        age = self._age.get(track_id, 0) + 1
        self._age[track_id] = age
        self._last_seen[track_id] = self._frame
        history.append(raw_verdict)

        if gate is not None:
            verdict, reason = VERDICT_UNKNOWN, gate
        elif age < self.k_verdict:
            verdict, reason = VERDICT_UNKNOWN, REASON_WARMING_UP
        else:
            counts = Counter(history)
            top, top_n = counts.most_common(1)[0]
            if top_n * 2 > len(history):
                verdict = top
                reason = raw_reason if top == raw_verdict else REASON_SMOOTHED
            else:
                verdict, reason = VERDICT_UNKNOWN, REASON_INSUFFICIENT
            if verdict == VERDICT_UNKNOWN:
                conf = "low"

        previous = self._last_verdict.get(track_id)
        if previous != verdict:
            self._last_verdict[track_id] = verdict
            self._emit(track_id, verdict, reason, bbox, head_roi, color_r,
                       skin_r, dark_r, age)
        return HelmetVerdict(
            track_id=track_id, bbox=bbox, head_roi=head_roi, verdict=verdict,
            reason=reason, helmet_color_ratio=round(color_r, 4),
            skin_ratio=round(skin_r, 4), dark_ratio=round(dark_r, 4),
            conf=conf, age_frames=age)

    def _purge(self, max_missing: int = 90) -> None:
        for track_id in list(self._last_seen):
            if self._frame - self._last_seen[track_id] > max_missing:
                for store in (self._history, self._age, self._last_seen,
                              self._last_verdict):
                    store.pop(track_id, None)

    def _emit(self, track_id: int, verdict: str, reason: str, bbox: Box,
              head_roi: Box, color_r: float, skin_r: float, dark_r: float,
              age: int) -> None:
        if self.on_event is None:
            return
        try:
            self.on_event("helmet_verdict", {
                "track_id": track_id, "verdict": verdict, "reason": reason,
                "bbox": list(bbox), "head_roi": list(head_roi),
                "helmet_color_ratio": round(color_r, 4),
                "skin_ratio": round(skin_r, 4), "dark_ratio": round(dark_r, 4),
                "age_frames": age})
        except Exception as exc:  # noqa: BLE001 - logging never kills loop
            print(f"[dms] helmet event log failed: {exc}")


def _clip_roi(frame_bgr: Optional[np.ndarray], roi: Box
              ) -> Tuple[Optional[np.ndarray], Box]:
    if frame_bgr is None or frame_bgr.size == 0:
        return None, (0, 0, 0, 0)
    height, width = frame_bgr.shape[:2]
    x, y, w, h = roi
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(width, x + w), min(height, y + h)
    if x1 <= x0 or y1 <= y0:
        return None, (0, 0, 0, 0)
    return frame_bgr[y0:y1, x0:x1], (x0, y0, x1 - x0, y1 - y0)


def make_person_detector(cfg: Any, debug: bool = False) -> Optional[Any]:
    """人体检测器（头盔路的唯一入口）。

    按序尝试 `helmet.person_model` → `checkpoints/yolov8n.pt`；前者失败时打印
    单行回退提示（契约 §3.2.1）。两者都不可用时返回 None（调用方把 helmet 路
    降级为 DISABLED，不崩溃、不静默）。
    """
    import os
    from app.warning.person_detection import UltralyticsPersonDetector

    candidates = [str(cfg.get("helmet.person_model",
                              "models/tensorrt/yolov8n_person_fp16.engine")),
                  "checkpoints/yolov8n.pt"]
    score_thresh = float(cfg.get("helmet.person_confidence", 0.45))
    iou = float(cfg.get("helmet.person_iou", 0.50))
    imgsz = int(cfg.get("helmet.person_imgsz", 640))
    last_error = "no candidate model file"
    for index, path in enumerate(candidates):
        if path in candidates[:index]:
            continue
        if not os.path.isfile(path):
            last_error = f"{path} does not exist"
            continue
        try:
            detector = UltralyticsPersonDetector(
                path, confidence=score_thresh, iou=iou, imgsz=imgsz)
        except Exception as exc:  # noqa: BLE001 - fall back visibly
            last_error = f"{type(exc).__name__}: {exc}"
            if index == 0 and len(candidates) > 1:
                print("[dms] helmet: falling back to checkpoints/yolov8n.pt "
                      f"({last_error})")
            continue
        if index > 0:
            print(f"[dms] helmet: falling back to {path} ({last_error})")
        return detector
    print(f"[dms] ERROR: helmet: no usable person model ({last_error}) — "
          "helmet detection disabled")
    return None