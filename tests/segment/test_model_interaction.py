"""Wrapper-level interaction tests for the add_point fix (root cause B).

The vendored streaming predictor never appends the current frame to
condition_state["images"], so `add_new_points_or_box(frame_idx=N)` for N>=1
crashed with `IndexError: list index out of range`. These tests pin down
`prepare_frame_for_interaction()` (pads the images list) and the add_point
call order (interaction first, then propagate_in_video_preflight).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

import numpy as np
import torch
import unittest

from backend.app.segment.model import (
    EfficientTAMSegmentModel,
    prepare_frame_for_interaction,
)


class FakePredictor:
    """Mimics the vendored EfficientTAMCameraPredictor surface the wrapper
    uses, recording calls (no weights, no CUDA)."""

    def __init__(self, images=None, frame_idx=5):
        self.image_size = 512
        self.frame_idx = frame_idx
        if images is None:
            images = [torch.zeros(3, self.image_size, self.image_size)]
        self.condition_state = {
            "images": images,
            "num_frames": len(images),
            "video_height": 360,
            "video_width": 640,
        }
        self.calls = []

    def perpare_data(self, img, image_size=512):
        return torch.ones(3, image_size, image_size), 640, 360

    def add_new_points_or_box(self, frame_idx=None, obj_id=None, points=None,
                              labels=None, clear_old_points=None, **kw):
        self.calls.append(("add", frame_idx, obj_id, points, labels, clear_old_points))
        return None, None, torch.zeros(1, 1, 360, 640)

    def propagate_in_video_preflight(self):
        self.calls.append(("preflight",))


class TestPrepareFrameForInteraction(unittest.TestCase):
    def test_pads_images_up_to_frame_idx(self):
        pred = FakePredictor(frame_idx=5)
        img = np.zeros((360, 640, 3), dtype=np.uint8)
        out = prepare_frame_for_interaction(pred, img)
        self.assertEqual(len(pred.condition_state["images"]), 6)
        for i in range(1, 6):
            self.assertTrue(torch.equal(pred.condition_state["images"][i], out))

    def test_no_padding_when_current_frame_covered(self):
        pred = FakePredictor(
            images=[torch.zeros(3, 512, 512)] * 3, frame_idx=2
        )
        out = prepare_frame_for_interaction(pred, np.zeros((360, 640, 3), np.uint8))
        self.assertEqual(len(pred.condition_state["images"]), 3)
        self.assertEqual(out.shape, (3, 512, 512))


class TestAddPointChain(unittest.TestCase):
    def test_add_point_runs_interaction_then_preflight_on_current_frame(self):
        pred = FakePredictor(frame_idx=5)
        model = EfficientTAMSegmentModel()
        model._predictor = pred  # inject without loading weights
        out = model.add_point(np.zeros((360, 640, 3), dtype=np.uint8), 100, 50, 1)
        self.assertEqual(out.shape, (360, 640))
        names = [c[0] for c in pred.calls]
        self.assertEqual(names, ["add", "preflight"])
        _, frame_idx, obj_id, points, labels, clear_old = pred.calls[0]
        self.assertEqual(frame_idx, 5)              # current streaming frame
        self.assertEqual(obj_id, 0)
        self.assertEqual(points, [[100.0, 50.0]])
        self.assertEqual(labels, [1])
        self.assertFalse(clear_old)                 # keeps previous points

    def test_add_point_pads_images_before_interaction(self):
        pred = FakePredictor(frame_idx=3)
        model = EfficientTAMSegmentModel()
        model._predictor = pred
        model.add_point(np.zeros((360, 640, 3), dtype=np.uint8), 10, 20, 0)
        self.assertEqual(len(pred.condition_state["images"]), 4)
        self.assertEqual(pred.calls[0][1], 3)       # frame_idx unchanged


if __name__ == "__main__":
    unittest.main()