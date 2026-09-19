# Dual-camera forklift tabletop demo

This is a product demonstration, not a functional-safety or calibrated braking
system. The rear view detects people and shows estimated monocular distance.
The front view verifies a marked model pallet against a marked floor zone. It
does not align forks with pallet pockets.

## Hardware layout

- FRONT_CAMERA_URL is the roof/front camera looking down at all four ground
  tags and the horizontal pallet tag.
- REAR_CAMERA_URL is the rear blind-zone camera. Bind it explicitly; discovery
  order is never used as identity.
- Ground tags 0, 1, 2 and 3 surround the target. Pallet tag 10 is rigidly
  attached horizontally at the pallet center.

Edit configs/marker_placement.yaml so every listed corner is a measured
physical point. Supplied values are an example, not a calibration result.
Mount every marker with the printed page top pointing toward the configured
board +Z direction (forward); rotating one marker invalidates its pose mapping.

## One-time setup

1. Run scripts/setup_apriltag.sh and scripts/setup_person_detector.sh.
2. Run .venv/bin/python scripts/generate_marker_sheets.py. Print at actual
   size and verify the outer black border is 120.0 mm.
3. Calibrate each camera at its operating resolution using
   scripts/calibrate_camera_intrinsics.py. Front completion is disabled without
   calibration; --allow-uncalibrated previews tag visibility only.
4. Set distinct FRONT_CAMERA_URL and REAR_CAMERA_URL in
   configs/two_cameras.env, then run ./start_dual_demo.sh.

Positioning calibration (measured ground/pallet/target geometry) and the
acceptance log live in docs/forklift_dual_demo_field_calibration.md.

Align X, Z and yaw. At WAITING CONFIRMATION, press Enter/Space or click CONFIRM
PLACED. VERIFIED requires a subsequent fresh, still-aligned frame. Missing
tags, wrong ID, frozen results, or excessive reprojection error blocks it.

## Acceptance run

- Rear: record 30 clear human entries covering standing, crossing, approach,
  two people and partial occlusion; then record ten minutes of an empty scene.
- Front: measure 30 independent poses including camera movement. Report P95
  translation and yaw errors. Targets are 5% of pallet short side and 5 degrees.
- Negative cases: 20% position error, wrong ID, tag loss, frozen video and no
  operator confirmation must never produce VERIFIED.
- Dual: target at least 10 fresh updates/s per view, P95 visible warning within
  500 ms, and two hours without sustained memory growth.

These are acceptance targets and must not be reported as achieved before the
physical run.
# Remote camera calibration (no display required)

Start the headless browser calibrator on Jetson:

```bash
.venv/bin/python scripts/calibrate_camera_web.py \
  --source "rtsp://admin:admin@192.168.137.20:554/" \
  --host 0.0.0.0 --port 8090 \
  --board 9x7 --square-mm 25 \
  --target-views 20 --max-rms 2.0 \
  --out configs/camera_calibration.yaml
```

Open `http://192.168.6.84:8090/` from a browser on the same reachable LAN.
Here `9x7` means **9x7 printed black/white squares**, which OpenCV detects as
8x6 inner corners. Measure the physical square edge and replace `25` if needed.

Click `开始自动采集`, then only move the complete board by hand. Pause briefly
at varied left/right, top/bottom, near/far, rotated and tilted poses. The page
rejects static duplicates, boards that are too small or touch an image edge,
and mixed video resolutions. It shows pose coverage and automatically solves
after 20 diverse views. A finite result with RMS <= 2.0 px is atomically saved
to `configs/camera_calibration.yaml`; a failed attempt never overwrites the old
file and asks for more varied poses. `重新开始` clears only in-memory samples.

The service detects a frozen stream and reconnects after read failures. Stop it
with `Ctrl-C` in the SSH terminal.
