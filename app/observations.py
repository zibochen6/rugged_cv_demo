"""Shared frame freshness contracts for live perception pipelines."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ObservationStamp:
    """Identity and capture time for one perception result."""

    frame_id: int
    timestamp_s: float
    coordinate_frame: str = "camera"

    def is_fresh(self, now_s: float, max_age_s: float = 0.5) -> bool:
        return self.frame_id >= 0 and 0.0 <= now_s - self.timestamp_s <= max_age_s


class NewFrameGate:
    """Accept a monotonically increasing frame id once."""

    def __init__(self) -> None:
        self._last_frame_id = -1

    @property
    def last_frame_id(self) -> int:
        return self._last_frame_id

    def accept(self, frame_id: int) -> bool:
        frame_id = int(frame_id)
        if frame_id <= self._last_frame_id:
            return False
        self._last_frame_id = frame_id
        return True

    def reset(self) -> None:
        self._last_frame_id = -1
