# Phase 13.0 — Baseline Audit & Architecture Design
## Forklift Monocular Cargo Placement Alignment System

Date: 2025-09-02 (audit run)
Branch audited: `feat/3d-click-track` @ `2ffc010`
Protection tag: **`baseline-phase12-live-3d-working`**
Development branch: **`feat/forklift-placement`** (created from the audited commit)

---

## 1. Baseline verification (ALL PASS)

| Suite | Result | Detail |
|---|---|---|
| `tests/geometry/test_camera_projection.py` | PASS | pinhole project/back-project |
| `tests/geometry/test_cuboid.py` | PASS | PCA cuboid fit + outliers |
| `tests/geometry/test_depth_sampling.py` | PASS | robust mask depth sampling |
| `tests/tracking3d/test_kalman.py` | PASS | gated 3D Kalman |
| `tests/tracking3d/test_status_gates.py` | PASS | Phase-12 2D/Depth/3D states |
| `tests/tracking3d/test_box_filter.py` | PASS | box temporal smoothing |
| `tests/tracking3d/test_trajectory.py` | PASS | bounded trajectory |
| `tests/bev/test_bev_renderer.py` | PASS | top-down BEV |
| `tests/depth/test_depth_standalone.py` | PASS | Depth Anything V2 metric |
| EfficientTAM regression (`scripts/run_regression.sh`) | **ALL PASS** | model load / image prompt / video propagation |
| Phase 12 live gate, `usb:0`, 500 frames | **PASS** | G1–G6 all pass, **10.6 FPS**, valid_track=500/500, neg_z=0, cuboid_vis=100% |
| Phase 12 video smoke (`dog.mp4`, 60 frames) | PASS* | G1–G5 pass; G6 = 9.8 FPS (video-decode overhead; gate target is the live camera, which passes at 10.6 FPS) |

Conclusion: **Existing regression = ALL PASS. Baseline protected = YES.**

Git state:
```
git tag baseline-phase12-live-3d-working   # at 2ffc010
git checkout -b feat/forklift-placement    # development happens ONLY here
```

---

## 2. Existing architecture summary (what we build on)

### 2.1 Coordinate convention (current, single convention everywhere)
Camera coordinate system, documented in `app/geometry/camera_model.py`:
```
+X = right, +Y = down, +Z = forward   (meters, camera optical center = origin)
```
Used consistently by: `projection.py`, `pointcloud.py`, `cuboid.py`,
`kalman.py`, `bev/renderer.py` (BEV plots (x, z), z = forward).

### 2.2 Key reusable components (composition, NOT modification)
| Component | File | Reuse for placement |
|---|---|---|
| `Pipeline3D` | `app/camera_demo_3d.py` | click → mask → depth → XYZ → Kalman → cuboid (via `process(frame, mask)` returning `box_filt`, `pos`, `status`, …) |
| `OnlineEfficientTAMTracker` | `app/tracker.py` | mask tracking, neg points |
| `CameraModel` | `app/geometry/camera_model.py` | intrinsics, `scaled_to`, undistortion hooks, `calibrated` flag |
| `DepthCalibration` | `app/geometry/camera_model.py` | tape-measure scale/bias fit (reuse for Phase 13.12) |
| `Cuboid3D` / `fit_cuboid` / `corners_from_params` | `app/geometry/cuboid.py` | input to cargo footprint; yaw ∈ (−90°, +90°], π symmetry |
| `BoxSmoother` | `app/tracking3d/box_filter.py` | dims rate-limit + doubled-angle yaw EMA — pattern reused for footprint smoothing |
| `BEVRenderer` | `app/bev/renderer.py` | (x, z) → panel mapping; the Placement Assist View builds a NEW renderer (superset), BEV renderer stays untouched |
| `open_source` / `overlay_masks` | `app/camera_demo.py` | camera open, exposure control |
| Status machinery | `app/tracking3d/status.py` | 2D/Depth/3D states feed the placement state machine |

---

## 3. Coordinate system design (Phase 13.1)

### 3.1 Ground / Forklift frame (NEW, documented convention)
```
Ground frame G:
    +X = right   (lateral)
    +Y = up      (height)
    +Z = forward (horizontal, away from the forklift)
    origin: camera position projected onto the ground plane
    ground plane: Y = 0
```
Rationale: (X, Z) matches the existing camera/BEV convention (right,
forward), so BEV/assist rendering reuses the same mapping; only the vertical
axis flips (+Y up instead of +Y down).

### 3.2 Camera ↔ Ground transform
Extrinsics parameterized by `height_m, pitch_deg, roll_deg, yaw_deg`
(pitch positive = looking down). With roll = yaw = 0 the exact transform is:
```
R_cg = [[1, 0,      0     ],
        [0, cos p, -sin p ],
        [0, sin p,  cos p ]]
P_ground = R_cg · P_camera + (0, h, 0)
P_camera = R_cgᵀ · (P_ground − (0, h, 0))
```
General roll/yaw composed as R = R_yaw·R_pitch·R_roll about ground axes.
Full SE(3) `R, t` stored so future calibration tools can inject a measured
extrinsic matrix directly.

### 3.3 New module `app/geometry/ground_plane.py`
```python
class GroundExtrinsics:           # height/pitch/roll/yaw -> R, t (+ hash for versioning)
def camera_point_to_ground(p_c)   # (N,3) cam -> (N,3) ground
def ground_point_to_camera(p_g)   # inverse
def ray_ground_intersection(u, v, cam)   # pixel -> ground (x, z); None if ray parallel/away
def project_ground_polygon(pts_g, cam, cam_model)  # ground poly -> camera -> pixels (+ validity)
def pixel_to_ground_ray(...)      # used by the zone calibration clicks
```
Pixel clicks are mapped to ground via the ray/plane intersection (no depth
model needed — the ground plane IS the calibration).

---

## 4. TargetZone design (Phase 13.2/13.3)

### 4.1 Data structure (`app/placement/target_zone.py`)
```python
@dataclass
class TargetZone:
    zone_id: str                 # "zone_a" … preset-ready (§74)
    frame: str = "forklift_ground"
    corners_m: np.ndarray        # (4, 2) ground (x, z), order P1..P4
    center: np.ndarray           # (2,) ground (x, z)
    width: float                 # lateral extent (m)
    length: float                # forward extent (m)
    yaw_deg: float               # orientation in ground frame, normalized (-90, 90]
    # provenance / versioning (§75/§76)
    timestamp: float
    camera_calibration_hash: str   # hash of intrinsics used
    extrinsic_calibration_hash: str

class TargetZoneProvider(ABC): ...          # §80 extension point
class ManualCalibratedZoneProvider(...)     # loads configs/target_zone.yaml
```
- Serialized to `configs/target_zone.yaml` exactly in the §11 schema
  (frame / corners_m / center / width / length / yaw_deg + provenance).
- File stores a `zones:` mapping keyed by `zone_id` (multi-preset-ready,
  `active:` selector) — never a single hardcoded global.
- Zone is **persistent geometry**: after calibration it never depends on
  per-frame vision; occlusion cannot invalidate it (§12/§40/§52).

### 4.2 Calibration tool `app/calibrate_target_zone.py`
- Opens camera (`open_source`, same exposure handling as the demos).
- Guided UI: `ZONE CALIBRATION — CLICK CORNER k/4`, live corner markers,
  `r` redo, `enter` confirm, `esc` cancel.
- Clicks → undistort → `ray_ground_intersection` → ground (x, z) → derive
  center/width/length/yaw (ordered corners, rect fit) → write yaml.
- Refuses to write if ground extrinsics are missing (`calibrated: false`
  warning path: allow with explicit `--allow-uncalibrated` but stamp the
  provenance hashes honestly).

### 4.3 Overlay (`app/placement/overlay.py`)
- Ground polygon → camera → image, drawn as **dashed** cyan/blue polygon
  + translucent fill; corner labels; "TARGET ZONE (calibrated, projected)"
  caption. First version: all-dashed (honest: not a live-detected boundary).

---

## 5. CargoFootprint design (Phase 13.4)

### 5.1 Data structure (`app/placement/cargo_footprint.py`)
```python
@dataclass
class CargoFootprint:
    center: np.ndarray        # (2,) ground (x, z)
    corners: np.ndarray       # (4, 2) ground (x, z)
    width: float              # lateral
    length: float             # forward
    yaw: float                # rad, normalized (-π/2, π/2] (π symmetry)
    dimension_confidence: float   # 0..1
    occlusion: str            # FULL | PARTIAL | SEVERE_OCCLUSION | LOST
```

### 5.2 Derivation
`Cuboid3D` (camera coords) → `camera_point_to_ground` on 8 corners →
bottom face (Y≈0 plane fit / lowest 4 corners) → ground 2D quad →
center / width / length / yaw.

### 5.3 Smoothing + occlusion policy (§16–§19, §78–§79)
New `FootprintSmoother` (pattern lifted from `BoxSmoother`):
- **center**: high-frequency EMA (fast response).
- **width/length**: rate-limited EMA (strong), and **frozen** when
  `dimension_confidence < threshold`.
- **yaw**: doubled-angle EMA, frozen in severe occlusion.

`dimension_confidence` from: mask area ratio vs. running stable area,
point count after outlier filter, depth spread (MAD), visible-surface
consistency. Occlusion state machine:
```
FULL               confidence high, dims trusted
PARTIAL            confidence mid: position updates, dims/yaw damped
SEVERE_OCCLUSION   confidence low: position-only updates, dims/yaw FROZEN
LOST               2D LOST -> footprint frozen last-known (never "no box")
```
Placement continues using the frozen geometry while `LOST`/severe, but
placement status reports `LOST` (§51).

---

## 6. PlacementMetrics design (Phase 13.5)

### 6.1 Module `app/placement/alignment.py`
```python
@dataclass
class PlacementMetrics:
    delta_x: float            # cargo_x - target_x   (+ = cargo right of target)
    delta_z: float            # cargo_z - target_z   (+ = cargo beyond target)
    delta_yaw_deg: float      # normalized with 180° symmetry
    polygon_iou: float
    cargo_inside_ratio: float # intersection_area / cargo_area  (PRIMARY metric, §24)
    min_edge_margin: float    # min distance cargo corner -> zone edge (negative = outside)
    placement_score: float    # 0-100 UI aid only; PASS/FAIL uses hard thresholds (§28)
    status: str               # from state machine
```
- **No Shapely** (not in the venv; no new deps on Jetson): implement
  Sutherland–Hodgman convex clipping (both polygons are convex quads) →
  intersection area; union = A+B−∩. Pure numpy, < 1 ms (§57 budget: whole
  ground-transform + alignment < 2 ms).
- PASS criteria (config `placement:` block):
  `|Δx| ≤ tol_x ∧ |Δz| ≤ tol_z ∧ |Δyaw| ≤ tol_yaw ∧ inside_ratio ≥ 0.95
   ∧ min_edge_margin ≥ min_margin`, held for `stable_time_s` (§27).

### 6.2 Guidance (`app/placement/guidance.py`)
Cargo-frame commands (§30): `MOVE LEFT/RIGHT n cm`, `MOVE FORWARD/BACK n cm`,
`ROTATE CW/CCW n°` with deadbands; signs derived from ΔX/ΔZ/ΔYaw conventions
documented in the module docstring.

---

## 7. PlacementStateMachine design (Phase 13.13)

`app/placement/state_machine.py`:
```
IDLE → TARGET_SELECTED (zone loaded) → TRACKING (cargo clicked)
→ APPROACHING   when cargo within near-radius of zone
→ ALIGNING      when inside_ratio > entry threshold
→ ALIGNED_PENDING  when all tolerances met
→ PLACEMENT_OK  after stable_time_s (≥0.8 s, AND ≥ 8 consecutive frames §64)
→ LOST          any time 2D/3D lost  (never PLACEMENT_OK while LOST §51)
```
- Hysteresis on every transition (enter/exit thresholds differ) to avoid
  flicker; timers use real timestamps.
- Zone itself never goes LOST (§52) unless calibration explicitly invalidated.

---

## 8. New files plan (incremental, per gate)

```
app/geometry/ground_plane.py            # 13.1  camera-ground transform
app/placement/__init__.py               # 13.x
app/placement/target_zone.py            # 13.2  zone model + provider + (de)serialization
app/calibrate_target_zone.py            # 13.2  interactive zone calibration
app/placement/overlay.py                # 13.3  zone projection into camera view
app/placement/cargo_footprint.py        # 13.4  footprint + smoother + occlusion policy
app/placement/alignment.py              # 13.5  metrics + polygon clipping
app/placement/guidance.py               # 13.6  operator guidance
app/placement/state_machine.py          # 13.13 states/transitions
app/forklift_placement_demo.py          # 13.7  product entry (orchestration ONLY)
app/placement/assist_view.py            # 13.7  Placement Assist top-down renderer
app/placement/logger.py                 # 13.14 CSV logging (logs/placement/)
configs/placement.yaml                  # 13.x  extrinsics+forklift+tolerances+guidance
configs/target_zone.yaml                # 13.2  output of calibration tool
scripts/run_placement_demo.sh           # 13.x  launcher
scripts/calibrate_camera_intrinsics.py  # 13.10 checkerboard K + RMS report
benchmarks/benchmark_placement.py       # 13.15 stage latencies
tests/placement/test_ground_plane.py    # 13.1  round-trip + ray intersection
tests/placement/test_target_zone.py     # 13.2  serialization, corner fit
tests/placement/test_footprint.py       # 13.4  smoothing/freeze behavior
tests/placement/test_alignment.py       # 13.5  IoU/inside-ratio/known offsets
tests/placement/test_state_machine.py   # 13.13 transitions, stability hold, LOST
tests/placement/test_zone_occlusion.py  # 13.9  metrics invariant under ROI blackout
docs/forklift_placement/phase13.*.md    # per-gate reports (§87 format)
```

## 9. Files that MUST NOT be modified (baseline protection)

```
app/camera_demo_3d.py      # generic demo stays the generic demo (§67-§69);
                           #   forklift demo COMPOSES Pipeline3D, no edits needed
app/camera_demo.py
app/tracker.py
app/geometry/{camera_model,cuboid,depth_sampling,pointcloud,projection}.py
app/tracking3d/*.py
app/bev/renderer.py
app/depth/*.py
third_party/EfficientTAM/**   # wrapper/adapter/composition only (§90)
tests/** (existing)           # existing tests only ever run, never weakened
configs/3d_pipeline.yaml      # extended ONLY via new keys if unavoidable;
                              #   placement config lives in configs/placement.yaml
```
Dependency rule: **no new pip dependencies** (no Shapely, no AprilTag) in
this round; `requirements-jetson.txt` untouched.

## 10. Config layout (new `configs/placement.yaml`)
```yaml
camera_extrinsics:
  calibrated: false
  height_m: null        # measured camera height above ground
  pitch_deg: null       # positive = looking down
  roll_deg: 0.0
  yaw_deg: 0.0

forklift:
  camera_to_fork_center_x_m: 0.0
  camera_to_fork_tip_z_m: 1.2
  fork_spacing_m: 0.68
  fork_length_m: 1.10

target_zone:
  config_path: configs/target_zone.yaml
  active_zone: zone_a

placement:
  tolerance_x_m: 0.05
  tolerance_z_m: 0.05
  tolerance_yaw_deg: 5.0
  min_inside_ratio: 0.95
  min_edge_margin_m: 0.02
  stable_time_s: 0.8
  min_stable_frames: 10
  near_radius_m: 3.0          # TRACKING -> APPROACHING
  inside_entry_ratio: 0.60    # APPROACHING -> ALIGNING

guidance:
  enable: true
  deadband_x_m: 0.02
  deadband_z_m: 0.02
  deadband_yaw_deg: 2.0

footprint:
  min_dimension_confidence: 0.5   # below => freeze dims/yaw
  occlusion_partial_ratio: 0.75
  occlusion_severe_ratio: 0.45
```

## 11. Honesty rules carried into every UI surface (§46–§48)
- While `camera.calibrated == false` OR extrinsics `calibrated: false` OR
  no depth scale fit → UI shows **APPROXIMATE** badge.
- Zone overlay is always labeled *projected calibrated zone*, never implied
  to be live-detected.
- No safety/metrology claims anywhere in UI/docs.

## 12. Known architecture risks (documented, not hidden)
1. **Moving forklift + fixed world zone**: a single static extrinsic
   calibration cannot track a zone once the vehicle moves relative to it.
   MVP assumes: approach → (re)calibrate/confirm zone → one placement
   maneuver with fixed camera-to-zone relation (§81–§83). Future work:
   marker/SLAM/odometry-based zone provider via `TargetZoneProvider`.
2. Monocular metric depth accuracy limits placement to ~±10 cm demo-grade;
   ±5 cm target only after real K + extrinsics + depth-scale calibration
   (§46). Until then, all metrics labeled APPROXIMATE.
3. Cargo footprint under fork occlusion is estimated, not measured —
   dims/yaw freeze policy is the mitigation (§17–§19, §78–§79).

## 13. Gate plan (Phase 13.1 → 13.16)
Per gate: implement → unit tests → run FULL existing regression suite →
performance check (placement budget < 2 ms; e2e ≥ 10 FPS) → report in
`docs/forklift_placement/` (§87 format) → independent commit (§89).
Any baseline regression FAIL ⇒ stop, revert, fix before proceeding (§88).

---

## Confirmation

```
Existing regression = ALL PASS
Baseline protected = YES   (tag baseline-phase12-live-3d-working @ 2ffc010)
Architecture ready = YES
```

Next: **Phase 13.1 — Ground coordinate system** (`app/geometry/ground_plane.py`
+ `tests/placement/test_ground_plane.py`).
