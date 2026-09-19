# Phase 13.2–13.15 Gate Reports (consolidated)

Each gate below was committed independently with its tests passing and the
baseline regression intact. Phase 13.1 is reported separately; 13.0 audit in
phase13.0_architecture_audit.md.

---

## Phase 13.2 — Persistent Target Zone Calibration
- Goal: zone as ground-frame geometry + 4-click calibration tool.
- Status: COMPLETE (`app/placement/target_zone.py`, `app/calibrate_target_zone.py`,
  `configs/placement.yaml`).
- Dependencies changed: none.
- Commands: `python app/calibrate_target_zone.py --source usb:0` (interactive)
  or `--pixels "u1,v1;..."` (headless).
- Validation: `tests/placement/test_target_zone.py` 7/7 — axis-aligned &
  rotated fits, click-order invariance, pixel→ground→zone round-trip with
  provenance hashes, multi-preset yaml persistence, above-horizon rejection.
- Regression: baseline untouched, unit gates PASS.
- Known limitations: extrinsics manual-measurement; APPROXIMATE stamp.
- Next: overlay.

## Phase 13.3 — Persistent Zone Projection Overlay
- Goal: draw the calibrated zone into the camera view honestly (§13/§14).
- Changed files: `app/placement/overlay.py`.
- Validation: `tests/placement/test_overlay.py` 4/4 incl. blackout-invariance
  of drawn geometry; out-of-view zones honestly not drawn.
- Design: dashed outline + translucent fill, labeled "(calibrated,
  projected)". V1 all-dashed per §14.

## Phase 13.4 — Cargo Ground Footprint
- Goal: footprint + occlusion-aware smoothing (§16–§19, §78–§79).
- Changed files: `app/placement/cargo_footprint.py`.
- Validation: `tests/placement/test_footprint.py` 10/10 — analytic horizontal
  projection (exact position/yaw), cos(pitch) along-extent documented with
  `length_scale` compensation knob, dims/yaw freeze under low confidence,
  doubled-angle yaw smoothing, rate-limited dims.
- Design note: footprint built from the cuboid axes projected onto the
  ground's horizontal basis (exact yaw, no shear-fit bias).

## Phase 13.5 — Placement Alignment Metrics
- Goal: ΔX/ΔZ/ΔYaw/IoU/inside-ratio/margin/score + hard PASS check (§20–§28).
- Changed files: `app/placement/alignment.py`.
- Validation: `tests/placement/test_alignment.py` 8/8 — perfect placement,
  bigger-zone (inside-ratio is primary, §24), known offsets, half-outside
  (IoU 1/3), 180° yaw symmetry, margin sign, disjoint polygons.
- No Shapely: own Sutherland–Hodgman convex clip (no new deps on Jetson).

## Phase 13.6 — Operator Guidance
- Changed files: `app/placement/guidance.py` (cargo-frame commands, §30).
- Validation: covered in `tests/placement/test_state_machine.py` (directions,
  deadbands, cm/deg formatting).

## Phase 13.7/13.8 — Placement Assist View + Forklift Demo + Fork Geometry
- Goal: product entry point + upgraded right-hand view (§31–§38, §67–§70).
- Changed files: `app/placement/assist_view.py`, `app/forklift_placement_demo.py`,
  `scripts/run_placement_demo.sh`, `configs/target_zone.yaml` (demo preset).
- Validation: headless video smoke GATE PASS (dog.mp4, click, 60 frames,
  11.1 FPS, states flow TRACKING→APPROACHING→ALIGNING→MISALIGNED).
- camera_demo_3d.py NOT modified; Pipeline3D composed as-is (§67).
- Forks drawn as reference rectangles from `placement.yaml forklift:` (§38:
  visualization only, no fork segmentation).
- Keys: L-click cargo / R-click neg / Z zone-calib / B assist / R reset /
  C clear / SPACE pause / Q quit (§72).

## Phase 13.9 — Occlusion Robustness
- Changed files: `tests/placement/test_zone_occlusion.py` (5 gates).
- Results: placement metrics & overlay bitwise-identical under 0/25/50/75/100%
  zone ROI blackout (§40/§77); cargo partial occlusion 10/30/50%: position
  tracks, dims/yaw freeze when confidence < 0.5 (§41/§79); state machine
  identical under blackout; zone-vs-cargo occlusion separation verified (§78).

## Phase 13.10/13.11 — Camera Intrinsics + Ground Extrinsics Calibration
- Changed files: `scripts/calibrate_camera_intrinsics.py`,
  `tests/camera/test_intrinsics_calib.py`.
- Validation: synthetic checkerboard (known K): recovered fx/fy within 0.4%,
  cx/cy within 9 px, RMS 0.30 px reported in yaml (§43). Live capture mode
  for the rig. Ground extrinsics remain manual measurement in
  `configs/placement.yaml` (V1, §44) with honest `calibrated: false`.

## Phase 13.12 — Depth Scale Calibration
- Reuses existing `scripts/calibrate_distance.py` unchanged; writes
  `configs/depth_calibration.yaml`, auto-merged by `load_config()` in BOTH
  demos. Until run on the rig, distances stay APPROXIMATE.

## Phase 13.13 — Placement State Machine
- Changed files: `app/placement/state_machine.py`.
- Validation: `tests/placement/test_state_machine.py` 8/8 — happy path,
  stability requires time AND >=min_stable_frames (§27/§64), blip reset,
  LOST overrides PLACEMENT_OK (§51), drift fallback, hysteresis bands, IDLE
  before tracking.

## Phase 13.14 — Logging
- Changed files: `app/placement/logger.py`; demo writes
  `logs/placement/placement.csv` (§54 fields + occlusion/confidence/FPS).
- Replay: CSV + recorded MP4 (§55); every PASS/FAIL debuggable offline.

## Phase 13.15 — Benchmark
- Changed files: `benchmarks/benchmark_placement.py`,
  `benchmarks/placement_accuracy.md`.
- Results: CORE placement logic (transform+footprint+alignment+SM+guidance+
  smoother) = **0.94 ms < 2 ms budget (§57)**; overlay 3.8 ms, assist view
  1.8 ms rendering. E2E demo measured 11.1 FPS on video smoke (§58: ≥10 kept).
- Accuracy: synthetic geometry chain lossless (0 false PASS / 0 false FAIL
  over 200 scenarios); real-tape-measure table pending Phase 13.16.

## Regression after all phases
- `scripts/run_placement_tests.sh`: 8 suites ALL PASS.
- Existing unit gates (geometry/tracking3d/bev/depth): ALL PASS (unchanged).
- EfficientTAM regression suite: ALL PASS (unchanged).
- Phase 12 live gate usb:0 500 frames: re-run at end of Phase 13 (below).

## Phase 13.16 — Real Forklift Validation: PREPARED, PENDING RIG
Deliverables ready: test matrix A–J in `benchmarks/placement_accuracy.md` §2,
calibration toolchain (intrinsics/extrinsics/depth-scale/zone), APPROXIMATE
labeling, conservative thresholds (§63). Requires the physical forklift +
cargo + floor markings; cannot be executed on the dev bench.
