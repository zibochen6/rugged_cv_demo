"""Danger ROI (normalized polygon) and physical corridor (§15/§16)."""
from __future__ import annotations

import numpy as np
import cv2


class DangerRegion:
    """Polygon in NORMALIZED coordinates (0..1), the spec's recommended form.

    `mask(h, w)` rasterizes it camera-resolution; the app uses the same
    normalized polygon for drawing at any display size.
    """

    def __init__(self, normalized_points) -> None:
        pts = np.asarray(normalized_points, dtype=np.float32).reshape(-1, 2)
        if pts.shape[0] < 3:
            pts = np.array([[0.2, 1.0], [0.8, 1.0], [0.63, 0.42],
                            [0.37, 0.42]], dtype=np.float32)
        self.norm_pts = pts
        self._mask_cache: tuple | None = None

    def to_pixels(self, h: int, w: int) -> np.ndarray:
        """(N,2) int32 polygon corners at the given resolution."""
        return (self.norm_pts * np.array([w, h], dtype=np.float32)).astype(np.int32)

    def mask(self, h: int, w: int) -> np.ndarray:
        """HxW bool mask; cached per resolution."""
        key = (h, w)
        if self._mask_cache == key:
            return self._roi_mask
        m = np.zeros((h, w), dtype=np.uint8)
        poly = self.to_pixels(h, w)
        if len(poly) >= 3:
            cv2.fillPoly(m, [poly], 1)
        self._roi_mask = m > 0
        self._mask_cache = key
        return self._roi_mask

    def contains(self, x: float, y: float, h: int, w: int) -> bool:
        poly = self.to_pixels(h, w).astype(np.float32)
        return float(cv2.pointPolygonTest(poly, (float(x), float(y)), False)) >= 0


class PhysicalCorridor:
    """Real-world collision corridor (BEV, §16). `enabled=False` in MVP.

    Pixels whose ground-plane projection lies within |x|<=width/2 and
    z<=max_distance_m. Needs calibrated intrinsics + extrinsics; without
    them the result is empty and the caller skips it.
    """

    def __init__(self, cam, extr, width_m: float = 1.8,
                 max_distance_m: float = 6.0, enabled: bool = False) -> None:
        self.cam = cam
        self.extr = extr
        self.width_m = float(width_m)
        self.max_distance_m = float(max_distance_m)
        self.enabled = enabled
        self._cache: tuple | None = None

    def mask(self, h: int, w: int) -> np.ndarray:
        if not self.enabled:
            return np.zeros((h, w), dtype=bool)
        key = (h, w)
        if self._cache == key:
            return self._corridor_mask
        m = np.zeros((h, w), dtype=bool)
        u, v = np.meshgrid(np.arange(w), np.arange(h))
        uv = np.stack([u.ravel(), v.ravel()], axis=1).astype(np.float64)
        uv_ud = self.cam.undistort_points(uv)
        ray = np.stack([
            (uv_ud[:, 0] - self.cam.cx) / self.cam.fx,
            (uv_ud[:, 1] - self.cam.cy) / self.cam.fy,
            np.ones(uv_ud.shape[0]),
        ], axis=1)
        rg = ray @ self.extr.M.T
        y = rg[:, 1]
        lam = np.where(y < -1e-9, -self.extr.t[1] / y, np.nan)
        # ground point in camera coords: lam*ray -> (X,Z) lateral/forward
        X = lam * ray[:, 0]
        Z = lam * ray[:, 2]
        inside = (np.abs(X) <= self.width_m / 2.0) & (Z > 0) & \
                 (Z <= self.max_distance_m) & np.isfinite(lam)
        m = inside.reshape(h, w)
        self._corridor_mask = m
        self._cache = key
        return m