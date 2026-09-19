"""Fatigue signal extraction + state machines (demo level).

The live DMS uses ``LandmarkFatigueEngine``: MediaPipe Face Landmarker supplies
facial blendshapes and pose, while this module applies fixed, explainable timing
rules.  The retained Haar/ROI engine is a legacy test/demo path, not the live
backend.  No visible face is always ``UNKNOWN``; it is never treated as normal.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Deque, Optional, Tuple

import numpy as np
import cv2

from app.dms.face import FaceTrack, crop_norm, dark_ratio

STATE_DISABLED = "DISABLED"
STATE_UNKNOWN = "UNKNOWN"
STATE_NORMAL = "NORMAL"
STATE_WARN = "DROWSY_WARN"
STATE_ALARM = "DROWSY_ALARM"

STATES = (STATE_DISABLED, STATE_UNKNOWN, STATE_NORMAL, STATE_WARN, STATE_ALARM)

Box = Tuple[int, int, int, int]


@dataclass(frozen=True)
class FatigueSignals:
    face_present: bool
    face_box: Optional[Box]
    track_id: int
    eye_dark_ratio: Optional[float]
    mouth_open_ratio: Optional[float]
    eye_cascade_hits: int = 0
    smile_cascade_hits: int = 0
    eye_closure: Optional[float] = None
    perclos: Optional[float] = None
    yawn_count_60s: int = 0
    yaw_deg: Optional[float] = None
    pitch_deg: Optional[float] = None
    reason: str = ""
    mouth_open: bool = False
    eyes_closed: bool = False
    mouth_open_duration_s: float = 0.0
    eye_closed_duration_s: float = 0.0


@dataclass(frozen=True)
class FatigueResult:
    state: str
    score: float
    signals: Optional[FatigueSignals]


class FatigueEngine:
    """One entry point per frame: `update(frame_bgr, gray) -> FatigueResult`."""

    def __init__(self, cfg: Any, face_detector: Any,
                 on_event: Optional[Callable[[str, dict], None]] = None,
                 clock: Optional[Callable[[], float]] = None) -> None:
        self.cfg = cfg
        self.detector = face_detector
        self.on_event = on_event
        self._clock = clock or time.time
        self.enabled = True

        self.eye_x = tuple(cfg.get("fatigue.roi.eye_x", [0.15, 0.85]))
        self.eye_y = tuple(cfg.get("fatigue.roi.eye_y", [0.20, 0.52]))
        self.mouth_x = tuple(cfg.get("fatigue.roi.mouth_x", [0.28, 0.72]))
        self.mouth_y = tuple(cfg.get("fatigue.roi.mouth_y", [0.62, 0.92]))
        self.eye_dark_thresh = float(cfg.get("fatigue.signal.eye_dark_thresh", 60))
        self.mouth_dark_thresh = float(
            cfg.get("fatigue.signal.mouth_dark_thresh", 55))
        self.eye_dark_enter = float(cfg.get("fatigue.signal.eye_dark_enter", 0.55))
        self.mouth_open_enter = float(
            cfg.get("fatigue.signal.mouth_open_enter", 0.45))
        self.crosscheck_enabled = bool(
            cfg.get("fatigue.signal.cascade_crosscheck", False))
        self.min_face_h_px = int(cfg.get("fatigue.face.min_face_h_px", 48))
        self.face_lost_warn_s = float(
            cfg.get("fatigue.face.face_lost_warn_s", 2.0))
        self.window_frames = int(cfg.get("fatigue.state_machine.window_frames", 90))
        self.ema_alpha = float(cfg.get("fatigue.state_machine.ema_alpha", 0.25))
        self.w_eye = float(cfg.get("fatigue.state_machine.w_eye", 1.0))
        self.w_mouth = float(cfg.get("fatigue.state_machine.w_mouth", 1.0))
        self.warn_enter = float(cfg.get("fatigue.state_machine.warn_enter", 0.60))
        self.alarm_enter = float(cfg.get("fatigue.state_machine.alarm_enter", 0.80))
        self.exit_enter = float(cfg.get("fatigue.state_machine.exit_enter", 0.35))
        self.warn_confirm = int(
            cfg.get("fatigue.state_machine.warn_confirm_frames", 15))
        self.alarm_confirm = int(
            cfg.get("fatigue.state_machine.alarm_confirm_frames", 15))
        self.exit_confirm = int(
            cfg.get("fatigue.state_machine.exit_confirm_frames", 45))
        self.warn_hold_s = float(cfg.get("fatigue.state_machine.warn_hold_s", 5.0))
        self.reset()

    # -- lifecycle ----------------------------------------------------------
    def reset(self) -> None:
        """Clear the rolling window / EMA / tracking.

        enabled=True  -> 状态回到 UNKNOWN（重新累积，不沿用旧窗口）
        enabled=False -> 状态 DISABLED（契约 §5.4：关闭即 DISABLED）
        """
        self.window: Deque[float] = deque(maxlen=self.window_frames)
        self.ema = 0.0
        self.warn_run = 0
        self.alarm_run = 0
        self.exit_run = 0
        self.warn_since: Optional[float] = None
        self.face_lost_since: Optional[float] = None
        self.face_lost_logged = False
        try:
            self.detector.reset()
        except AttributeError:
            pass
        self._state = STATE_UNKNOWN if self.enabled else STATE_DISABLED

    def set_enabled(self, flag: bool) -> None:
        self.enabled = bool(flag)
        self.reset()

    @property
    def state(self) -> str:
        return self._state

    # -- per-frame entry point ---------------------------------------------
    def update(self, frame_bgr: np.ndarray, gray: np.ndarray) -> FatigueResult:
        if not self.enabled:
            self._state = STATE_DISABLED
            return FatigueResult(STATE_DISABLED, 0.0, None)

        now = self._clock()
        tracks = self.detector.update(gray)
        main: Optional[FaceTrack] = self.detector.main(tracks)
        qualified = (main is not None
                     and int(main.box[3]) >= self.min_face_h_px)

        if not qualified:
            signals = FatigueSignals(
                face_present=main is not None,
                face_box=main.box if main is not None else None,
                track_id=main.track_id if main is not None else -1,
                eye_dark_ratio=None, mouth_open_ratio=None)
            self._note_face_lost(now)
            self._set_state(STATE_UNKNOWN, now, signals)
            return FatigueResult(STATE_UNKNOWN, self.ema, signals)

        self.face_lost_since = None
        self.face_lost_logged = False
        signals = self._signals(gray, main)
        score_now = (self.w_eye if signals.eye_dark_ratio is not None
                     and signals.eye_dark_ratio >= self.eye_dark_enter else 0.0)
        score_now += (self.w_mouth if signals.mouth_open_ratio is not None
                      and signals.mouth_open_ratio >= self.mouth_open_enter
                      else 0.0)
        self.ema = self.ema_alpha * score_now + (1.0 - self.ema_alpha) * self.ema
        self.window.append(score_now)

        if self.ema >= self.warn_enter:
            self.warn_run += 1
        else:
            self.warn_run = 0
        if self.ema >= self.alarm_enter:
            self.alarm_run += 1
        else:
            self.alarm_run = 0
        if self.ema <= self.exit_enter:
            self.exit_run += 1
        else:
            self.exit_run = 0

        self._set_state(self._next_state(now), now, signals,
                        reason="score_ema=%.3f" % self.ema)
        return FatigueResult(self._state, self.ema, signals)

    # -- internals ----------------------------------------------------------
    def _signals(self, gray: np.ndarray, track: FaceTrack) -> FatigueSignals:
        eye_roi = crop_norm(gray, track.box, self.eye_x, self.eye_y)
        mouth_roi = crop_norm(gray, track.box, self.mouth_x, self.mouth_y)
        eye_hits = smile_hits = 0
        if self.crosscheck_enabled:
            try:
                eye_hits, smile_hits = self.detector.cascade_hits(gray, track.box)
            except AttributeError:
                eye_hits = smile_hits = 0
        return FatigueSignals(
            face_present=True,
            face_box=track.box,
            track_id=track.track_id,
            eye_dark_ratio=dark_ratio(eye_roi[0] if eye_roi else None,
                                      self.eye_dark_thresh),
            mouth_open_ratio=dark_ratio(mouth_roi[0] if mouth_roi else None,
                                        self.mouth_dark_thresh),
            eye_cascade_hits=int(eye_hits),
            smile_cascade_hits=int(smile_hits))

    def _note_face_lost(self, now: float) -> None:
        """主脸丢失 >= face_lost_warn_s -> 写一条可观测的诚实事件。"""
        if self.face_lost_since is None:
            self.face_lost_since = now
            return
        if (not self.face_lost_logged
                and now - self.face_lost_since >= self.face_lost_warn_s):
            self.face_lost_logged = True
            self._emit("fatigue_face_lost",
                       {"lost_s": round(now - self.face_lost_since, 2),
                        "state": STATE_UNKNOWN})

    def _next_state(self, now: float) -> str:
        state = self._state
        if state == STATE_NORMAL:
            if self.warn_run >= self.warn_confirm:
                return STATE_WARN
        elif state == STATE_WARN:
            if self.alarm_run >= self.alarm_confirm:
                return STATE_ALARM
            if (self.warn_since is not None
                    and now - self.warn_since >= self.warn_hold_s):
                return STATE_ALARM
            if self.exit_run >= self.exit_confirm:
                return STATE_NORMAL
        elif state == STATE_ALARM:
            if self.exit_run >= self.exit_confirm:
                return STATE_NORMAL
        elif state == STATE_UNKNOWN:
            # 契约 §3.1.3: UNKNOWN -> NORMAL（重新检测到合格主脸且已回落）
            if self.ema <= self.exit_enter:
                return STATE_NORMAL
        return state

    def _set_state(self, new_state: str, now: float, signals: FatigueSignals,
                   reason: str = "no_qualified_face") -> None:
        old = self._state
        if new_state == old:
            return
        self._state = new_state
        if new_state == STATE_WARN:
            self.warn_since = now
        elif new_state in (STATE_NORMAL, STATE_ALARM):
            self.warn_since = None
        self._emit("fatigue_state", {
            "from": old, "to": new_state,
            "score": round(self.ema, 3), "reason": reason,
            "face_present": signals.face_present,
            "face_box": list(signals.face_box) if signals.face_box else None,
            "eye_dark_ratio": _round_opt(signals.eye_dark_ratio),
            "mouth_open_ratio": _round_opt(signals.mouth_open_ratio),
        })

    def _emit(self, kind: str, payload: dict) -> None:
        if self.on_event is not None:
            try:
                self.on_event(kind, payload)
            except Exception as exc:  # noqa: BLE001 - logging never kills loop
                print(f"[dms] fatigue event log failed: {exc}")


def _round_opt(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(float(value), 4)


class LandmarkUnavailable(RuntimeError):
    """The explicitly selected Face Landmarker runtime or asset is unavailable."""


class LandmarkFatigueEngine:
    """Explainable fatigue state machine driven by MediaPipe face landmarks.

    The model only yields landmarks and blendshape values.  All fatigue states
    below are deterministic time-based rules, which makes a displayed warning
    explainable and configurable for the installed cabin camera.
    """

    def __init__(self, cfg: Any, on_event=None, clock=None) -> None:
        self.cfg, self.on_event = cfg, on_event
        self._clock = clock or time.monotonic
        model_path = str(cfg.get("fatigue.model_asset", ""))
        if not model_path or not __import__("os").path.isfile(model_path):
            raise LandmarkUnavailable("Face Landmarker asset missing: %s" % model_path)
        try:
            import mediapipe as mp
        except ImportError as exc:
            raise LandmarkUnavailable("mediapipe==0.10.5 is not installed") from exc
        try:
            options = mp.tasks.vision.FaceLandmarkerOptions(
                base_options=mp.tasks.BaseOptions(model_asset_path=model_path),
                running_mode=mp.tasks.vision.RunningMode.VIDEO,
                num_faces=1, output_face_blendshapes=True,
                output_facial_transformation_matrixes=True)
            self._mp = mp
            self._landmarker = mp.tasks.vision.FaceLandmarker.create_from_options(options)
        except Exception as exc:  # API/ABI incompatibility must stay visible
            raise LandmarkUnavailable("cannot create Face Landmarker: %s" % exc) from exc
        self.eye_warn_s = float(cfg.get("fatigue.thresholds.eye_warn_s", 1.5))
        self.eye_alarm_s = float(cfg.get("fatigue.thresholds.eye_alarm_s", 3.0))
        self.perclos_warn = float(cfg.get("fatigue.thresholds.perclos_warn", 0.40))
        self.perclos_alarm = float(cfg.get("fatigue.thresholds.perclos_alarm", 0.55))
        self.perclos_min_window_s = float(
            cfg.get("fatigue.thresholds.perclos_min_window_s", 10.0))
        self.yawn_warn_s = float(cfg.get("fatigue.thresholds.yawn_warn_s", 1.5))
        self.yawn_alarm_s = float(cfg.get("fatigue.thresholds.yawn_alarm_s", 3.0))
        self.yawn_alarm_count = int(cfg.get("fatigue.thresholds.yawn_alarm_count", 3))
        self.recovery_s = float(cfg.get("fatigue.thresholds.recovery_s", 1.0))
        self.pose_yaw_deg = float(cfg.get("fatigue.thresholds.pose_yaw_deg", 30.0))
        self.pose_pitch_deg = float(cfg.get("fatigue.thresholds.pose_pitch_deg", 25.0))
        self.pose_warn_s = float(cfg.get("fatigue.thresholds.pose_warn_s", 3.0))
        self.pose_alarm_s = float(cfg.get("fatigue.thresholds.pose_alarm_s", 5.0))
        self.closure_enter = float(cfg.get("fatigue.thresholds.eye_closure_enter", 0.45))
        self.yawn_enter = float(cfg.get("fatigue.thresholds.yawn_open_enter", 0.30))
        self.signal_ema_alpha = float(
            cfg.get("fatigue.thresholds.signal_ema_alpha", 0.35))
        self.enabled = True
        self.reset()

    def reset(self) -> None:
        self._state = STATE_UNKNOWN if self.enabled else STATE_DISABLED
        self._closed_since = self._yawn_since = self._distracted_since = None
        self._closed_samples: Deque[Tuple[float, bool]] = deque()
        self._yawns: Deque[float] = deque()
        self._eye_ema: Optional[float] = None
        self._jaw_ema: Optional[float] = None
        self._recovered_since: Optional[float] = None

    def set_enabled(self, flag: bool) -> None:
        self.enabled = bool(flag)
        self.reset()

    def _blendshape(self, result, name: str) -> Optional[float]:
        groups = getattr(result, "face_blendshapes", None) or []
        if not groups:
            return None
        wanted = name.lower()
        for item in groups[0]:
            if str(getattr(item, "category_name", "")).lower() == wanted:
                return float(getattr(item, "score", 0.0))
        return None

    @staticmethod
    def _box(landmarks, width: int, height: int) -> Box:
        xs, ys = [float(p.x) for p in landmarks], [float(p.y) for p in landmarks]
        x0, y0 = max(0, int(min(xs) * width)), max(0, int(min(ys) * height))
        x1, y1 = min(width, int(max(xs) * width)), min(height, int(max(ys) * height))
        return x0, y0, max(1, x1 - x0), max(1, y1 - y0)

    @staticmethod
    def _pose(result) -> tuple[Optional[float], Optional[float]]:
        matrices = getattr(result, "facial_transformation_matrixes", None) or []
        if not matrices:
            return None, None
        try:
            matrix = np.asarray(matrices[0], dtype=float).reshape(4, 4)
            rotation = matrix[:3, :3]
            yaw = float(np.degrees(np.arctan2(rotation[1, 0], rotation[0, 0])))
            pitch = float(np.degrees(np.arctan2(-rotation[2, 1], rotation[2, 2])))
            return yaw, pitch
        except Exception:
            return None, None

    def _smooth(self, value: Optional[float], previous: Optional[float]) -> Optional[float]:
        if value is None:
            return None
        value = max(0.0, min(1.0, float(value)))
        if previous is None:
            return value
        alpha = max(0.0, min(1.0, self.signal_ema_alpha))
        return alpha * value + (1.0 - alpha) * previous

    def _clear_active_signals(self) -> None:
        """Do not carry a held eye/yawn duration across a lost face."""
        self._closed_since = None
        self._yawn_since = None
        self._distracted_since = None
        self._eye_ema = None
        self._jaw_ema = None

    def update(self, frame_bgr: np.ndarray, _gray: np.ndarray) -> FatigueResult:
        if not self.enabled:
            return FatigueResult(STATE_DISABLED, 0.0, None)
        now = self._clock()
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        result = self._landmarker.detect_for_video(image, int(now * 1000))
        faces = getattr(result, "face_landmarks", None) or []
        if not faces:
            signals = FatigueSignals(False, None, -1, None, None,
                                     reason="FACE_NOT_VISIBLE")
            self._clear_active_signals()
            self._transition(STATE_UNKNOWN, now, signals)
            return FatigueResult(STATE_UNKNOWN, 0.0, signals)
        box = self._box(faces[0], frame_bgr.shape[1], frame_bgr.shape[0])
        left = self._blendshape(result, "eyeBlinkLeft")
        right = self._blendshape(result, "eyeBlinkRight")
        eye_raw = None if left is None or right is None else (left + right) / 2.0
        jaw_raw = self._blendshape(result, "jawOpen")
        self._eye_ema = self._smooth(eye_raw, self._eye_ema)
        self._jaw_ema = self._smooth(jaw_raw, self._jaw_ema)
        eye_closure = self._eye_ema
        jaw_open = self._jaw_ema
        yaw, pitch = self._pose(result)
        closed = eye_closure is not None and eye_closure >= self.closure_enter
        yawning = jaw_open is not None and jaw_open >= self.yawn_enter
        distracted = ((yaw is not None and abs(yaw) >= self.pose_yaw_deg)
                      or (pitch is not None and abs(pitch) >= self.pose_pitch_deg))
        active_signal = closed or yawning or distracted
        self._recovered_since = (None if active_signal else
                                 now if self._recovered_since is None else
                                 self._recovered_since)
        self._closed_since = now if closed and self._closed_since is None else None if not closed else self._closed_since
        previous_yawn = self._yawn_since
        if yawning:
            self._yawn_since = now if previous_yawn is None else previous_yawn
        else:
            if previous_yawn is not None and now - previous_yawn >= self.yawn_warn_s:
                self._yawns.append(now)
            self._yawn_since = None
        self._distracted_since = now if distracted and self._distracted_since is None else None if not distracted else self._distracted_since
        self._closed_samples.append((now, closed))
        while self._closed_samples and now - self._closed_samples[0][0] > 60.0:
            self._closed_samples.popleft()
        while self._yawns and now - self._yawns[0] > 60.0:
            self._yawns.popleft()
        perclos = (sum(1 for _, flag in self._closed_samples if flag) /
                   max(1, len(self._closed_samples)))
        perclos_window_s = (now - self._closed_samples[0][0]
                            if self._closed_samples else 0.0)
        perclos_ready = perclos_window_s >= self.perclos_min_window_s
        closed_s = now - self._closed_since if self._closed_since is not None else 0.0
        yawn_s = now - self._yawn_since if self._yawn_since is not None else 0.0
        pose_s = now - self._distracted_since if self._distracted_since is not None else 0.0
        recovered = (self._state in (STATE_WARN, STATE_ALARM)
                     and self._recovered_since is not None
                     and now - self._recovered_since >= self.recovery_s)
        if recovered:
            # The visible state represents the driver's current condition.
            # Do not make an old PERCLOS/yawn sample pin an alert after a
            # sustained recovery; the next fatigue episode starts a fresh window.
            self._closed_samples.clear()
            self._yawns.clear()
            perclos = 0.0
            perclos_ready = False
        alarm = (closed_s >= self.eye_alarm_s
                 or (perclos_ready and perclos >= self.perclos_alarm)
                 or yawn_s >= self.yawn_alarm_s
                 or len(self._yawns) >= self.yawn_alarm_count or pose_s >= self.pose_alarm_s)
        warn = (closed_s >= self.eye_warn_s
                or (perclos_ready and perclos >= self.perclos_warn)
                or yawn_s >= self.yawn_warn_s or pose_s >= self.pose_warn_s)
        state = STATE_ALARM if alarm else STATE_WARN if warn else STATE_NORMAL
        reason = ("RECOVERED" if recovered else
                  "EYE_CLOSURE" if closed_s >= self.eye_warn_s else
                  "PERCLOS" if perclos_ready and perclos >= self.perclos_warn else
                  "YAWN" if yawn_s >= self.yawn_warn_s else
                  "DISTRACTION" if pose_s >= self.pose_warn_s else "NORMAL")
        signals = FatigueSignals(True, box, 1, None, jaw_open,
                                 eye_closure=eye_closure, perclos=perclos,
                                 yawn_count_60s=len(self._yawns), yaw_deg=yaw,
                                 pitch_deg=pitch, reason=reason,
                                 mouth_open=yawning, eyes_closed=closed,
                                 mouth_open_duration_s=yawn_s,
                                 eye_closed_duration_s=closed_s)
        self._transition(state, now, signals)
        score = 1.0 if state == STATE_ALARM else 0.65 if state == STATE_WARN else 0.0
        return FatigueResult(self._state, score, signals)

    def _transition(self, state: str, now: float, signals: FatigueSignals) -> None:
        old = self._state
        if state == old:
            return
        self._state = state
        if self.on_event is not None:
            self.on_event("fatigue_state", {
                "from": old, "to": state, "reason": signals.reason,
                "face_present": signals.face_present,
                "perclos": _round_opt(signals.perclos),
                "yawn_count_60s": signals.yawn_count_60s,
                "yaw_deg": _round_opt(signals.yaw_deg),
                "pitch_deg": _round_opt(signals.pitch_deg),
            })
