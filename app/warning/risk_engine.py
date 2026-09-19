"""Risk decision engine + watchdog (§23/§44/§45).

States: SAFE, WARNING, DANGER, SYSTEM_ERROR.

Rules:
  base distance rule : d <= warning_dist -> WARNING, d <= danger_dist -> DANGER
  TTC rule          : ttc <= warning_ttc -> WARNING, ttc <= danger_ttc -> DANGER
  hysteresis        : entering uses the raw thresholds; EXITING needs the
                      threshold + exit margin (0.3 m / 0.5 s defaults) so the
                      state does not flicker around the boundary (§20).
  SYSTEM ERROR      : watchdog failures override everything and MUST be
                      displayed instead of SAFE (fail visible, §44/§63):
                        - camera lost (frames stale > timeout)
                        - >= max_consecutive_bad_frames invalid depth frames
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum

SAFE = "SAFE"
WARNING = "WARNING"
DANGER = "DANGER"
SYSTEM_ERROR = "SYSTEM ERROR"


@dataclass
class RiskOutput:
    level: str
    distance_m: float | None      # filtered nearest distance
    velocity_mps: float | None    # closing speed (positive = approaching)
    ttc_s: float | None
    reason: str                   # human-readable trigger


class RiskEngine:
    def __init__(self, warning_distance_m: float = 3.0,
                 danger_distance_m: float = 1.5,
                 warning_ttc_s: float = 3.0, danger_ttc_s: float = 1.5,
                 exit_margin_m: float = 0.3, exit_ttc_s: float = 0.5,
                 max_consecutive_bad_frames: int = 3) -> None:
        self.warn_d = float(warning_distance_m)
        self.danger_d = float(danger_distance_m)
        self.warn_ttc = float(warning_ttc_s)
        self.danger_ttc = float(danger_ttc_s)
        self.exit_m = float(exit_margin_m)
        self.exit_ttc = float(exit_ttc_s)
        self.max_bad = int(max_consecutive_bad_frames)
        self.state = SAFE
        self._bad_frames = 0

    # -- watchdog -----------------------------------------------------------
    def _camera_lost(self, camera_stale_s: float, timeout_s: float) -> bool:
        return camera_stale_s > timeout_s

    def update(self, distance_m: float | None, velocity_mps: float | None,
               ttc_s: float | None, camera_stale_s: float = 0.0,
               camera_timeout_s: float = 5.0,
               depth_valid: bool = True, obstacle_present: bool = False) -> RiskOutput:
        # watchdog dominates: never show SAFE when perception is broken
        if depth_valid:
            self._bad_frames = 0
        else:
            self._bad_frames += 1

        if self._camera_lost(camera_stale_s, camera_timeout_s) or \
                self._bad_frames >= self.max_bad:
            self.state = SYSTEM_ERROR
            return RiskOutput(SYSTEM_ERROR, distance_m,
                              velocity_mps, ttc_s,
                              f"camera_stale={camera_stale_s:.1f}s "
                              f"bad_depth={self._bad_frames}")

        # an obstacle only counts when it is PERSISTENT (voted); otherwise a
        # stale EMA distance would keep the state DANGER forever (§19)
        if not obstacle_present:
            distance_m, ttc_s = None, None

        cur = self.state
        d = distance_m
        t = ttc_s

        d_warn = d is not None and d <= self.warn_d
        d_danger = d is not None and d <= self.danger_d
        # hysteresis on EXIT: need thresholds + margin to leave a state
        if cur == SAFE:
            target = DANGER if (d_danger or
                                (t is not None and t <= self.danger_ttc)) \
                else WARNING if (d_warn or
                                 (t is not None and t <= self.warn_ttc)) \
                else SAFE
        elif cur == WARNING:
            if d_danger or (t is not None and t <= self.danger_ttc):
                target = DANGER
            elif (d is None or d > self.warn_d + self.exit_m) and \
                    (t is None or t > self.warn_ttc + self.exit_ttc):
                target = SAFE
            else:
                target = WARNING
        elif cur == DANGER:
            if (d is None or d > self.danger_d + self.exit_m) and \
                    (t is None or t > self.danger_ttc + self.exit_ttc):
                target = WARNING if (d_warn or
                                     (t is not None and t <= self.warn_ttc)) \
                    else SAFE
            else:
                target = DANGER
        else:  # SYSTEM_ERROR -> recover via SAFE path
            target = SAFE

        self.state = target
        reason = f"d={d:.2f}m" if d is not None else "d=None"
        if t is not None:
            reason += f" ttc={t:.2f}s"
        if not obstacle_present:
            reason = "no obstacle in ROI"
        return RiskOutput(target, d, velocity_mps, t, reason)