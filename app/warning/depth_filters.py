"""Depth-map filtering: invalid/range/spatial/temporal (§11 of the spec).

All functions are numpy, camera resolution (H x W float32), shape-preserving.
NaN is the project-wide "invalid depth" marker.
"""
from __future__ import annotations

from typing import Optional

import cv2
import numpy as np


def validity_mask(depth_m: np.ndarray, min_m: float = 0.3,
                  max_m: float = 10.0) -> np.ndarray:
    """True where depth is finite, >0 and inside [min, max]. NaN/Inf/<=0
    and out-of-range pixels are rejected (§11)."""
    d = np.asarray(depth_m, dtype=np.float32)
    return np.isfinite(d) & (d > min_m) & (d <= max_m)


def invalidate(depth_m: np.ndarray, min_m: float = 0.3,
               max_m: float = 10.0) -> np.ndarray:
    """Return a sanitized depth map: invalid pixels -> NaN."""
    d = np.asarray(depth_m, dtype=np.float32).copy()
    d[~validity_mask(d, min_m, max_m)] = np.nan
    return d


def spatial_smooth(depth_m: np.ndarray, kernel: int = 3,
                   mask: Optional[np.ndarray] = None) -> np.ndarray:
    """Fast spatial smoothing: small median blur over valid pixels.

    mask (optional): boolean validity mask to protect.
    """
    if kernel <= 1:
        return depth_m
    d = np.asarray(depth_m, dtype=np.float32)
    m = mask if mask is not None else np.isfinite(d)
    if not m.any():
        return d.copy()
    work = d.copy()
    work[~m] = np.nan
    kernel = kernel if kernel % 2 == 1 else kernel + 1
    # cv2.medianBlur on float32 with NaN: outlier-robust, reasonably fast
    smooth = cv2.medianBlur(work, kernel)
    out = np.where(m, smooth, d)
    return out


class TemporalDepthFilter:
    """Per-pixel EMA (alpha) between consecutive depth maps (§11/§18).

    `update(depth, mask)` -> smoothed copy; NaN pixels carry the previous
    value when available, else stay NaN.
    """

    def __init__(self, alpha: float = 0.25):
        self.alpha = float(alpha)
        self._prev: Optional[np.ndarray] = None

    @property
    def enabled(self) -> bool:
        return 0.0 < self.alpha < 1.0

    def update(self, depth_m: np.ndarray,
               mask: Optional[np.ndarray] = None) -> np.ndarray:
        d = np.asarray(depth_m, dtype=np.float32)
        m = mask if mask is not None else np.isfinite(d)
        if not self.enabled:
            self._prev = d.copy()
            return d
        if self._prev is None or self._prev.shape != d.shape:
            self._prev = d.copy()
            return d
        prev = self._prev
        prev_valid = np.isfinite(prev)
        # NaN-aware blend: a NaN previous (e.g. an all-invalid history such
        # as a warmup pass over a zero map) must not poison the output —
        # fall back to the current sample where prev is invalid
        blended = np.where(prev_valid,
                           self.alpha * d + (1.0 - self.alpha) * prev, d)
        # where current invalid but previous valid -> keep previous
        out = np.where(m, blended, np.where(prev_valid, prev, d))
        self._prev = out
        return out

    def reset(self) -> None:
        self._prev = None