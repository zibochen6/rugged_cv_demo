"""Camera model + pinhole projection tests for the live rear-warning geometry.

These cover app/geometry/{camera_model,projection}.py, which app/warn_app.py uses
to turn a mask plus a depth value into camera-frame XYZ.
"""
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.geometry.camera_model import CameraModel, Distortion  # noqa: E402
from app.geometry.projection import (  # noqa: E402
    mask_reference_pixel,
    mask_to_xyz,
    pixel_depth_to_xyz,
)


def _cam() -> CameraModel:
    return CameraModel(1280, 720, fx=1000.0, fy=1000.0, cx=640.0, cy=360.0,
                       calibrated=True)


def test_principal_axis_maps_to_zero():
    p = pixel_depth_to_xyz(640, 360, 2.0, _cam())
    assert abs(p.x) < 1e-9 and abs(p.y) < 1e-9 and p.z == 2.0


def test_known_pixel_offset():
    # (u-cx)*Z/fx = 500*2/1000 = 1.0 m
    p = pixel_depth_to_xyz(1140, 360, 2.0, _cam())
    assert abs(p.x - 1.0) < 1e-9


def test_invalid_depth_is_nan_not_zero():
    p = pixel_depth_to_xyz(640, 360, float("nan"), _cam())
    assert not np.isfinite(p.z)


def test_scaled_intrinsics_keep_the_same_physical_point():
    cam = _cam().scaled_to(640, 360)
    p2 = pixel_depth_to_xyz(320 + 250, 180, 2.0, cam)
    assert abs(p2.x - 1.0) < 1e-9, p2


def test_zero_distortion_is_a_noop():
    uv = np.array([[100.0, 200.0], [700.0, 50.0]])
    assert np.allclose(_cam().undistort_points(uv), uv)


def test_barrel_distortion_shifts_pixels():
    camd = CameraModel(1280, 720, 1000, 1000, 640, 360,
                       Distortion(k1=-0.1), calibrated=True)
    uvc = camd.undistort_points(np.array([[840.0, 460.0]]))
    assert not np.allclose(uvc[:, 0], 840.0)


def test_mask_reference_pixel_modes():
    m = np.zeros((100, 100), dtype=bool)
    m[10:90, 40:60] = True
    uc, vc = mask_reference_pixel(m, "centroid")
    assert abs(uc - 50) < 1 and abs(vc - 50) < 1
    ul, vl = mask_reference_pixel(m, "lower_center")
    assert vl > 80 and abs(ul - 50) < 2


def test_mask_to_xyz_end_to_end():
    import torch

    m = np.zeros((100, 100), dtype=bool)
    m[10:90, 40:60] = True
    xyz = mask_to_xyz(torch.from_numpy(m), _cam(), 3.0)
    # centroid (49.5, 49.5): X = (49.5-640)*3/1000
    assert abs(xyz.z - 3.0) < 1e-6
    assert abs(xyz.x - (49.5 - 640.0) * 3.0 / 1000.0) < 1e-6


def test_config_fallback_estimates_intrinsics():
    cm = CameraModel.from_config({"camera": {"width": 1280, "height": 720}})
    assert not cm.calibrated
    expect = 1280 / (2 * math.tan(math.radians(30)))
    assert abs(cm.fx - expect) < 1.0