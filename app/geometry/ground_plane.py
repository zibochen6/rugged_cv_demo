"""Ground / Forklift coordinate system + camera-ground extrinsics (Phase 13.1).

Frames
------
Camera frame C (existing convention, app/geometry/camera_model.py):
    +X = right, +Y = down, +Z = forward, origin = optical center.

Ground / forklift frame G (NEW, this module):
    +X = right (lateral), +Y = up (height), +Z = forward (horizontal).
    Origin: camera position projected onto the ground plane.
    Ground plane: Y = 0.

Handedness note: C is right-handed (right x down = forward). G uses
(right, up, forward), so the C->G coordinate map is an ORTHONORMAL map with
determinant -1 (a roto-reflection). Distances and angles are preserved;
all 2D placement logic operates on (X, Z) = (right, forward) only, so no
handedness subtlety leaks into the alignment math. We therefore build the
transform by tracking the camera BODY axes as vectors in G-space under
physical rotations, instead of composing "rotation matrices".

Extrinsic parameters (manual measurement first, calibration tool later):
    height_m : camera height above ground, meters
    pitch_deg: positive = looking DOWN from horizontal
    roll_deg : positive = right side of camera tilts DOWN (clockwise,
               viewed from behind the camera)
    yaw_deg  : positive = optical axis turned to the RIGHT (about up axis)

Application order on the nominal pose (pointing forward, level):
    yaw (about world up) -> pitch (about body lateral axis) ->
    roll (about optical axis).

Core API
--------
    GroundExtrinsics.from_config(cfg)   # reads cfg["camera_extrinsics"]
    camera_point_to_ground(p_c)         # (N,3) or (3,) cam  -> ground
    ground_point_to_camera(p_g)         # inverse
    pixel_to_ground(u, v, cam)          # ray-plane intersection -> (x, z) or None
    ground_to_pixel(pts_g, cam_model)   # ground (N,2|N,3) -> pixels + valid
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

import numpy as np

from app.geometry.camera_model import CameraModel
from app.geometry.projection import project_points

# Ground frame unit axes, expressed in ground coordinates.
_UP = np.array([0.0, 1.0, 0.0])


def _rot_axis_angle(v: np.ndarray, axis: np.ndarray, ang: float) -> np.ndarray:
    """Rotate vector v about unit axis by ang (rad), Rodrigues."""
    c, s = np.cos(ang), np.sin(ang)
    return v * c + np.cross(axis, v) * s + axis * np.dot(axis, v) * (1.0 - c)


@dataclass
class GroundExtrinsics:
    """Camera pose relative to the ground plane (see module docstring)."""

    height_m: float
    pitch_deg: float = 0.0
    roll_deg: float = 0.0
    yaw_deg: float = 0.0
    calibrated: bool = False

    def __post_init__(self) -> None:
        self.height_m = float(self.height_m)
        # camera body axes in ground coordinates, nominal: level, forward
        xc = np.array([1.0, 0.0, 0.0])
        yc = np.array([0.0, -1.0, 0.0])
        zc = np.array([0.0, 0.0, 1.0])
        yaw = np.deg2rad(self.yaw_deg)
        pitch = np.deg2rad(self.pitch_deg)
        roll = np.deg2rad(self.roll_deg)
        # 1) yaw about world up (positive => look right)
        xc = _rot_axis_angle(xc, _UP, yaw)
        yc = _rot_axis_angle(yc, _UP, yaw)
        zc = _rot_axis_angle(zc, _UP, yaw)
        # 2) pitch about body lateral axis (positive => look down)
        yc = _rot_axis_angle(yc, xc, pitch)
        zc = _rot_axis_angle(zc, xc, pitch)
        # 3) roll about optical axis (positive => right side down)
        xc = _rot_axis_angle(xc, zc, roll)
        yc = _rot_axis_angle(yc, zc, roll)
        # columns = camera axes in ground coords; orthonormal, det = -1
        self.M = np.stack([xc, yc, zc], axis=1)
        # camera origin in ground coords (camera sits at height h, x=z=0)
        self.t = np.array([0.0, self.height_m, 0.0])

    # -- transforms ---------------------------------------------------------
    def camera_point_to_ground(self, p_c) -> np.ndarray:
        """Camera-coord point(s) -> ground coords. (N,3) or (3,) in/out."""
        P = np.asarray(p_c, dtype=np.float64)
        single = P.ndim == 1
        P = np.atleast_2d(P)
        G = P @ self.M.T + self.t
        return G[0] if single else G

    def ground_point_to_camera(self, p_g) -> np.ndarray:
        """Ground-coord point(s) -> camera coords. Inverse of the above."""
        P = np.asarray(p_g, dtype=np.float64)
        single = P.ndim == 1
        P = np.atleast_2d(P)
        C = (P - self.t) @ self.M  # M orthonormal => M^-1 = M^T
        return C[0] if single else C

    def camera_ray_to_ground(self, ray_c: np.ndarray):
        """Camera-frame ray direction -> intersection with ground plane.

        Returns ground (3,) point or None if the ray never hits Y=0 ahead."""
        r_g = self.M @ np.asarray(ray_c, dtype=np.float64)
        if abs(r_g[1]) < 1e-12:
            return None
        lam = -self.t[1] / r_g[1]
        if lam <= 0.0:
            return None
        return self.t + lam * r_g

    def pixel_to_ground(self, u: float, v: float, cam: CameraModel):
        """Pixel -> ground (x, z) via undistorted pinhole ray / plane hit.

        Returns (x, z) tuple or None when the ray points above the horizon
        (no forward ground intersection)."""
        uv = cam.undistort_points(np.array([[float(u), float(v)]],
                                           dtype=np.float64))[0]
        ray_c = np.array([(uv[0] - cam.cx) / cam.fx,
                          (uv[1] - cam.cy) / cam.fy, 1.0])
        p = self.camera_ray_to_ground(ray_c)
        if p is None:
            return None
        return (float(p[0]), float(p[2]))

    def ground_to_pixel(self, pts_g, cam: CameraModel):
        """(N,2)[x,z] or (N,3) ground points -> ((N,2) pixels, (N,) valid).

        2D input is lifted onto the ground plane (Y=0)."""
        P = np.asarray(pts_g, dtype=np.float64)
        if P.ndim == 1:
            P = P[None, :]
        if P.shape[1] == 2:
            P = np.column_stack([P[:, 0], np.zeros(len(P)), P[:, 1]])
        return project_points(self.ground_point_to_camera(P), cam)

    # -- provenance ----------------------------------------------------------
    def hash(self) -> str:
        """Stable short hash of the extrinsic parameters (zone versioning)."""
        s = f"h={self.height_m:.6f};p={self.pitch_deg:.6f};" \
            f"r={self.roll_deg:.6f};y={self.yaw_deg:.6f}"
        return hashlib.sha256(s.encode()).hexdigest()[:16]

    @classmethod
    def from_config(cls, cfg: dict) -> "GroundExtrinsics":
        """Read cfg["camera_extrinsics"]. Missing height/pitch => NOT usable:
        raises ValueError so callers surface a clear 'not calibrated' state."""
        e = (cfg or {}).get("camera_extrinsics", {}) or {}
        h, p = e.get("height_m"), e.get("pitch_deg")
        if h is None or p is None:
            raise ValueError(
                "camera_extrinsics.height_m / pitch_deg not set — run ground "
                "extrinsic calibration (manual measurement first)")
        return cls(height_m=float(h), pitch_deg=float(p),
                   roll_deg=float(e.get("roll_deg", 0.0)),
                   yaw_deg=float(e.get("yaw_deg", 0.0)),
                   calibrated=bool(e.get("calibrated", False)))
