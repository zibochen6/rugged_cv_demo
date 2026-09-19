"""Temporal stability: EMA on the nearest distance + presence voting.

Spec §18/§19:
  D_filtered = alpha*D_current + (1-alpha)*D_previous      (default 0.25)
  Presence voting: an obstacle must appear in trigger/history frames
  (default 3/5) before it officially "exists" — suppresses flicker.
"""
from __future__ import annotations

from collections import deque


class DistanceEMA:
    """Exponential smoothing of a scalar distance with NaN handling."""

    def __init__(self, alpha: float = 0.25):
        self.alpha = float(alpha)
        self.value: float | None = None

    def update(self, d: float | None) -> float | None:
        if d is None or (isinstance(d, float) and d != d):  # None/NaN
            return self.value  # hold last valid
        if self.value is None:
            self.value = float(d)
        else:
            self.value = self.alpha * float(d) + (1.0 - self.alpha) * self.value
        return self.value

    def reset(self) -> None:
        self.value = None


class PresenceVoter:
    """3/5-style frame voting on a boolean observation."""

    def __init__(self, history_frames: int = 5, trigger_frames: int = 3):
        assert 0 < trigger_frames <= history_frames
        self.history_frames = int(history_frames)
        self.trigger_frames = int(trigger_frames)
        self._buf: deque[int] = deque(maxlen=self.history_frames)

    def update(self, present: bool) -> bool:
        """Returns the voted (officially present) state."""
        self._buf.append(1 if present else 0)
        return sum(self._buf) >= self.trigger_frames

    def fill(self) -> int:
        return len(self._buf)

    def reset(self) -> None:
        self._buf.clear()