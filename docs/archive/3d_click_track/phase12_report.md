# Phase 12 — Live 3D Tracking Correctness

Date: run on Jetson AGX Orin (JetPack 6.2.1), repo branch `feat/3d-click-track`.

## Problem (real-camera screenshot)

Simultaneously visible in one frame:
- EfficientTAM mask OK
- `TRACKING LOST`
- UI `Est.Distance = -1.74m` (negative Z)
- BEV target gone / frozen, no valid 3D cuboid on RGB

## Root cause

Three state layers were conflated into one `lost` flag, and the Kalman
constant-velocity extrapolation ran unbounded while measurements were lost:

1. mask/depth invalid -> `z_meas=None` -> `ObjectTrack3D.step()` predicted
   (constant velocity) every frame forever.
2. UI printed filtered (predicted) Z as `Est.Distance` whenever `pos` was not
   None -> drifted to negative Z and was displayed as a valid distance.
3. trajectory appended on every frame (`traj.add` whenever pos non-None) ->
   BEV target kept moving while LOST.
4. `BoxSmoother` held the last cuboid indefinitely: stale box anywhere /
   skipped by the behind-camera safety check -> no box.

## Fix

### New: `app/tracking3d/status.py` (pure, CPU-only)
- `ObjectStatus3D`: `two_d` (TRACKING|LOST) / `depth` (VALID|INVALID) /
  `three_d` (UPDATED|PREDICTED|STALE).
- Displayed `distance_m` comes ONLY from a real measurement; `None` => "N/A".
- `display_pos()`: only `3D_UPDATED` + finite + `Z > 0`. Any `Z<=0` state is
  never exposed to UI/BEV/CSV (§R2/§R3).

### `app/tracking3d/kalman.py` (ObjectTrack3D)
- Physical gate: measurement must be finite, `Z > 0` and inside
  `[min_depth_m, max_depth_m]`; physically invalid measurements behave like a
  missing measurement (never update, never re-anchor).
- Bounded lost window: `max_pred_frames` (default 3) predict-only frames with
  `vel_decay` (default 0.5) per frame; predicted `Z<=0` clamps+freezes.
- Exceeding the window FREEZES the track: velocity zeroed, position held
  (`3D_STALE`); no further extrapolation.
- A new valid measurement un-freezes via re-anchor (reacquire).
- `step()` result gains `status`, `frozen`, `n_invalid`; old keys unchanged.

### `app/camera_demo_3d.py` (Pipeline3D + UI)
- Config `tracking.min_depth_m` (0.3) / `max_depth_m` (10.0) /
  `max_pred_frames` (3) / `vel_decay` (0.5); CLI `--min-depth/--max-depth`.
- Trajectory appends ONLY on `3D_UPDATED`; BEV `current` only from
  measurement-backed display pos; cuboid fit only on depth-VALID frames,
  smoother reset when `3D_STALE`, cuboid shown only on valid positive-Z
  state; `draw_cuboid` behind-camera safety check untouched.
- UI: `2D: … Depth: … 3D: …` status line + `Distance: N/A` when invalid;
  CSV gains `status2d/status_depth/status3d/distance` columns.
- `reset_track()` rebuilds the Kalman with the SAME configured gates.

## Verification

### Unit (CPU)
`tests/tracking3d/test_status_gates.py`:
2D/Depth layer mapping; negative/zero/out-of-range distance => INVALID;
PREDICTED x3 then STALE frozen xN; velocity decays then exactly 0;
frozen position held (no drift); negative-Z clamp; reacquire un-freezes;
trajectory-append rule (UPDATED only). PASS.

### Old regressions
- `scripts/run_regression.sh` (model load / image prompt / video propagation): ALL PASS
- tests/geometry/* : PASS
- tests/bev/* : PASS
- tests/tracking3d/* (incl. unchanged test_kalman.py): PASS

### Deterministic fault injection (same pipeline code path, video source)
`app/phase12_gate.py --source video:dog.mp4 --frames 200 --inject-lost 10@100`
- frames=200 valid_track=163 neg_z=0 cuboid_vis=100.0% bev_vis=100.0%
- lost_streaks=1, traj_grew_while_lost=0, reacquire_ok=1, fps=12.1
- GATE: PASS (all of G1-G6 + G5b)
- CSV trace around the injected loss:
  `... UPDATED(dist 3.391 Z 3.402) -> PREDICTED x3 (no dist, no Z)
   -> STALE (traj_len frozen at 73) -> reacquire UPDATED(dist 3.533)`.

### Real camera (usb:0, 640x480) — 500-frame continuous run
- G1 500 consecutive frames: PASS
- G2 negative-Z displays = 0: PASS
- G3 cuboid visible while tracking valid: 100.0% (>90%) — PASS
- G4 BEV footprint while tracking valid: 100.0% (>90%) — PASS
- G5 + G5b: trajectory froze during injected LOST (8 frames @250) and during
  a natural LOST streak (frames ~450+); zero appends while lost; resumed on
  reacquire — PASS
- G6 FPS >= 10: 16.4 FPS P50 — PASS
- valid_track = 434 / 500 frames
- Full gate result: **GATE: PASS** (with `--inject-lost 8@250`)

Earlier runs on the same box showed 652/666 frames 2D-LOST. Root cause
(diagnosed via the run log): the lab camera's auto-exposure overexposed the
scene (mean RGB ~204, 22% pixels clipped at 255) — texture was washed out and
EfficientTAM could only return degenerate ~99.5% full-frame masks. Fix:
`open_source(..., exposure=...)` now forces V4L2 manual exposure (default
`--exposure manual` in the 3D demo and gate; `-exp auto` restores camera AE).
With sane exposure (mean ~147, 0.6% clipped) live tracking works:
15/15 and 434/500 frames UPDATED in the two live runs.

## Red lines honored

- EfficientTAM / PyTorch / CUDA environment untouched.
- `_C` extension not rebuilt (still absent / GPU postproc skip warning unchanged).
- All old regressions PASS.
- No performance / TensorRT / calibration work in this phase.

## Next phases (as agreed)

1. Real camera calibration: fx/fy/cx/cy/distortion.
2. 1/2/3/4 m tape-measure depth scale calibration.