"""Obstacle candidate extraction + robust distance (§12/§13/§17).

The nearest-distance estimate explicitly does NOT use depth.min(): each
connected obstacle region contributes the configured percentile (default 10)
of its valid depths, and component-level spurious noise is removed by
morphology + minimum area.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class ObstacleCandidate:
    idx: int
    bbox: tuple  # (x, y, w, h)
    area_px: int
    distance_m: float
    valid_px: int


def extract_candidates(cand_mask: np.ndarray, depth_m: np.ndarray,
                       min_area_px: int = 200, percentile: float = 10.0,
                       morphology: bool = True) -> list[ObstacleCandidate]:
    """cand_mask: HxW bool candidate pixels (ground-filtered + ROI + valid).
    depth_m: HxW metric depth (NaN invalid)."""
    m = np.asarray(cand_mask, dtype=np.uint8)
    if morphology:
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, kernel)
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, kernel)
    n_labels, labels, stats, _ = cv2.connectedComponentsWithStats(
        m, connectivity=8)
    result: list[ObstacleCandidate] = []
    d = np.asarray(depth_m, dtype=np.float32)
    for i in range(1, n_labels):
        x, y, w, h, area = stats[i]
        if area < min_area_px:
            continue
        region_mask = labels[y:y + h, x:x + w] == i
        vals = d[y:y + h, x:x + w][region_mask]
        vals = vals[np.isfinite(vals)]
        if vals.size == 0:
            continue
        dist = float(np.percentile(vals, percentile))
        result.append(ObstacleCandidate(
            idx=int(i), bbox=(int(x), int(y), int(w), int(h)),
            area_px=int(area), distance_m=dist, valid_px=int(vals.size)))
    return result


def nearest_candidate(cands: list[ObstacleCandidate]):
    """Candidate with the smallest distance (or None)."""
    if not cands:
        return None
    return min(cands, key=lambda c: c.distance_m)


def candidate_visual_mask(cands: list[ObstacleCandidate], labels: np.ndarray,
                          target_idx, h: int, w: int) -> np.ndarray:
    """Bool mask of ONE candidate (for debug view). labels from CC stats."""
    return labels == target_idx