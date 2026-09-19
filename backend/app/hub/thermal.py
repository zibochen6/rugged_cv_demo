"""Jetson thermal policy with immediate escalation and delayed recovery."""
from __future__ import annotations

import glob
import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional


@dataclass(frozen=True)
class ThermalPolicy:
    state: str
    rear_depth_fps: float
    rear_person_fps: float
    front_fps: float
    dms_helmet_fps: float
    dms_helmet_suspended: bool
    reason: Optional[str]


POLICIES: Dict[str, ThermalPolicy] = {
    "normal": ThermalPolicy("normal", 10.0, 5.0, 8.0, 5.0, False, None),
    "constrained": ThermalPolicy(
        "constrained", 10.0, 5.0, 4.0, 1.0, False,
        "温度达到 88C，降低座舱头盔与前视推理频率",
    ),
    "critical": ThermalPolicy(
        "critical", 8.0, 4.0, 1.0, 0.0, True,
        "温度达到 89C，暂停座舱头盔并降低前视与后视推理频率",
    ),
}


def read_max_temp_c() -> Optional[float]:
    values = []
    for path in glob.glob("/sys/class/thermal/thermal_zone*/temp"):
        try:
            with open(path, "r", encoding="ascii") as handle:
                raw = float(handle.read().strip())
        except (OSError, ValueError):
            continue
        values.append(raw / 1000.0 if raw > 1000.0 else raw)
    return max(values) if values else None


class ThermalController:
    """Three-level controller; recovery happens one level per cool hold."""

    def __init__(
        self,
        reader: Callable[[], Optional[float]] = read_max_temp_c,
        clock: Callable[[], float] = time.monotonic,
        constrained_c: float = 88.0,
        critical_c: float = 89.0,
        recovery_c: float = 85.0,
        recovery_hold_s: float = 30.0,
    ) -> None:
        self._reader = reader
        self._clock = clock
        self._constrained_c = constrained_c
        self._critical_c = critical_c
        self._recovery_c = recovery_c
        self._recovery_hold_s = recovery_hold_s
        self._state = "normal"
        self._max_temp_c: Optional[float] = None
        self._cool_since: Optional[float] = None

    @property
    def policy(self) -> ThermalPolicy:
        return POLICIES[self._state]

    def update(self) -> dict:
        now = self._clock()
        temp = self._reader()
        self._max_temp_c = temp
        if temp is None:
            self._cool_since = None
            return self.snapshot()

        if temp >= self._critical_c:
            self._state = "critical"
            self._cool_since = None
        elif temp >= self._constrained_c:
            if self._state == "normal":
                self._state = "constrained"
            self._cool_since = None
        elif temp < self._recovery_c and self._state != "normal":
            if self._cool_since is None:
                self._cool_since = now
            elif now - self._cool_since >= self._recovery_hold_s:
                self._state = (
                    "constrained" if self._state == "critical" else "normal"
                )
                self._cool_since = now if self._state != "normal" else None
        else:
            self._cool_since = None
        return self.snapshot()

    def snapshot(self) -> dict:
        policy = self.policy
        degraded = []
        if policy.state != "normal":
            degraded.append("front")
            degraded.append("dms")
        return {
            "state": policy.state,
            "max_temp_c": (
                round(self._max_temp_c, 1)
                if self._max_temp_c is not None else None
            ),
            "policy": {
                "rear_depth_fps": policy.rear_depth_fps,
                "rear_person_fps": policy.rear_person_fps,
                "front_fps": policy.front_fps,
                "dms_helmet_fps": policy.dms_helmet_fps,
                "dms_helmet_suspended": policy.dms_helmet_suspended,
            },
            "degraded_modules": degraded,
            "reason": policy.reason,
            "recovery": {
                "below_c": self._recovery_c,
                "hold_s": self._recovery_hold_s,
            },
        }
