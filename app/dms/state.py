"""DmsRuntime — the single source of truth for the two runtime switches.

冻结语义（契约 §5.4）:
  * `set_flag()` 是 web 模式与 display 模式**唯一共用**的开关入口；
  * `set_flag()` 只改内存，不回写 configs/dms.yaml（yaml 只是初始值真源）；
  * 关闭某路后该路的状态必须变成 DISABLED / persons==[]，而不是"不画叠加"；
  * `infer_count` / `last_infer_fidx` / `last_infer_ms` 是"真停止"的证据字段，
    只在真正调用了该路引擎时自增（由主循环在分支内调用 note_*_infer）。
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, List, Optional

FLAG_NAMES = ("fatigue_enabled", "helmet_enabled")

# 关闭疲劳时对外发布的状态（契约 §5.4：必须是 DISABLED，不得伪装 NORMAL/UNKNOWN）
DISABLED_FATIGUE_VIEW: Dict[str, Any] = {
    "state": "DISABLED",
    "score": 0.0,
    "face_present": False,
    "face_box": None,
    "eye_dark_ratio": None,
    "mouth_open_ratio": None,
    "jaw_open": None,
    "mouth_open": False,
    "eyes_closed": False,
    "mouth_open_duration_s": 0.0,
    "eye_closed_duration_s": 0.0,
    "eye_closure": None,
    "perclos": None,
    "yawn_count_60s": 0,
    "yaw_deg": None,
    "pitch_deg": None,
    "reason": "DISABLED",
}


class DmsRuntime:
    """Thread-safe switch/counter/view store shared by both UI modes."""

    def __init__(self, *, fatigue_enabled: bool, helmet_enabled: bool,
                 notices: Optional[List[str]] = None,
                 fatigue_alarm_buzzer: bool = False,
                 on_flag_change: Optional[Callable[[str, bool], None]] = None
                 ) -> None:
        self._lock = threading.RLock()
        self._fatigue_enabled = bool(fatigue_enabled)
        self._helmet_enabled = bool(helmet_enabled)
        # Physical output remains owned by Visual Hub; this is only its
        # operator-controlled intent, defaulting to silent.
        self._fatigue_alarm_buzzer = bool(fatigue_alarm_buzzer)
        self._on_flag_change = on_flag_change
        self.notices = list(notices or [])

        self._fatigue = dict(DISABLED_FATIGUE_VIEW)
        self._fatigue_infer_count = 0
        self._fatigue_last_fidx = -1
        self._fatigue_last_ms = 0.0

        self._helmet_persons: List[Dict[str, Any]] = []
        self._helmet_infer_count = 0
        self._helmet_last_fidx = -1
        self._helmet_last_ms = 0.0
        self._helmet_counts = {"worn": 0, "not_worn": 0, "unknown": 0}
        self._fatigue_last_at = 0.0
        self._fatigue_fps = 0.0
        self._helmet_last_at = 0.0
        self._helmet_fps = 0.0
        self._thermal_state = "normal"
        self._helmet_target_fps = 5.0
        self._helmet_suspended = False
        self._degradation_reason: Optional[str] = None

    # -- switches -----------------------------------------------------------
    @property
    def fatigue_enabled(self) -> bool:
        with self._lock:
            return self._fatigue_enabled

    @property
    def helmet_enabled(self) -> bool:
        with self._lock:
            return self._helmet_enabled

    @property
    def fatigue_alarm_buzzer(self) -> bool:
        with self._lock:
            return self._fatigue_alarm_buzzer

    def set_flag(self, name: str, value: bool) -> None:
        """Flip one switch (memory only). Unknown names are ignored."""
        if name not in FLAG_NAMES:
            return
        changed = False
        with self._lock:
            new = bool(value)
            if name == "fatigue_enabled":
                changed = self._fatigue_enabled != new
                self._fatigue_enabled = new
                if not new and changed:
                    self._fatigue = dict(DISABLED_FATIGUE_VIEW)
            else:
                changed = self._helmet_enabled != new
                self._helmet_enabled = new
                if not new and changed:
                    self._helmet_persons = []
        if changed and self._on_flag_change is not None:
            try:
                self._on_flag_change(name, new)
            except Exception as exc:  # noqa: BLE001 - never kill the caller
                print(f"[dms] switch callback failed: {exc}")

    def apply_config(self, data: dict) -> dict:
        """Whitelist + type check; invalid entries keep their current value."""
        if isinstance(data, dict):
            for name in FLAG_NAMES:
                if name in data and isinstance(data[name], bool):
                    self.set_flag(name, data[name])
            if isinstance(data.get("fatigue_alarm_buzzer"), bool):
                with self._lock:
                    self._fatigue_alarm_buzzer = data["fatigue_alarm_buzzer"]
            thermal_state = data.get("thermal_state")
            if thermal_state in ("normal", "constrained", "critical"):
                try:
                    target_fps = max(
                        0.0, float(data.get("helmet_target_fps", 5.0)))
                except (TypeError, ValueError):
                    target_fps = self.helmet_target_fps
                with self._lock:
                    self._thermal_state = str(thermal_state)
                    self._helmet_target_fps = target_fps
                    self._helmet_suspended = bool(
                        data.get("helmet_suspended", False))
                    reason = data.get("degradation_reason")
                    self._degradation_reason = (
                        str(reason) if reason else None)
        return self.snapshot()

    @property
    def helmet_target_fps(self) -> float:
        with self._lock:
            return self._helmet_target_fps

    @property
    def helmet_inference_allowed(self) -> bool:
        with self._lock:
            return self._helmet_enabled and not self._helmet_suspended

    # -- inference evidence (only called when the branch really ran) --------
    def note_fatigue_infer(self, ms: float, fidx: int = -1) -> None:
        now = time.monotonic()
        with self._lock:
            self._fatigue_infer_count += 1
            self._fatigue_last_fidx = int(fidx)
            self._fatigue_last_ms = round(float(ms), 3)
            if self._fatigue_last_at > 0.0:
                instant = 1.0 / max(1e-6, now - self._fatigue_last_at)
                self._fatigue_fps = (instant if self._fatigue_fps <= 0.0
                                     else 0.9 * self._fatigue_fps + 0.1 * instant)
            self._fatigue_last_at = now

    def note_helmet_infer(self, ms: float, fidx: int = -1) -> None:
        now = time.monotonic()
        with self._lock:
            self._helmet_infer_count += 1
            self._helmet_last_fidx = int(fidx)
            self._helmet_last_ms = round(float(ms), 3)
            if self._helmet_last_at > 0.0:
                instant = 1.0 / max(1e-6, now - self._helmet_last_at)
                self._helmet_fps = (instant if self._helmet_fps <= 0.0
                                    else 0.9 * self._helmet_fps + 0.1 * instant)
            self._helmet_last_at = now

    # -- published views ----------------------------------------------------
    def set_fatigue_view(self, result: Optional[Any]) -> None:
        """Publish a FatigueResult (or None => DISABLED view)."""
        if result is None:
            with self._lock:
                self._fatigue = dict(DISABLED_FATIGUE_VIEW)
            return
        sig = getattr(result, "signals", None)
        box = getattr(sig, "face_box", None) if sig is not None else None
        view = {
            "state": str(getattr(result, "state", "UNKNOWN")),
            "score": round(float(getattr(result, "score", 0.0)), 4),
            "face_present": bool(getattr(sig, "face_present", False))
            if sig is not None else False,
            "face_box": list(box) if box else None,
            "eye_dark_ratio": _round_opt(getattr(sig, "eye_dark_ratio", None))
            if sig is not None else None,
            "mouth_open_ratio": _round_opt(
                getattr(sig, "mouth_open_ratio", None)) if sig is not None else None,
            # ``mouth_open_ratio`` was published before the landmark backend.
            # Keep it as a compatibility alias and expose the semantic name too.
            "jaw_open": _round_opt(getattr(sig, "mouth_open_ratio", None))
            if sig is not None else None,
            "mouth_open": bool(getattr(sig, "mouth_open", False))
            if sig is not None else False,
            "eyes_closed": bool(getattr(sig, "eyes_closed", False))
            if sig is not None else False,
            "mouth_open_duration_s": _round_opt(
                getattr(sig, "mouth_open_duration_s", 0.0)) if sig is not None else 0.0,
            "eye_closed_duration_s": _round_opt(
                getattr(sig, "eye_closed_duration_s", 0.0)) if sig is not None else 0.0,
            "eye_closure": _round_opt(getattr(sig, "eye_closure", None))
            if sig is not None else None,
            "perclos": _round_opt(getattr(sig, "perclos", None))
            if sig is not None else None,
            "yawn_count_60s": int(getattr(sig, "yawn_count_60s", 0))
            if sig is not None else 0,
            "yaw_deg": _round_opt(getattr(sig, "yaw_deg", None))
            if sig is not None else None,
            "pitch_deg": _round_opt(getattr(sig, "pitch_deg", None))
            if sig is not None else None,
            "reason": str(getattr(sig, "reason", "")) if sig is not None else "",
        }
        with self._lock:
            self._fatigue = view

    def set_helmet_view(self, verdicts: Optional[List[Any]]) -> None:
        """Publish HelmetVerdict list (or None => no persons, disabled)."""
        persons: List[Dict[str, Any]] = []
        with self._lock:
            if verdicts:
                for v in verdicts:
                    verdict = str(getattr(v, "verdict", "unknown"))
                    if verdict in self._helmet_counts:
                        self._helmet_counts[verdict] += 1
                    persons.append({
                        "track_id": int(getattr(v, "track_id", -1)),
                        "bbox": list(getattr(v, "bbox", (0, 0, 0, 0))),
                        "head_roi": list(getattr(v, "head_roi", (0, 0, 0, 0))),
                        "verdict": verdict,
                        "reason": str(getattr(v, "reason", "")),
                        "conf": str(getattr(v, "conf", "low")),
                        "helmet_color_ratio": _round_opt(
                            getattr(v, "helmet_color_ratio", None)),
                        "skin_ratio": _round_opt(getattr(v, "skin_ratio", None)),
                        "dark_ratio": _round_opt(getattr(v, "dark_ratio", None)),
                        "age_frames": int(getattr(v, "age_frames", 0)),
                    })
            self._helmet_persons = persons

    # -- snapshot -----------------------------------------------------------
    def snapshot(self) -> dict:
        """`/state` 的 `dms` 子树（字段名冻结，见 docs/dms_helmet_demo.md）。"""
        with self._lock:
            return {
                "fatigue": {
                    "enabled": self._fatigue_enabled,
                    "alarm_buzzer": self._fatigue_alarm_buzzer,
                    **self._fatigue,
                    "infer_count": self._fatigue_infer_count,
                    "last_infer_fidx": self._fatigue_last_fidx,
                    "last_infer_ms": self._fatigue_last_ms,
                    "inference_fps": round(self._fatigue_fps, 2),
                    "result_age_s": (round(time.monotonic() - self._fatigue_last_at, 3)
                                     if self._fatigue_last_at > 0.0 else None),
                },
                "helmet": {
                    "enabled": self._helmet_enabled,
                    "infer_count": self._helmet_infer_count,
                    "last_infer_fidx": self._helmet_last_fidx,
                    "last_infer_ms": self._helmet_last_ms,
                    "inference_fps": (
                        0.0 if self._helmet_suspended
                        else round(self._helmet_fps, 2)
                    ),
                    "result_age_s": (round(time.monotonic() - self._helmet_last_at, 3)
                                     if self._helmet_last_at > 0.0 else None),
                    "target_fps": self._helmet_target_fps,
                    "thermal_suspended": self._helmet_suspended,
                    "verdict_counts": dict(self._helmet_counts),
                    "persons": [dict(p) for p in self._helmet_persons],
                },
                "thermal": {
                    "state": self._thermal_state,
                    "degradation_reason": self._degradation_reason,
                },
            }


def _round_opt(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None


def now_ts() -> float:
    return time.time()
