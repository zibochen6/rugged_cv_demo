"""Ground filtering via expected ground depth (§14).

With a level-view camera at KNOWN height/pitch/intrinsics, every pixel has a
theoretically expected depth IF that pixel sees the flat ground:

    expected_depth(u,v) = ray-plane intersection distance (camera Z, meters)

An obstacle is a pixel whose PREDICTED depth is significantly CLOSER than the
expected ground depth at that pixel (pred < exp * (1 - tolerance_ratio)).
"Significantly farther" is NOT treated as an obstacle (spec §14: only the
closer-than-ground rule; far/illuminated errors are handled by range ratio and
temporal voting downstream).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.geometry.camera_model import CameraModel
from app.geometry.ground_plane import GroundExtrinsics


@dataclass
class GroundFilterResult:
    expected_m: np.ndarray   # HxW expected ground depth (NaN = pixel above horizon)
    ground_mask: np.ndarray  # HxW bool: predicted ≈ expected (this is ground)
    obstacle_cand: np.ndarray  # HxW bool: predicted significantly closer
    has_georef: bool           # False when extrinsics/intrinsics uncalibrated


class ExpectedGroundDepthFilter:
    """Vectorized expected-depth ground filter."""

    def __init__(self, cam: CameraModel, tolerance_ratio: float = 0.20,
                 enabled: bool = True, extr_from: dict | None = None,
                 extr=None) -> None:
        """cam: intrinsics model. extr: GroundExtrinsics (direct).
        extr_from: config dict with camera_mount (height/pitch...) used to
        build the extrinsics when extr is not passed directly."""
        self.cam = cam
        if extr is not None:
            self.extr = extr
        elif extr_from is not None:
            from app.geometry.ground_plane import GroundExtrinsics as GE
            e = (extr_from or {}).get("camera_mount") or {}
            self.extr = GE(
                height_m=float(e.get("height_m", 1.5)),
                pitch_deg=float(e.get("pitch_deg", 25.0)),
                roll_deg=float(e.get("roll_deg", 0.0)),
                yaw_deg=float(e.get("yaw_deg", 0.0)),
                calibrated=bool(e.get("calibrated", False)),
            )
        else:
            from app.geometry.ground_plane import GroundExtrinsics as GE
            self.extr = GE(height_m=1.5, pitch_deg=25.0)
        self.tolerance_ratio = float(tolerance_ratio)
        self.enabled = enabled
        self._expected: np.ndarray | None = None     # cached per resolution
        self.has_georef = True

    def expected_ground_depth(self, h: int, w: int) -> np.ndarray:
        """(H,W) expected camera-forward depth (m) of the ground plane per
        pixel; NaN where the ray does not intersect the ground ahead."""
        if self._expected is not None and self._expected.shape[:2] == (h, w):
            return self._expected
        # pixel grid
        u, v = np.meshgrid(np.arange(w, dtype=np.float64),
                           np.arange(h, dtype=np.float64))  # each HxW
        uv = np.stack([u.ravel(), v.ravel()], axis=1)       # (N,2)
        uv_ud = self.cam.undistort_points(uv)
        ray = np.stack([
            (uv_ud[:, 0] - self.cam.cx) / self.cam.fx,
            (uv_ud[:, 1] - self.cam.cy) / self.cam.fy,
            np.ones(uv_ud.shape[0]),
        ], axis=1)                                          # (N,3)
        rg = ray @ self.extr.M.T                            # ray in ground frame
        y = rg[:, 1]
        lam = np.where(y < -1e-9, -self.extr.t[1] / y, np.nan)
        # depth along camera Z = lam * ray_z(=1)
        expected = lam.reshape(h, w).astype(np.float32)
        self._expected = expected
        return expected

    def apply(self, depth_m: np.ndarray, mask: np.ndarray | None = None
              ) -> GroundFilterResult:
        """depth_m: HxW predicted meters (NaN invalid). mask: validity."""
        h, w = depth_m.shape
        if not self.enabled or not self.has_georef:
            blank = np.zeros((h, w), dtype=bool)
            return GroundFilterResult(
                expected_m=np.full((h, w), np.nan, dtype=np.float32),
                ground_mask=blank, obstacle_cand=blank, has_georef=False)
        exp = self.expected_ground_depth(h, w).astype(np.float32)
        valid = np.isfinite(exp) & np.isfinite(depth_m)
        if mask is not None:
            valid &= mask.astype(bool)
        tol = self.tolerance_ratio
        ground = valid & (depth_m >= exp * (1.0 - tol))   # ≈ or farther
        obstacle = valid & (depth_m < exp * (1.0 - tol))  # closer than ground
        return GroundFilterResult(
            expected_m=exp, ground_mask=ground,
            obstacle_cand=obstacle, has_georef=True)


def expected_ground_from_config(cam: CameraModel,
                                cfg_dict: dict) -> ExpectedGroundDepthFilter:
    """Build the filter from a WarnConfig-style merged dict."""
    gf = (cfg_dict.get("ground_filter") or {})
    return ExpectedGroundDepthFilter(
        cam,
        tolerance_ratio=float(gf.get("tolerance_ratio", 0.20)),
        enabled=bool(gf.get("enabled", True)),
        extr_from=cfg_dict)