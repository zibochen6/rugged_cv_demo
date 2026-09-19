# Phase 10B — EfficientTAM Mask + Depth Robust Sampling

Date: 2025-09-01 · Branch: `feat/3d-click-track`

```
Phase:    10B
Status:   PASS
Goal:     Fuse EfficientTAM bool mask with metric depth via robust sampling.
          No XYZ yet.
```

## New files

- `app/geometry/__init__.py`, `app/geometry/depth_sampling.py`
- `app/mask_depth_demo.py` (online tracker + depth sampling demo)
- `tests/geometry/test_depth_sampling.py` (synthetic GPU unit gate)

## Sampling pipeline (§19)

mask(bool@camera res) -> 3x3 GPU erosion (fallback for thin masks) ->
finite/positive/<100m filter -> 20-80 percentile trim -> median.
All on CUDA; only final scalars cross to CPU (§39). bbox-center single pixel
is NOT used (§18).

Outputs per frame: raw median, filtered median, valid pixels, mask area,
valid_ratio, MAD, confidence, valid flag with reason.

## Validation

Synthetic gate: exact median; 10% outliers (999 m) leave filtered median and
MAD untouched; NaN border ring absorbed by erosion; 80% invalid pixels ->
valid=False (no bogus distance); empty mask -> valid=False. GATE: PASS.

Online demo (dog.mp4, seed (640,396) = dog, mask verified visually):
```
valid frames 30/30 (100%)
distance median 4.12 m (3.70..4.48), conf ~0.93, ratio ~0.94
```
NOTE: printed per-frame "smp" ms includes the async tail of depth inference
(no explicit sync between timers); true sampling cost is re-measured with
proper sync in Phase 10I.

## Regression

EfficientTAM baseline: PASS

## Next

Phase 10C — camera intrinsics config (K, distortion fields, estimated-K
fallback clearly labeled APPROXIMATE).
