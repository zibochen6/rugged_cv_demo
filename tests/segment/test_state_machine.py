"""Segment state machine tests: tracking / lost(ghost) / auto-resume."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

import numpy as np
import unittest

from backend.app.segment.service import SegmentStateMachine


def mask(ratio):
    """A small bool mask whose mean area ratio == `ratio`."""
    m = np.zeros((100, 100), dtype=bool)
    n = int(round(ratio * 100 * 100))
    m.reshape(-1)[:n] = True
    return m


class TestStateMachine(unittest.TestCase):
    def setUp(self):
        self.sm = SegmentStateMachine(
            lost_area_ratio=0.001,
            lost_frames=5,
            resume_area_ratio=0.002,
            resume_frames=2,
        )

    def test_idle_until_target(self):
        self.assertEqual(self.sm.state, "idle")
        self.sm.update(mask(0.5))
        self.assertEqual(self.sm.state, "idle")  # no target yet

    def test_begin_target_tracking(self):
        self.sm.begin_target()
        self.assertEqual(self.sm.state, "tracking")

    def test_present_frames_stay_tracking(self):
        self.sm.begin_target()
        for _ in range(20):
            self.assertEqual(self.sm.update(mask(0.5)), "steady")
        self.assertEqual(self.sm.state, "tracking")

    def test_lost_after_hysteresis_keeps_ghost(self):
        self.sm.begin_target()
        self.sm.update(mask(0.5))  # last good
        for i in range(4):
            self.assertEqual(self.sm.update(mask(0.0)), "steady")
        self.assertEqual(self.sm.state, "tracking")  # not yet lost
        self.assertEqual(self.sm.update(mask(0.0)), "lost")
        self.assertEqual(self.sm.state, "lost")
        self.assertIsNotNone(self.sm.last_good)
        self.assertTrue(self.sm.last_good.any())

    def test_stays_lost_while_absent(self):
        self.sm.begin_target()
        self.sm.update(mask(0.5))
        for _ in range(5):
            self.sm.update(mask(0.0))
        self.assertEqual(self.sm.state, "lost")
        for _ in range(10):
            self.assertEqual(self.sm.update(mask(0.0)), "steady")
        self.assertEqual(self.sm.state, "lost")

    def test_auto_resume_after_reappear(self):
        self.sm.begin_target()
        self.sm.update(mask(0.5))
        for _ in range(5):
            self.sm.update(mask(0.0))
        self.assertEqual(self.sm.state, "lost")
        # one present frame is not enough (resume_frames=2)
        self.assertEqual(self.sm.update(mask(0.5)), "steady")
        self.assertEqual(self.sm.state, "lost")
        self.assertEqual(self.sm.update(mask(0.5)), "recovered")
        self.assertEqual(self.sm.state, "tracking")

    def test_clear_resets(self):
        self.sm.begin_target()
        self.sm.update(mask(0.5))
        self.sm.clear()
        self.assertEqual(self.sm.state, "idle")
        self.assertIsNone(self.sm.last_good)

    def test_boundary_ratio_counts_as_present(self):
        self.sm.begin_target()
        self.sm.update(mask(0.002))  # exactly the resume threshold
        self.assertEqual(self.sm.state, "tracking")


if __name__ == "__main__":
    unittest.main()