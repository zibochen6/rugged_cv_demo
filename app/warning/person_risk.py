"""Person-specific distance estimation and two-zone warning decisions."""
from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

from app.warning.person_detection import PersonDetection
from app.warning.risk_engine import DANGER, SAFE, WARNING

PERSON_UNKNOWN_DISTANCE = "PERSON_UNKNOWN_DISTANCE"


@dataclass(frozen=True)
class PersonRisk:
    level: str
    reason: str
    people: tuple[PersonDetection, ...]
    primary: PersonDetection | None


def estimate_distance_at_feet(depth_m: np.ndarray | None,
                              bbox: tuple[int, int, int, int],
                              min_valid_pixels: int = 8
                              ) -> tuple[float | None, bool]:
    if depth_m is None or depth_m.ndim != 2:
        return None, False
    x, y, w, h = bbox
    ih, iw = depth_m.shape
    x0 = max(0, int(x + 0.30 * w))
    x1 = min(iw, int(x + 0.70 * w) + 1)
    y0 = max(0, int(y + 0.72 * h))
    y1 = min(ih, int(y + h) + 1)
    if x1 <= x0 or y1 <= y0:
        return None, False
    values = depth_m[y0:y1, x0:x1]
    values = values[np.isfinite(values) & (values > 0.05)]
    if values.size < min_valid_pixels:
        return None, False
    # A lower percentile resists background pixels between a person's legs.
    return float(np.percentile(values, 30.0)), True


def footpoint_in_normalized_roi(
        bbox: tuple[int, int, int, int],
        frame_shape: tuple[int, int],
        polygon: list[list[float]]) -> bool:
    if not polygon:
        return True
    x, y, w, h = bbox
    ih, iw = frame_shape
    px = (x + 0.5 * w) / max(1, iw)
    py = (y + h) / max(1, ih)
    pts = np.asarray(polygon, dtype=np.float64)
    inside = False
    j = len(pts) - 1
    for i in range(len(pts)):
        xi, yi = pts[i]
        xj, yj = pts[j]
        crosses = ((yi > py) != (yj > py))
        if crosses and px < (xj - xi) * (py - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


class PersonRiskAssessor:
    def __init__(self, warning_distance_m: float = 4.0,
                 danger_distance_m: float = 2.0,
                 roi_points: list[list[float]] | None = None,
                 unknown_level: str = WARNING) -> None:
        self.warning_distance_m = float(warning_distance_m)
        self.danger_distance_m = float(danger_distance_m)
        self.roi_points = roi_points or []
        self.unknown_level = unknown_level

    def assess(self, detections: list[PersonDetection],
               depth_m: np.ndarray | None,
               frame_shape: tuple[int, int]) -> PersonRisk:
        people = []
        for det in detections:
            if not footpoint_in_normalized_roi(
                    det.bbox, frame_shape, self.roi_points):
                continue
            sample_box = det.bbox
            if depth_m is not None and depth_m.shape != frame_shape:
                ih, iw = frame_shape
                dh, dw = depth_m.shape
                x, y, w, h = det.bbox
                sample_box = (
                    int(round(x * dw / max(1, iw))),
                    int(round(y * dh / max(1, ih))),
                    max(1, int(round(w * dw / max(1, iw)))),
                    max(1, int(round(h * dh / max(1, ih)))))
            distance, valid = estimate_distance_at_feet(depth_m, sample_box)
            people.append(replace(det, distance_m=distance,
                                  distance_valid=valid))
        if not people:
            return PersonRisk(SAFE, "NO_PERSON_IN_ZONE", tuple(), None)
        known = [p for p in people if p.distance_valid]
        primary = (
            min(known, key=lambda p: (
                p.distance_m if p.distance_m is not None else float("inf")))
            if known else max(people, key=lambda p: p.confidence)
        )
        if not primary.distance_valid or primary.distance_m is None:
            return PersonRisk(self.unknown_level, PERSON_UNKNOWN_DISTANCE,
                              tuple(people), primary)
        if primary.distance_m <= self.danger_distance_m:
            level, reason = DANGER, "PERSON_DANGER_ZONE"
        elif primary.distance_m <= self.warning_distance_m:
            level, reason = WARNING, "PERSON_WARNING_ZONE"
        else:
            level, reason = SAFE, "PERSON_OUTER_CLEAR"
        return PersonRisk(level, reason, tuple(people), primary)
