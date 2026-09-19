import numpy as np

from app.dms.ppe import (ModelHelmetEngine, PpeDetection, associate_ppe,
                         decode_yolov8)
from app.warning.person_detection import IoUTracker


def _output(rows):
    value = np.zeros((1, 7, len(rows)), dtype=np.float32)
    for index, row in enumerate(rows):
        value[0, :, index] = row
    return value


def test_decode_yolov8_maps_the_manifest_classes_and_rescales_boxes():
    # cx, cy, w, h, helmet, head, person at 640 square model coordinates.
    decoded = decode_yolov8(_output([
        [320, 320, 200, 400, 0.01, 0.02, 0.95],
        [320, 180, 80, 80, 0.90, 0.02, 0.01],
    ]), (720, 1280), 640, 0.45, 0.50)
    assert [item.label for item in decoded] == ["head", "person"]
    assert decoded[1].bbox == (440, 135, 400, 450)


def test_model_helmet_engine_marks_head_without_helmet_as_not_worn():
    engine = ModelHelmetEngine()
    detections = [
        PpeDetection("person", (100, 100, 200, 300), 0.9),
        PpeDetection("head", (160, 120, 80, 80), 0.9),
    ]
    result = engine.update(np.zeros((720, 1280, 3), dtype=np.uint8), detections)
    assert len(result) == 1
    assert result[0].verdict == "not_worn"
    assert result[0].reason == "model_exposed_head"


def test_model_helmet_engine_uses_head_when_seated_person_is_not_detected():
    engine = ModelHelmetEngine()
    result = engine.update(np.zeros((720, 1280, 3), dtype=np.uint8), [
        PpeDetection("head", (160, 120, 80, 80), 0.9),
    ])
    assert len(result) == 1
    assert result[0].verdict == "not_worn"
    assert result[0].reason == "model_exposed_head"


def test_no_head_evidence_is_unknown_not_not_worn():
    rows = associate_ppe([PpeDetection("person", (100, 100, 200, 300), 0.9)],
                         IoUTracker())
    assert len(rows) == 1 and rows[0][1] == [] and rows[0][2] == []
    engine = ModelHelmetEngine()
    result = engine.update(np.zeros((720, 1280, 3), dtype=np.uint8),
                           [PpeDetection("person", (100, 100, 200, 300), 0.9)])
    assert result[0].verdict == "unknown"
