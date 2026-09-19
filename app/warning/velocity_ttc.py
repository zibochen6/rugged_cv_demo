"""Closing velocity + TTC (§21/§22).

Velocity = -slope of a linear regression over the last N (time, distance)
samples. Positive = approaching. TTC is only valid when closing speed
exceeds min_closing_speed_mps, otherwise None/infinite.
"""
from __future__ import annotations

from collections import deque


def _lin_slope(ts: list[float], ds: list[float]):
    """Least-squares slope of d vs t. Needs >=2 samples. Returns None on
    degenerate input (constant distance is slope 0, which is fine)."""
    n = len(ts)
    if n < 2:
        return None
    mt, md = sum(ts) / n, sum(ds) / n
    num = sum((t - mt) * (d - md) for t, d in zip(ts, ds))
    den = sum((t - mt) ** 2 for t in ts)
    if den <= 0:
        return None
    return float(num / den)


class VelocityTTC:
    """Sliding-window velocity/TTC estimator.

    update(distance_m, now_s) -> (closing_mps or None, ttc_s or None)
    """

    def __init__(self, window_frames: int = 10,
                 min_closing_speed_mps: float = 0.10, enabled: bool = True):
        self.window = max(3, int(window_frames))
        self.min_closing = float(min_closing_speed_mps)
        self.enabled = enabled
        self._ts: deque[float] = deque(maxlen=self.window)
        self._ds: deque[float] = deque(maxlen=self.window)
        self._last_d: float | None = None

    def update(self, distance_m, now_s: float) -> tuple:
        """Returns (closing_speed_mps, ttc_s). Either may be None."""
        self._last_d = float(distance_m)
        self._ts.append(float(now_s))
        self._ds.append(float(distance_m))
        slope = _lin_slope(list(self._ts), list(self._ds))
        if slope is None or not self.enabled:
            return None, None
        closing = -slope  # positive = approaching
        if closing <= self.min_closing:
            return closing, None
        ttc = self._last_d / closing
        return float(closing), float(ttc)

    def reset(self) -> None:
        self._ts.clear()
        self._ds.clear()
        self._last_d = None

    @property
    def samples(self) -> int:
        return len(self._ts)