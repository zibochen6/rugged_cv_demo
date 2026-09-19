"""Camera model + intrinsics (Phase 10C).

Pinhole camera with optional radial/tangential distortion coefficients.
Coordinate convention (documented, §26):
    +X = right, +Y = down, +Z = forward (camera coordinate system)

Loads K from configs/warning.yaml. If no calibrated values exist, an
ESTIMATED K is produced from focal-length heuristics and every downstream
consumer must label results APPROXIMATE (§23/§44).

Rescaling: if an image is resized by (sx, sy) relative to the calibration
resolution, intrinsics scale as fx*=sx, cx*=sx, fy*=sy, cy*=sy (§41).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import yaml


@dataclass
class Distortion:
    k1: float = 0.0
    k2: float = 0.0
    p1: float = 0.0
    p2: float = 0.0
    k3: float = 0.0

    @property
    def is_zero(self) -> bool:
        return (self.k1, self.k2, self.p1, self.p2, self.k3) == (0, 0, 0, 0, 0)


@dataclass
class CameraModel:
    width: int
    height: int
    fx: float
    fy: float
    cx: float
    cy: float
    distortion: Distortion = field(default_factory=Distortion)
    calibrated: bool = False  # False => estimated K => APPROXIMATE XYZ

    @classmethod
    def from_config(cls, cfg: dict) -> "CameraModel":
        cam = cfg.get("camera", {})
        w = int(cam.get("width", 1280))
        h = int(cam.get("height", 720))
        intr = cam.get("intrinsics") or {}
        dist = Distortion(**(cam.get("distortion") or {}))
        # accept intrinsics only when ALL four are present AND positive
        keys = ("fx", "fy", "cx", "cy")
        if intr and all(k in intr for k in keys) and \
                all(float(intr[k]) > 0 for k in keys):
            return cls(w, h, float(intr["fx"]), float(intr["fy"]),
                       float(intr["cx"]), float(intr["cy"]), dist,
                       calibrated=bool(cam.get("calibrated", True)))
        # estimated fallback (§23): horizontal FOV ~60deg => fx = w/(2*tan30)
        fx = w / (2.0 * np.tan(np.deg2rad(30.0)))
        return cls(w, h, fx, fx, w / 2.0, h / 2.0, dist, calibrated=False)

    def scaled_to(self, width: int, height: int) -> "CameraModel":
        """Intrinsics rescaled for a different image size (§41)."""
        sx = width / float(self.width)
        sy = height / float(self.height)
        return CameraModel(width, height, self.fx * sx, self.fy * sy,
                           self.cx * sx, self.cy * sy, self.distortion,
                           self.calibrated)

    def undistort_points(self, uv: np.ndarray) -> np.ndarray:
        """Iterative undistortion of (N,2) pixel coords -> corrected pixels.

        No-op when distortion coefficients are all zero (§42: interface ready,
        disabled by default)."""
        if self.distortion.is_zero:
            return uv
        d = self.distortion
        x = (uv[:, 0] - self.cx) / self.fx
        y = (uv[:, 1] - self.cy) / self.fy
        x0, y0 = x.copy(), y.copy()
        for _ in range(5):
            r2 = x * x + y * y
            dx = x * (d.k1 * r2 + d.k2 * r2 * r2 + d.k3 * r2**3) + \
                2 * d.p1 * x * y + d.p2 * (r2 + 2 * x * x)
            dy = y * (d.k1 * r2 + d.k2 * r2 * r2 + d.k3 * r2**3) + \
                d.p1 * (r2 + 2 * y * y) + 2 * d.p2 * x * y
            x = x0 - dx
            y = y0 - dy
        out = np.stack([x * self.fx + self.cx, y * self.fy + self.cy], axis=1)
        return out


@dataclass
class DepthCalibration:
    """Linear scale+bias fit from tape-measure samples (§45).

    corrected_m = raw_m * scale + bias. Identity until calibrated."""

    scale: float = 1.0
    bias: float = 0.0

    def apply(self, d: float) -> float:
        return float(d) * self.scale + self.bias

    @classmethod
    def from_config(cls, cfg: dict) -> "DepthCalibration":
        c = (cfg.get("camera", {}) or {}).get("depth_calibration", {}) or {}
        return cls(float(c.get("scale", 1.0)), float(c.get("bias", 0.0)))


def load_camera_model(config_path: str = "configs/warning.yaml") -> CameraModel:
    try:
        with open(config_path) as f:
            cfg = yaml.safe_load(f) or {}
    except FileNotFoundError:
        cfg = {}
    return CameraModel.from_config(cfg)
