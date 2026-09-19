import numpy as np

from app.observations import NewFrameGate, ObservationStamp
from app.warning.person_detection import (
    IoUTracker, PersonDetection, _ensure_tensorrt_numpy_compat,
)
from app.warning.person_risk import (
    PERSON_UNKNOWN_DISTANCE, PersonRiskAssessor, estimate_distance_at_feet,
)
from app.warning.risk_engine import DANGER, SAFE, WARNING


def test_observation_freshness_and_monotonic_frame_gate():
    assert ObservationStamp(2, 10.0).is_fresh(10.49)
    assert not ObservationStamp(2, 10.0).is_fresh(10.51)
    gate = NewFrameGate()
    assert gate.accept(3)
    assert not gate.accept(3)
    assert not gate.accept(2)
    assert gate.accept(4)


def test_distance_samples_person_feet_and_two_zones():
    depth = np.full((100, 100), np.nan, np.float32)
    depth[70:91, 40:61] = 1.4
    det = PersonDetection((25, 20, 50, 70), 0.9)
    d, valid = estimate_distance_at_feet(depth, det.bbox)
    assert valid and abs(d - 1.4) < 1e-5
    assessor = PersonRiskAssessor(3.0, 1.5)
    out = assessor.assess([det], depth, depth.shape)
    assert out.level == DANGER
    depth[70:91, 40:61] = 2.2
    assert assessor.assess([det], depth, depth.shape).level == WARNING
    depth[70:91, 40:61] = 4.0
    assert assessor.assess([det], depth, depth.shape).level == SAFE


def test_unknown_distance_warns_and_roi_filters():
    det = PersonDetection((40, 10, 20, 40), 0.9)
    assessor = PersonRiskAssessor(
        roi_points=[[0.25, 1.0], [0.75, 1.0], [0.65, 0.3], [0.35, 0.3]])
    out = assessor.assess([det], None, (100, 100))
    assert out.level == WARNING and out.reason == PERSON_UNKNOWN_DISTANCE
    outside = PersonDetection((0, 10, 10, 40), 0.9)
    assert assessor.assess([outside], None, (100, 100)).level == SAFE


def test_iou_tracker_keeps_id_and_expires_tracks():
    tracker = IoUTracker(iou_threshold=0.2, max_missed_frames=1)
    first = tracker.update([PersonDetection((10, 10, 20, 40), 0.9)])[0]
    second = tracker.update([PersonDetection((12, 10, 20, 40), 0.9)])[0]
    assert first.track_id == second.track_id
    tracker.update([])
    tracker.update([])
    third = tracker.update([PersonDetection((12, 10, 20, 40), 0.9)])[0]
    assert third.track_id != first.track_id


def test_jetpack5_tensorrt_numpy_bool_compatibility():
    np.__dict__.pop("bool", None)
    _ensure_tensorrt_numpy_compat()
    assert np.__dict__["bool"] is np.bool_


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  {fn.__name__}: PASS")
    print("\nGATE: PASS")
