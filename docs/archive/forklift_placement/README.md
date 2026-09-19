# Forklift Cargo Placement Assist — Workflow

Product mode built on top of the generic Click-to-Track-in-3D baseline
(see `docs/3d_click_track/`). The generic demo is untouched; this is a
separate product entry composed from it.

> **Monocular AI Placement Assistance — NOT a safety-certified measurement
> system (§48).** Until all calibrations are done the UI shows APPROXIMATE.

## Coordinate conventions
- Camera frame (existing): +X right, +Y down, +Z forward.
- Ground/forklift frame (placement): +X right, +Y up, +Z forward, origin =
  camera projected onto ground, plane Y=0. All placement math in (X, Z).
- Yaw: measured from +Z (forward) toward +X (right), normalized (-90°, 90°]
  (rectangle 180° symmetry).

## One-time setup (per camera mount!)
1. **Mount the camera** pointing forward/down at the placement area.
   ⚠ If the mount position/angle ever changes, redo steps 2–4 (§53).
2. **Intrinsics** (Phase 13.10): checkerboard, ≥8 varied views:
   ```
   .venv/bin/python scripts/calibrate_camera_intrinsics.py --source usb:0
   ```
   → `configs/camera_calibration.yaml` (with RMS reprojection error).
3. **Ground extrinsics** (Phase 13.11): measure camera height (tape) and
   pitch (inclinometer), enter in `configs/placement.yaml`
   (`camera_extrinsics:`), set `calibrated: true` once verified.
4. **Depth scale** (Phase 13.12): targets at 1/2/3/4 m:
   ```
   .venv/bin/python scripts/calibrate_distance.py --source usb:0 --distances 1,2,3,4
   ```
5. **Target zone** (Phase 13.2): park so the zone is visible, click its 4
   corners:
   ```
   .venv/bin/python app/calibrate_target_zone.py --source usb:0
   ```
   → `configs/target_zone.yaml` (ground-frame polygon; multi-preset ready).

## Running
```
./scripts/run_placement_demo.sh                       # usb:0 + UI
# or
python app/forklift_placement_demo.py --source usb:0 \
    --camera-config configs/camera_calibration.yaml \
    --placement-config configs/placement.yaml
```
Product UI (Phase 14): single window with header + camera (65%) |
placement assist (35%) + full-width action bar. Technical telemetry stays
computed and logged; press **D** to see it.

```
Keys:  left click   Select cargo
       right click  Negative prompt
       Z            Edit target zone (first run: 4-corner wizard)
       B            Toggle placement assist view
       D            Debug info (2D/Depth/3D, XYZ, dX/dZ/dYaw, IoU, score,
                    FPS — hidden by default, never deleted)
       H            Help
       R            Reset cargo   SPACE pause   Q/ESC quit
```
If `configs/target_zone.yaml` already holds a valid zone, calibration is
skipped: the UI shows `ZONE READY` + “Click cargo to begin”.

## How placement works (and why occlusion is safe)
- Target zone = pre-calibrated **persistent ground geometry** (§92). Forks,
  pallet or cargo can cover 100% of it — placement keeps computing; the
  dashed polygon you see is the projection of calibrated geometry, NOT a
  live detection (§13/§14).
- Cargo: click → EfficientTAM mask → metric depth → cuboid → ground
  footprint. Fork occlusion of the CARGO shrinks the visible mask: position
  keeps updating, dimensions/yaw FREEZE when confidence drops (§79), and
  partial occlusion never flips tracking to LOST (§19).
- Verdict: ΔX/ΔZ/ΔYaw + cargo-inside-ratio + edge margin all within
  tolerances for ≥ `stable_time_s` AND ≥ `min_stable_frames` consecutive
  frames → `PLACEMENT_OK` (§27/§64). Thresholds are conservative: a false
  PASS is worse than a slow PASS (§63).

## Known limitations / future work
- **Moving forklift vs fixed world zone**: MVP assumes the camera↔zone
  relation stays fixed during one placement maneuver (§81–§83). A moving
  vehicle needs markers/SLAM/odometry — not solved, not hidden (§82).
  `TargetZoneProvider` is the extension point for marker-based zones (§80).
- Along-extent shrinks ~cos(pitch) in the monocular cuboid model; compensate
  via `footprint.length_scale` after on-vehicle measurement.
- Accuracy: ±10 cm demo-grade until full calibration; never claimed better
  (§46).
- Forks are drawn from config geometry only — no fork segmentation (§38).

## Tests & gates
```
./scripts/run_placement_tests.sh        # placement unit suite
./scripts/run_regression.sh             # EfficientTAM baseline (mandatory)
.venv/bin/python app/phase12_gate.py --source usb:0 --frames 500
benchmarks/benchmark_placement.py       # latency gate (<2 ms core)
```
