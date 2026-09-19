# Phase 13.1 — Ground Coordinate System

Phase: 13.1
Goal: Establish the Ground/Forklift coordinate frame and the camera↔ground
      extrinsic transform that all placement geometry builds on.
Status: COMPLETE

Changed files:
  app/geometry/ground_plane.py              (new, ~170 LOC)
  tests/placement/test_ground_plane.py      (new, 12 tests)
  docs/forklift_placement/phase13.1_report.md (this file)

Dependencies changed: NONE

Design decisions:
  • Ground frame: +X right, +Y up, +Z forward; origin = camera projected onto
    the ground; ground plane Y=0. (X,Z) matches the existing camera/BEV
    (right, forward) convention so no existing code's coordinates change.
  • Camera frame (right, down, forward) is right-handed, ground (right, up,
    forward) has opposite handedness => the C->G map is orthonormal with
    det=-1. Implemented as tracked camera BODY axes under physical rotations
    (axis-angle), not matrix composition — immune to convention bugs.
  • Extrinsics parameters: height_m, pitch_deg (down+), roll_deg (right-side-
    down+), yaw_deg (right+); applied yaw -> pitch -> roll.
  • GroundExtrinsics.hash() for zone provenance versioning (§76).
  • from_config() raises on missing height/pitch => honest "not calibrated".

Commands:
  .venv/bin/python tests/placement/test_ground_plane.py

Validation:
  • 12/12 tests PASS: round-trip identity, rigidity (distance preservation),
    pitch/yaw known-value checks, pixel->ground ray intersection
    (principal pixel at h/tan(pitch)), lateral mirror symmetry,
    above-horizon -> None, ground->pixel->ground polygon round-trip,
    hash stability, config error path.

Regression: ALL PASS (10 unit gates incl. new placement test +
  EfficientTAM regression suite 3/3). No existing file was modified.

Performance:
  • camera<->ground round-trip for 1000 points: ~42 µs
  • ground zone polygon -> image projection: ~43 µs steady-state
  => full ground-transform + alignment budget (< 2 ms, §57) has ~40x headroom.

Known limitations:
  • Extrinsics are still manual-measurement only (`calibrated: false` until a
    tape measure / calibration tool provides them) — APPROXIMATE labeling
    stays on (§47).
  • No automatic camera-mount-change detection (§53, future work).

Next: Phase 13.2 — persistent Target Zone data model + interactive
      calibration tool (app/calibrate_target_zone.py).
