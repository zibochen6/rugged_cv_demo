"""Template re-lock helper tests (lost -> auto re-acquire)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

import numpy as np
import unittest

from backend.app.segment.service import (
    build_template,
    erode_mask_for_template,
    find_template_peak,
    find_template_peak_psr,
)


class TestBuildTemplate(unittest.TestCase):
    def test_none_for_empty_mask(self):
        gray = np.zeros((100, 100), dtype=np.uint8)
        mask = np.zeros((100, 100), dtype=bool)
        self.assertIsNone(build_template(gray, mask))

    def test_tiny_mask_none(self):
        gray = np.zeros((100, 100), dtype=np.uint8)
        mask = np.zeros((100, 100), dtype=bool)
        mask[0, 0] = True
        self.assertIsNone(build_template(gray, mask))

    def test_crop_resized(self):
        gray = np.full((100, 100), 10, dtype=np.uint8)
        gray[30:60, 40:80] = 200  # 30x40 bright block
        mask = np.zeros((100, 100), dtype=bool)
        mask[30:60, 40:80] = True
        tpl = build_template(gray, mask, size=48)
        self.assertIsNotNone(tpl)
        # aspect preserved: 30x40 -> 36x48
        self.assertEqual(tpl.shape[:2], (36, 48))

    def test_erode_shrinks_mask_before_build(self):
        gray = np.full((100, 100), 10, dtype=np.uint8)
        gray[30:60, 40:80] = 200
        mask = np.zeros((100, 100), dtype=bool)
        mask[30:60, 40:80] = True
        eroded = erode_mask_for_template(mask)
        # 30x40 block, 3 iterations of a 3x3 erosion -> 24x34
        self.assertEqual(eroded.sum(), (60 - 30 - 6) * (80 - 40 - 6))
        self.assertIsNotNone(build_template(gray, eroded))

    def test_erode_empty_mask_unchanged(self):
        mask = np.zeros((10, 10), dtype=bool)
        eroded = erode_mask_for_template(mask)
        self.assertFalse(eroded.any())


class TestFindTemplatePeak(unittest.TestCase):
    def setUp(self):
        self.frame = np.full((180, 320), 20, dtype=np.uint8)
        # textured (checkerboard) 50x60 block — a flat block would be
        # matchable everywhere and is deliberately not used
        checker = np.tile(np.array([[200, 40], [40, 200]], dtype=np.uint8), (25, 30))
        self.frame[60:110, 100:160] = checker
        self.template = self.frame[60:110, 100:160].copy()

    def test_finds_peak_at_center(self):
        peak = find_template_peak(self.frame, self.template, threshold=0.5)
        self.assertIsNotNone(peak)
        cx, cy, score = peak
        self.assertGreater(score, 0.95)
        # center of the block: x=(100+160)/2=130 -> 130/320=0.406; y=85/180=0.472
        self.assertAlmostEqual(cx, 130 / 320.0, places=2)
        self.assertAlmostEqual(cy, 85 / 180.0, places=2)

    def test_below_threshold_none(self):
        blank = np.full((180, 320), 20, dtype=np.uint8)
        self.assertIsNone(find_template_peak(blank, self.template, threshold=0.5))

    def test_template_bigger_than_frame_none(self):
        big = np.zeros((400, 400), dtype=np.uint8)
        small = np.zeros((100, 100), dtype=np.uint8)
        self.assertIsNone(find_template_peak(small, big, threshold=0.5))

    def test_constant_template_none(self):
        frame = np.full((180, 320), 20, dtype=np.uint8)
        flat_tpl = np.full((50, 60), 200, dtype=np.uint8)
        self.assertIsNone(find_template_peak(frame, flat_tpl, threshold=0.5))

    def test_none_inputs(self):
        self.assertIsNone(find_template_peak(None, None))


class TestFindTemplatePeakPSR(unittest.TestCase):
    """find_template_peak_psr: same matching contract plus the peak
    significance (PSR) used by the verified re-lock gate."""

    def setUp(self):
        self.frame = np.full((180, 320), 20, dtype=np.uint8)
        checker = np.tile(np.array([[200, 40], [40, 200]], dtype=np.uint8), (25, 30))
        self.frame[60:110, 100:160] = checker
        self.template = self.frame[60:110, 100:160].copy()

    def test_returns_peak_with_psr(self):
        peak = find_template_peak_psr(self.frame, self.template, threshold=0.5)
        self.assertIsNotNone(peak)
        cx, cy, score, psr = peak
        self.assertGreater(score, 0.95)
        self.assertGreater(psr, 4.0)
        self.assertAlmostEqual(cx, 130 / 320.0, places=2)
        self.assertAlmostEqual(cy, 85 / 180.0, places=2)

    def test_unique_spot_psr_is_large(self):
        # a lone textured patch on black: strong unique peak -> high PSR
        f = np.zeros((180, 320), dtype=np.uint8)
        f[60:110, 100:160] = 200
        f[75:95, 120:140] = 40  # inner mark -> non-constant template
        t = f[60:110, 100:160].copy()
        peak = find_template_peak_psr(f, t, threshold=0.5)
        self.assertIsNotNone(peak)
        self.assertGreater(peak[3], 4.0)

    def test_below_threshold_none(self):
        blank = np.full((180, 320), 20, dtype=np.uint8)
        self.assertIsNone(find_template_peak_psr(blank, self.template, threshold=0.5))

    def test_constant_template_none(self):
        flat_tpl = np.full((50, 60), 200, dtype=np.uint8)
        self.assertIsNone(find_template_peak_psr(self.frame, flat_tpl, threshold=0.5))

    def test_none_inputs(self):
        self.assertIsNone(find_template_peak_psr(None, None))


if __name__ == "__main__":
    unittest.main()