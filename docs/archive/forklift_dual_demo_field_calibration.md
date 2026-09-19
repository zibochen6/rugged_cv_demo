# Front Camera Localization Calibration — Field Worksheet

Companion to `docs/forklift_dual_demo.md`. This is the hands-on record for
"positioning calibration" of the front (roof-mounted, ground-looking)
camera: the ground coordinate frame, the ground-board tags, the pallet tag,
and the target zone are pinned down by physical measurement and written into
`configs/marker_placement.yaml`. The front camera pose itself is solved each
frame by PnP from the ground board, so **no camera extrinsics/hand-eye
calibration is needed** — measure once, verify, then move the camera freely.

Units: metres. Ground frame: **+X right, +Y up, +Z forward**. Y=0 is the
floor plane. Every marker page top must face the +Z of the board it is used
for.

## 0. Prerequisites

- [ ] `configs/camera_calibration.yaml` exists and `calibrated: true`
      (front camera, RMS ≤ 2.0 px; runtime stream must stay 2304×1296).
- [ ] `generated_assets/forklift_markers.pdf` printed at actual size
      (tags 0, 1, 2, 3, 10).
- [ ] Front camera fixed at operating position looking down at the target
      area; all four ground tags and the pallet tag fit in view.

## 1. Print verification

Measure the outer black border of each printed tag.

| Tag | Border (mm, target 120.0) | Page top orientation | OK |
|-----|----------------------------|----------------------|----|
| 0   |                            | +Z (forward)         | ☐  |
| 1   |                            | +Z (forward)         | ☐  |
| 2   |                            | +Z (forward)         | ☐  |
| 3   |                            | +Z (forward)         | ☐  |
| 10  |                            | pallet +Z            | ☐  |

If any border deviates from 120.0 mm by more than ~1 %, fix the print scale
or regenerate with `scripts/generate_marker_sheets.py --size-mm <actual>`.

## 2. Ground frame definition

- Origin O: centre of the target zone (reference layout: zone centre at
  (X=0, Z=2.1)).
- Axes drawn on the floor (tape/laser cross): +X right, +Z forward.
- All measurements in steps 3–5 use this one frame.

## 3. Ground tags 0–3

Layout blueprint (reference): tags form a rectangle around the zone,
nearrow Z ≈ 1.24–1.36, far row Z ≈ 2.84–2.96, X ≈ ±(0.64–0.76). You do not
need to reproduce these numbers — measure wherever you place each tag.

Corner order in `corners_m` (pupil-apriltags detection order):
**[bottom-left, bottom-right, top-right, top-left]**; "top" = page top = +Z.

Target measurement precision: ≤ 1 cm (acceptance tolerance is 5 cm).
Measure each tag twice (two rulers / two people) and keep the difference
≤ 1 cm.

| Tag | BL (x, y, z) | BR (x, y, z) | TR (x, y, z) | TL (x, y, z) | double-check Δ |
|-----|---------------|---------------|---------------|---------------|----------------|
| 0   | ( , 0, )      | ( , 0, )      | ( , 0, )      | ( , 0, )      |                |
| 1   | ( , 0, )      | ( , 0, )      | ( , 0, )      | ( , 0, )      |                |
| 2   | ( , 0, )      | ( , 0, )      | ( , 0, )      | ( , 0, )      |                |
| 3   | ( , 0, )      | ( , 0, )      | ( , 0, )      | ( , 0, )      |                |

Write the measured values (in metres, 3 decimals) into
`configs/marker_placement.yaml` → `ground_board.tags.{0..3}.corners_m`,
replacing the example values.

## 4. Pallet tag 10

Tag 10 sits horizontally, rigidly fixed at the pallet centre. Pallet frame:
origin = pallet centre projected to the floor, +X right, +Y up, +Z forward.

| Item                             | Value                                |
|----------------------------------|--------------------------------------|
| Tag plane height (Y)             | (reference 0.15)                     |
| Tag corners BL / BR / TR / TL    | ( , , ) / ( , , ) / ( , , ) / ( , , )|
| Pallet width_m / length_m        | / (reference 1.0 × 1.2)              |

Target measurement precision: ≤ 1 cm. Write into `pallet_board.tags.10.corners_m`
and `pallet.width_m/length_m`.

## 5. Target zone

Rectangle on the floor, in the ground frame. Reference: 1.1 m (X) × 1.3 m
(Z), Z from 1.45 to 2.75, centred at X=0.

| Corner | (x, z) |
|--------|--------|
| 1      | ( , )  |
| 2      | ( , )  |
| 3      | ( , )  |
| 4      | ( , )  |

Write into `target_zone.corners_m` (format `[[x1,z1],[x2,z2],...]`; the
example order matters: it defines the zone polygon).

## 6. Config review (before first run)

- [ ] `ground_board.tags` contains only measured values (no example numbers).
- [ ] `pallet_board.tags.10.corners_m` matches the physical tag.
- [ ] `target_zone.corners_m` matches the physical zone.
- [ ] `require_calibrated_intrinsics: true`; `max_reprojection_error_px: 3.0`
      and placement tolerances left at defaults.

## 7. Smoke test (front app alone; dual launcher needs the rear link)

```bash
.venv/bin/python -u app/marker_placement_app.py \
  --source "rtsp://admin:admin@192.168.137.20:554/" \
  --config configs/marker_placement.yaml \
  --camera-config configs/camera_calibration.yaml
```

Use `--headless` for console-only checks (state, tag count, fps printed
every 30 frames).

- [ ] Tags 0–3 and 10 all detected with correct IDs.
- [ ] Ground and pallet reprojection error < 3 px (aim < 2 px).
- [ ] Known-offset check: move the pallet +20 cm right — the assist view
      reports ~0.20 m lateral error in the right direction.
- [ ] Camera-move invariance (R3): nudge/shake the camera — zone and pallet
      stay fixed in the floor frame, nothing jumps.
- [ ] Full path: SEEKING → APPROACH → ALIGN → **WAITING_CONFIRMATION** →
      Enter/Space or button → **VERIFIED** (requires one more fresh aligned
      frame).
- [ ] Update rate ≥ 10 FPS.

## 8. Negative cases (AC4 — must NEVER reach VERIFIED)

| Case                                | Expected | Observed |
|-------------------------------------|----------|----------|
| Cover one ground tag                | LOST     | ☐        |
| Pallet tag removed                  | LOST     | ☐        |
| Wrong-ID tag in view                | LOST     | ☐        |
| Frozen stream / pulled cable        | LOST     | ☐        |
| Pallet offset > 20 % of short side  | not VERIFIED | ☐   |
| No operator confirmation            | not VERIFIED | ☐   |

## 9. Acceptance log (AC5)

30 independent poses including camera movement. Ground truth by tape
measure. Targets: **P95 translation error ≤ 0.05 m** (5 % of pallet short
side) and **P95 yaw error ≤ 5°**; ≥ 10 FPS; warning latency ≤ 500 ms (rear).
Do not report these as achieved before this table is filled and computed.

| # | Measured x (m) | Measured z (m) | Measured yaw (°) | Reported x | Reported z | Reported yaw | err pos (m) | err yaw (°) |
|---|----------------|----------------|------------------|------------|------------|--------------|-------------|-------------|
| 1 | | | | | | | | |
| … | | | | | | | | |
| 30 | | | | | | | | |

P95 (quick check):

```bash
# put each error on its own line in two files: err_pos_m.txt, err_yaw_deg.txt
.venv/bin/python - <<'EOF'
import numpy as np
for name in ("err_pos_m", "err_yaw_deg"):
    v = np.loadtxt(f"{name}.txt")
    print(name, "P95 =", round(float(np.percentile(v, 95)), 3),
          "max =", round(float(v.max()), 3), "n =", len(v))
EOF
```

## 10. Wrap-up

- [ ] `marker_placement.yaml` contains only measured values.
- [ ] Acceptance table filled and P95 within targets.
- [ ] Commit `configs/camera_calibration.yaml` + `configs/marker_placement.yaml`
      (calibration record) per the Phase 3.4 commit flow.

## Alternative: the Web guide (scripts/calibrate_positioning_web.py)

Steps 1–5 above can be performed from a browser instead of by hand. One-click
launcher (brings up PoE power + front interface, starts the tool, logs to
/tmp/seg_demo_dual/posweb.log):

```bash
./scripts/run_positioning_web.sh        # prints the browser URL
./scripts/run_positioning_web.sh stop   # stops only this tool's PID
```

Or start it manually:

```bash
.venv/bin/python scripts/calibrate_positioning_web.py \
  --source "rtsp://admin:admin@192.168.137.20:554/" \
  --config configs/marker_placement.yaml \
  --camera-config configs/camera_calibration.yaml
# open http://<JETSON-IP>:8091/
```

Operator flow:
1. Lay the four ground tags flat (page top toward +Z), all in view, then
   click 开始采集 and **slowly move/tilt the camera a little** between frames
   (or move the complete camera/vehicle rig; never move one ground tag
   relative to another). The page now accepts only complete, visibly
   different views; a static video no longer fills the counter. Then click
   停止并解算.
2. The page solves the tag layout and shows per-corner P95 error and the
   validation gates (rotation, coplanarity, repeatability, print-size
   checkbox). A 120.0 mm±1 % border check on the printout remains mandatory.
3. Click anchor mode: pick the ground-frame origin, then the forward
   direction (2 clicks). Click zone mode: pick the four target-zone corners.
4. Park the pallet (tag 10 at its centre) next to the ground board so the
   tag height is estimated automatically; otherwise type the height
   (default 0.15 m) manually.
5. The preview panel runs the exact runtime pipeline; when every gate is
   green, 保存到 marker_placement.yaml atomically writes the file (old file
   backed up first, only the three coordinate blocks are replaced).
6. Acceptance section: park the pallet at 30 tape-measured poses, record
   each row; P95 translation/yaw vs the 5 cm/5 ° targets is computed and can
   be exported to `results/field_acceptance_<ts>.json`.

The paper worksheet stays valid as the offline fallback; both produce the
same `marker_placement.yaml` structure.
