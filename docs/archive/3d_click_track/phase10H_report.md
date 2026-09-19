# Phase 10H — Real-Time 3D Click-to-Track Integration

Date: 2025-09-01 · Branch: `feat/3d-click-track`

```
Phase:    10H
Status:   PASS
Goal:     Single demo: click -> mask -> depth -> XYZ -> Kalman -> trajectory
          -> BEV, serial first version (§35).
```

## New / changed files

- `app/camera_demo_3d.py` (new; serial loop, CSV logging, --debug, --repeat soak)
- `scripts/run_camera_3d.sh` (new launcher)
- `app/tracking3d/kalman.py` (fix: velocity-scaled gate, speed clamp, re-anchor)

## Behavior

- Controls identical to existing demo (§33) + right-click negative point.
- Depth runs every `depth.frequency` frames (default 2 after 10I bench).
- Mask lost (area <0.05% or >60%) or invalid depth => predict-only Kalman,
  confidence decay, no Z=0 (§58/§59). UI shows TRACKING LOST.
- Every frame logged to `logs/3d_pipeline/trajectory.csv` (§65).
- `--debug` adds raw-vs-filtered Z, depth heatmap, raw-measurement cross in BEV
  (§28/§66).
- Un-calibrated K => `[APPROXIMATE: estimated K]` on every XYZ line (§23/§44).

## Validation (headless gate)

```
video dog.mp4, click (640,396), 60 frames:
valid_3d=60/60  e2e_fps=15.1 (async-timing; see 10I for synced numbers)
GATE: PASS
```

Kalman divergence found & fixed here: raw dog depth 4.8->8.1 m (real retreat)
made vz overshoot to 4.2 m/s and extrapolation ran away while rejecting good
measurements. Fixed with per-step jump limit v_max*dt+0.5, |v|<=3 m/s clamp,
and forced re-anchor after 5 consecutive rejects (unit-tested).

## Regression

EfficientTAM baseline: PASS

## Next

Phase 10I — synced-timing benchmark + configuration decision (depth freq 2,
fp32 online tracking).
