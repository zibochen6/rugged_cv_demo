"""Pixel + depth -> XYZ back-projection (Phase 10D).

Pinhole model in the camera coordinate system (+X right, +Y down, +Z forward):
    X = (u - cx) * Z / fx
    Y = (v - cy) * Z / fy
    Z = depth (meters)

Object reference pixel (§25): mask centroid is the Phase-10D baseline
(baseline choice: mask centroid + mask median depth). Alternatives
(median-depth centroid / lower-center / robust center) remain as options.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import torch

from app.geometry.camera_model import CameraModel

REF_CENTROID = "centroid"
REF_LOWER_CENTER = "lower_center"


def mask_reference_pixel(mask, how: str = REF_CENTROID) -> tuple[float, float]:
    """(u, v) reference pixel for an object mask (bool HxW, numpy or torch)."""
    m = mask if isinstance(mask, np.ndarray) else mask.cpu().numpy()
    ys, xs = np.where(m)
    if len(xs) == 0:
        return float("nan"), float("nan")
    if how == REF_LOWER_CENTER:
        # median x of the lowest 10% rows: approximates ground contact point
        y_thr = np.quantile(ys, 0.90)
        sel = ys >= y_thr
        return float(np.median(xs[sel])), float(np.median(ys[sel]))
    return float(xs.mean()), float(ys.mean())


@dataclass
class XYZ:
    x: float
    y: float
    z: float

    def as_array(self) -> np.ndarray:
        return np.array([self.x, self.y, self.z], dtype=np.float64)


def pixel_depth_to_xyz(u: float, v: float, z_m: float, cam: CameraModel) -> XYZ:
    """Single-pixel back-projection using (optionally undistorted) pixels."""
    if not np.isfinite(z_m) or z_m <= 0:
        return XYZ(float("nan"), float("nan"), float("nan"))
    uv = cam.undistort_points(np.array([[u, v]], dtype=np.float64))[0]
    x = (uv[0] - cam.cx) * z_m / cam.fx
    y = (uv[1] - cam.cy) * z_m / cam.fy
    return XYZ(float(x), float(y), float(z_m))


@torch.inference_mode()
def mask_to_xyz(mask, cam: CameraModel, distance_m: float,
                how: str = REF_CENTROID) -> XYZ:
    """Object XYZ from mask reference pixel + robust distance (centroid baseline)."""
    u, v = mask_reference_pixel(mask, how)
    return pixel_depth_to_xyz(u, v, distance_m, cam)


def project_points(pts, cam: CameraModel) -> tuple[np.ndarray, np.ndarray]:
    """(N,3) camera-coord points (torch or numpy) -> ((N,2) pixel coords,
    (N,) bool valid with Z > eps)."""
    P = pts if isinstance(pts, np.ndarray) else pts.detach().cpu().numpy()
    P = np.asarray(P, dtype=np.float64)
    if P.shape[0] == 0:
        return np.zeros((0, 2)), np.zeros(0, dtype=bool)
    Z = P[:, 2]
    valid = Z > 1e-3
    uv = np.zeros((P.shape[0], 2))
    uv[valid, 0] = cam.fx * P[valid, 0] / Z[valid] + cam.cx
    uv[valid, 1] = cam.fy * P[valid, 1] / Z[valid] + cam.cy
    return uv, valid
