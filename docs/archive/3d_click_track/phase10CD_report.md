# Phase 10C/10D — Camera Intrinsics + Pixel/Depth -> XYZ

Date: 2025-09-01 · Branch: `feat/3d-click-track`

```
Phase:    10C + 10D
Status:   PASS
Goal:     Config-driven pinhole camera model (K + distortion interface,
          estimated-K fallback) and mask+depth -> XYZ back-projection.
```

## New files

- `configs/3d_pipeline.yaml` (full 3D pipeline config, §63)
- `app/geometry/camera_model.py` (K, distortion, scaled_to, undistort_points)
- `app/geometry/projection.py` (pinhole back-projection, reference pixels)
- `app/xyz_demo.py` (tracker + depth + K -> XYZ on dog.mp4)
- `tests/geometry/test_camera_projection.py`

## Key rules honored

- Coordinate system documented: +X right, +Y down, +Z forward (§26)
- No intrinsics in config => estimated K, `calibrated=False`, every output line
  tagged "(APPROXIMATE: estimated K)" (§23/§44)
- `scaled_to()` rescales fx/fy/cx/cy for resized images (§41)
- Distortion interface implemented (iterative undistort), zero by default (§42)
- Reference pixel baseline = mask centroid (§25); lower_center available
- Invalid depth -> NaN XYZ, never Z=0 (§58)

## Validation

Unit gate PASS (principal axis, 1.0 m offset, rescale equivalence, distortion
no-op/non-zero, reference pixels, config fallback).

Online demo (dog.mp4): valid 30/30; Z 3.70-4.48 m; X ≈ -0.2 m, Y ≈ +0.3 m;
XYZ std over clip [0.04, 0.03, 0.22] m (dog mostly stationary) -> stable.

## Regression

EfficientTAM baseline: PASS

## Next

Phase 10E — depth gating + 3D Kalman (raw vs filtered kept separate).
