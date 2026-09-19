# Coordinate Systems and Conventions

This document defines the coordinate systems, rotation conventions, and units used
throughout the pose estimation pipeline.

## 1. Camera Coordinate System (OpenCV Convention)

We use the **OpenCV camera coordinate convention**:

```
        +Y (down)
         ↓
         |
Camera ●─┼────────► +X (right)
         |  ↘
         |    ↘
         ↓      +Z (forward, into scene)
```

- **+X**: Camera image right
- **+Y**: Camera image down
- **+Z**: Camera forward (into the scene)

This is the standard OpenCV convention. The camera optical frame is right-handed.

### Origin
- Origin is at the camera optical center (center of the lens)
- Equivalently, this is the origin of the intrinsic matrix (cx, cy in pixels)

### Why OpenCV Convention?
- Native to OpenCV functions (solvePnP, projectPoints, etc.)
- No coordinate flipping required when projecting 3D to 2D
- Standard for computer vision pipelines

---

## 2. Chessboard Object Coordinate System

```
                  +Y (rows down)
                  ↓
                  |
                  |
   Origin ●───────┼──────► +X (columns right)
                  |
                  |
                  ↓
                  +Z (normal, outward, toward camera)
```

### Origin
- **First inner corner** of the chessboard (top-left when viewing the board face)

### Axes
- **+X**: Along chessboard columns (right when looking at the board)
- **+Y**: Along chessboard rows (down when looking at the board)
- **+Z**: Normal to the chessboard plane (out of the board, toward the viewer)

### Coordinate Frame is Right-Handed
- X × Y = Z (cross product)
- Verified: when looking at the board, Z points toward the camera

### Object Points Layout
For a board with `inner_cols × inner_rows` inner corners and `square_size_m`:

```python
# (N, 3) where N = inner_cols * inner_rows
objp[i, 0] = (i % inner_cols) * square_size_m  # X
objp[i, 1] = (i // inner_cols) * square_size_m  # Y
objp[i, 2] = 0                                  # Z (all on Z=0 plane)
```

### Why "Inner Corners"?
OpenCV detects **inner corners** (intersections where 4 squares meet), not
the printed squares themselves.

| Board Size        | Inner Corners    |
|-------------------|------------------|
| 9×7 squares       | 8×6 inner corners|
| 8×6 squares       | 7×5 inner corners|
| 11×8 squares      | 10×7 inner corners|

Each inner corner provides a known 3D point in object coordinates.

---

## 3. Rotation Representation

### Internal Primary Representation: Rotation Matrix + Quaternion

We use **Rotation Matrix (3×3)** as the canonical internal representation, with
**Quaternion (w, x, y, z)** as a derived form for temporal filtering.

### Why Not Euler Angles Internally?
- **Gimbal lock**: At certain orientations, Euler angles lose a degree of freedom
- **±180° discontinuities**: Same orientation can have multiple Euler representations
- **Non-linear interpolation**: Difficult to smoothly interpolate rotations

Quaternion SLERP (spherical linear interpolation) is mathematically correct
for rotation smoothing.

### Display Convention: Euler Angles (ZYX Intrinsic / Yaw-Pitch-Roll)

When displaying rotation to humans, we use **Euler angles in degrees**:

| Angle  | Axis | Description                    |
|--------|------|--------------------------------|
| Roll   | X    | Rotation around camera X       |
| Pitch  | Y    | Rotation around camera Y       |
| Yaw    | Z    | Rotation around camera Z       |

Euler convention: **ZYX intrinsic** (yaw-pitch-roll)
1. Rotate around Z by yaw
2. Rotate around new Y by pitch
3. Rotate around new X by roll

```
R = Rz(yaw) @ Ry(pitch) @ Rx(roll)
```

### Conversion Functions

```python
from backend.app.pose.transforms import (
    matrix_to_quaternion,
    quaternion_to_matrix,
    matrix_to_euler,
    euler_to_matrix,
    quaternion_to_euler,
    euler_to_quaternion,
)

# All conversions supported
R = matrix_to_quaternion(R)  # R -> Quaternion
euler = matrix_to_euler(R)   # R -> EulerAngles
```

---

## 4. Translation Units

### All Translations in METERS

| Parameter | Unit |
|-----------|------|
| `tvec.x`  | meters |
| `tvec.y`  | meters |
| `tvec.z`  | meters |

### Conversion from camera.yaml

The `square_size_mm` in `camera_calibration.yaml` is converted to meters:

```python
square_size_m = square_size_mm / 1000.0
```

### Example Pose Values

For a chessboard at ~80cm from the camera, slightly off-center:
```json
{
  "translation_m": {
    "x": 0.05,    // 5cm right of camera center
    "y": -0.02,   // 2cm above center
    "z": 0.82     // 82cm in front of camera
  }
}
```

---

## 5. Pose Output Schema

The unified `PoseResult` data structure:

```python
@dataclass
class PoseResult:
    frame_id: int
    timestamp: float

    # Identity
    object_id: str
    object_name: str

    # Validity
    valid: bool
    tracking_state: TrackingState  # WAITING_FOR_CAMERA, WAITING_FOR_OBJECT, DETECTING, TRACKING, UNRELIABLE

    # Confidence
    confidence: float

    # Rotation (rotation matrix is primary)
    rvec: np.ndarray  # (3,) Rodrigues vector
    rotation_matrix: np.ndarray  # (3, 3)
    quaternion: Quaternion  # (w, x, y, z)
    euler: EulerAngles  # (roll, pitch, yaw) in degrees

    # Translation
    translation: Translation  # (x, y, z) in meters

    # Reprojection quality
    reprojection: ReprojectionStats
    metrics: PerformanceMetrics

    # Filter state
    is_raw: bool
    is_filtered: bool
```

### Example JSON Output

```json
{
  "frame_id": 1234,
  "timestamp": 1694351234.567,
  "object_id": "chessboard",
  "object_name": "Chessboard",
  "valid": true,
  "tracking_state": "tracking",
  "confidence": 0.98,

  "translation_m": {
    "x": 0.052,
    "y": -0.031,
    "z": 0.823
  },

  "rotation_deg": {
    "roll": 3.2,
    "pitch": -14.3,
    "yaw": 27.8
  },

  "quaternion": {
    "w": 0.941,
    "x": 0.027,
    "y": -0.124,
    "z": 0.314
  },

  "reprojection": {
    "mean_px": 0.42,
    "median_px": 0.38,
    "max_px": 0.91,
    "inlier_count": 48,
    "total_points": 48,
    "inlier_ratio": 1.0
  },

  "metrics": {
    "detection_ms": 8.3,
    "pnp_ms": 1.2,
    "filter_ms": 0.1,
    "total_ms": 9.8,
    "fps": 28.5
  },

  "is_raw": false,
  "is_filtered": true
}
```

---

## 6. Tracking States

```text
STOPPED (engine not running)
   ↓
RUNNING (engine running)
   ↓
   ├─► WAITING_FOR_CAMERA (camera not started)
   │
   ├─► WAITING_FOR_OBJECT (no chessboard in view)
   │
   ├─► DETECTING (chessboard found, PnP running)
   │
   ├─► TRACKING (PnP succeeded, pose valid)
   │
   └─► UNRELIABLE (PnP failed or quality gates not met)
```

---

## 7. Calibration Resolution

The camera MUST be running at the **same resolution** as during intrinsic calibration.

From `camera_calibration.yaml`:
```yaml
camera:
  width: 1280
  height: 720
```

If the actual stream resolution differs, PnP will produce wrong results because
the intrinsics K is calibrated for a specific resolution.

### Validation
On startup, `PoseEngine` should check:
```python
actual_resolution = camera.get_config().width, camera.get_config().height
calibration_resolution = 1280, 720  # from camera.yaml

if actual_resolution != calibration_resolution:
    raise RuntimeError("Camera resolution does not match calibration resolution")
```

---

## 8. Future: Generic Objects (Phase 3+)

When moving to real objects with YOLO-Pose, the coordinate convention
**must remain identical**:

- Camera coordinate: OpenCV convention (X right, Y down, Z forward)
- Object coordinate: arbitrary, defined by ObjectProfile YAML
- Unit: meters (always)

The ObjectProfile specifies:
```yaml
object:
  id: part_a
  name: Part A

  coordinate_frame:
    origin: object_center    # Where (0,0,0) is on the object
    unit: meter               # Always meters

  keypoints:
    - id: kp0
      xyz: [-0.10, -0.05, -0.04]  # 3D point in object frame (meters)
    - id: kp1
      xyz: [0.10, -0.05, -0.04]
    ...
```

The ObjectProfile must be measured from real geometry (CAD, calipers) — never guessed.

---

## 9. Summary

| Concept              | Convention                                |
|----------------------|-------------------------------------------|
| Camera frame         | OpenCV: X=right, Y=down, Z=forward       |
| Chessboard frame     | Origin=first inner corner, X=col, Y=row, Z=normal |
| Internal rotation    | Rotation Matrix + Quaternion             |
| Display rotation     | Euler (ZYX intrinsic / yaw-pitch-roll), degrees |
| Translation          | Meters (always)                          |
| Reprojection error   | Pixels                                    |
| Coordinate origin    | Camera optical center                    |

This document is the **single source of truth** for coordinate conventions
in the pose estimation pipeline. Any deviation must be documented here.

---

## 10. Object Frame — Seeed Micro SD Card Tool Kit Box (FROZEN)

**Frozen definition. Never change.** The real tracked object is a rigid
115 × 78 × 28 mm cuboid (`object_profiles/seeed_sd_toolkit_box.yaml`):

```
                   +Z
                    ↑
                    |
             +Y ↗   |
                  \ ●────────→ +X
                Origin
                  \
               -Y Front
```

| Item     | Definition |
|----------|-----------|
| Origin   | physical geometric center of the box |
| +X       | toward the RIGHT side of the box |
| +Y       | toward the BACK edge — the long edge near the top "seeed studio" logo |
| -Y       | FRONT — the long edge beneath the "www.seeedstudio.com" / large label |
| +Z       | from the box bottom toward the printed TOP surface |
| Units    | meter |

Geometry (half extents): half_x = 0.0575 m, half_y = 0.0390 m,
half_z = 0.0140 m. The 8 physical cuboid corners are the ObjectProfile
keypoints and the visualization cuboid (identical set, bottom face first):

| corner id         | xyz (m)                        |
|-------------------|--------------------------------|
| front_left_bottom | [-0.0575, -0.0390, -0.0140]    |
| front_right_bottom| [ 0.0575, -0.0390, -0.0140]    |
| back_right_bottom | [ 0.0575,  0.0390, -0.0140]    |
| back_left_bottom  | [-0.0575,  0.0390, -0.0140]    |
| front_left_top    | [-0.0575, -0.0390,  0.0140]    |
| front_right_top   | [ 0.0575, -0.0390,  0.0140]    |
| back_right_top    | [ 0.0575,  0.0390,  0.0140]    |
| back_left_top     | [-0.0575,  0.0390,  0.0140]    |

## 11. Marker Frame (explicit)

The fiducial marker (AprilTag family tag36h11, one marker on the top
surface) defines its own frame — it is NOT the object frame.

| Item     | Definition |
|----------|-----------|
| Origin   | center of the printed marker square |
| +X       | toward the RIGHT side of the marker (printed pattern right) |
| +Y       | toward the marker's pattern TOP (the direction the tag image is "read"), i.e. the printed top edge |
| +Z       | normal OUTWARD from the printed marker plane |
| Units    | meter |

When the marker is mounted per the P0 instruction — centered on the top
surface, edges parallel to the box, pattern top toward the object BACK —
the marker frame is parallel to the object frame and its origin sits at
approximately [0, 0, +height/2] = [0, 0, 0.014] m in the object frame.

**Detector corner order (empirically pinned, M0):** pupil_apriltags
returns corners in image coordinates ordered

    0: pattern bottom-left    1: pattern bottom-right
    2: pattern top-right      3: pattern top-left

So the PnP object points (marker frame, side length S = measured
marker_size_m) used with that exact corner order are:

    0: (-S/2, -S/2, 0)   1: (+S/2, -S/2, 0)
    2: (+S/2, +S/2, 0)   3: (-S/2, +S/2, 0)

There is deliberately NO corner reordering anywhere in the pipeline.

## 12. Transform Convention

`T_A_B` maps POINTS from frame B into frame A (4x4 homogeneous).

    T_camera_object = T_camera_marker @ inverse(T_object_marker)

`T_object_marker` comes from the marker mount configuration:

    translation = [offset_x_mm, offset_y_mm, offset_z_mm] / 1000   (object frame)
    rotation    = Rz(yaw_deg)      (defaults: [0, 0, 14 mm], yaw 0)

The pose published on the Web is ALWAYS `T_camera_object` (the OBJECT
pose). `T_camera_marker` is available separately for debugging — the two
must never be mixed (marker origin is ~14 mm above the object center).
