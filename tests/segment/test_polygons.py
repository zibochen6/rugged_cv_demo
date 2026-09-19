"""Polygon / geometry helper tests."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

import numpy as np
import unittest

from backend.app.segment.service import (
    downscale_frame,
    mask_center,
    mask_to_polygons,
)


class TestMaskToPolygons(unittest.TestCase):
    def test_rectangle_produces_quad(self):
        m = np.zeros((40, 80), dtype=bool)
        m[10:30, 20:60] = True
        polys = mask_to_polygons(m, scale_x=2.0, scale_y=2.0)
        self.assertEqual(len(polys), 1)
        pts = polys[0]
        self.assertGreaterEqual(len(pts), 3)
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        # corners land inside the scaled rect (40..120, 20..60)
        self.assertLessEqual(min(xs), 45)
        self.assertGreaterEqual(max(xs), 115)
        self.assertLessEqual(min(ys), 25)
        self.assertGreaterEqual(max(ys), 55)

    def test_empty_mask_no_polygons(self):
        self.assertEqual(mask_to_polygons(np.zeros((10, 10), dtype=bool), 1.0, 1.0), [])

    def test_tiny_mask_filtered(self):
        m = np.zeros((40, 40), dtype=bool)
        m[0, 0] = True  # 1 px, below min area
        self.assertEqual(mask_to_polygons(m, 1.0, 1.0), [])

    def test_two_blobs_sorted_largest_first(self):
        m = np.zeros((100, 100), dtype=bool)
        m[10:60, 10:60] = True  # 2500 px
        m[10:70, 60:70] = True  # bar below-right -> L-shape, more vertices
        m[75:85, 75:85] = True  # separate small square, 100 px
        polys = mask_to_polygons(m, 1.0, 1.0)
        self.assertEqual(len(polys), 2)
        self.assertGreater(len(polys[0]), len(polys[1]))


class TestMaskCenter(unittest.TestCase):
    def test_center_of_square(self):
        m = np.zeros((10, 10), dtype=bool)
        m[2:8, 2:8] = True
        cx, cy = mask_center(m, scale_x=2.0, scale_y=2.0)
        # pixel-centroid of columns 2..7 is 4.5 (row pixels 2..7 -> 4.5)
        self.assertEqual((cx, cy), (9, 9))

    def test_empty_mask_no_center(self):
        self.assertIsNone(mask_center(np.zeros((8, 8), dtype=bool), 1.0, 1.0))


class TestDownscale(unittest.TestCase):
    def test_large_frame_downscaled_to_max_side(self):
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        small, sx, sy = downscale_frame(frame, max_side=640)
        self.assertEqual(small.shape[:2], (360, 640))
        self.assertAlmostEqual(sx, 2.0)
        self.assertAlmostEqual(sy, 2.0)

    def test_small_frame_kept(self):
        frame = np.zeros((240, 320, 3), dtype=np.uint8)
        small, sx, sy = downscale_frame(frame, max_side=640)
        self.assertEqual(small.shape[:2], (240, 320))
        self.assertAlmostEqual(sx, 1280 / 320.0)
        self.assertAlmostEqual(sy, 720 / 240.0)

    def test_portrait_frame(self):
        frame = np.zeros((1280, 720, 3), dtype=np.uint8)
        small, sx, sy = downscale_frame(frame, max_side=640)
        self.assertEqual(small.shape[:2], (640, 360))
        self.assertAlmostEqual(sx, 1280 / 360.0)
        self.assertAlmostEqual(sy, 720 / 640.0)


if __name__ == "__main__":
    unittest.main()