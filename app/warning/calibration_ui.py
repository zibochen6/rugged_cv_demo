"""Calibration UI (§37/§38).

C key toggles calibration mode inside warn_app:
  - trackbars: camera height (0.5-3.0 m), pitch (0-60 deg),
               warning distance, danger distance
  - ROI editing: drag the polygon vertex nearest to the mouse (left button
    press + move + release); S saves configs/warning.yaml, R resets
This module is UI-only: it mutates a WarnConfig and reads mouse events.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

_WIN = "warn-calib"


@dataclass
class CalibrationState:
    active: bool = False
    dragging: int = -1           # ROI vertex index being dragged (-1 none)
    # trackbar mirrors (written by callbacks)
    height_cm: int = 150
    pitch_deg: int = 25
    warn_dm: int = 30            # meters * 10
    danger_dm: int = 15
    saved: bool = False


class CalibrationUI:
    """Manages the calibration window/trackbars/ROI-drag."""

    def __init__(self, cfg) -> None:
        self.cfg = cfg                      # WarnConfig
        self.st = CalibrationState(
            height_cm=int(round(cfg.height_m * 100)),
            pitch_deg=int(round(cfg.pitch_deg)),
            warn_dm=int(round(cfg.get("warning.warning_distance_m", 3.0) * 10)),
            danger_dm=int(round(cfg.get("warning.danger_distance_m", 1.5) * 10)),
        )
        self._roi_pts = [np.array(p, dtype=np.float32)
                         for p in cfg.roi_points]
        self._baseline_roi = [p.copy() for p in self._roi_pts]

    # -- trackbar plumbing ----------------------------------------------------
    @staticmethod
    def _cb(state: CalibrationState, out: dict):
        def _store(x, key):
            out[key] = x
        return _store

    def open_win(self) -> None:
        try:
            cv2.destroyWindow(_WIN)
        except Exception:
            pass
        cv2.namedWindow(_WIN, cv2.WINDOW_NORMAL | cv2.WINDOW_GUI_EXPANDED)
        cv2.resizeWindow(_WIN, 560, 420)

        def tb(name, val, lo, hi, key):
            cv2.createTrackbar(name, _WIN, val, hi - lo,
                               lambda x: None)  # read transform below
        tb("height cm", self.st.height_cm, 50, 300, "h")
        cv2.setTrackbarMin("height cm", _WIN, 50)
        cv2.setTrackbarMax("height cm", _WIN, 300)
        tb("pitch deg", self.st.pitch_deg, 0, 60, "p")
        cv2.setTrackbarMin("pitch deg", _WIN, 0)
        cv2.setTrackbarMax("pitch deg", _WIN, 60)
        tb("warning m x10", self.st.warn_dm, 5, 100, "w")
        cv2.setTrackbarMin("warning m x10", _WIN, 5)
        cv2.setTrackbarMax("warning m x10", _WIN, 100)
        tb("danger m x10", self.st.danger_dm, 3, 100, "d")
        cv2.setTrackbarMin("danger m x10", _WIN, 3)
        cv2.setTrackbarMax("danger m x10", _WIN, 100)
        dummy = np.zeros((140, 560, 3), np.uint8)
        cv2.imshow(_WIN, dummy)

    def read_trackbars(self) -> None:
        try:
            self.st.height_cm = cv2.getTrackbarPos("height cm", _WIN)
            self.st.pitch_deg = cv2.getTrackbarPos("pitch deg", _WIN)
            self.st.warn_dm = cv2.getTrackbarPos("warning m x10", _WIN)
            self.st.danger_dm = cv2.getTrackbarPos("danger m x10", _WIN)
        except Exception:
            pass

    # -- ROI drag ---------------------------------------------------------
    def mouse(self, event, x, y, flags, draw_scale) -> bool:
        """Returns True when a drag consumed the event (draw_scale applied
        externally: the renderer knows window->video mapping)."""
        if not self.st.active:
            return False
        if event == cv2.EVENT_LBUTTONDOWN:
            best, bd = -1, 1e9
            for i, p in enumerate(self._roi_pts):
                d = (p[0] - x) ** 2 + (p[1] - y) ** 2
                if d < bd:
                    bd, best = d, i
            if bd < 64.0 ** 2:
                self.st.dragging = best
                return True
        elif event == cv2.EVENT_MOUSEMOVE and self.st.dragging >= 0:
            idx = self.st.dragging
            px = min(max(x, 0.0), 1.0)
            py = min(max(y, 0.0), 1.0)
            self._roi_pts[idx] = np.array([px, py], dtype=np.float32)
            return True
        elif event == cv2.EVENT_LBUTTONUP:
            self.st.dragging = -1
            return True
        return False

    def apply_to_config(self) -> None:
        d = self.cfg.data
        d.setdefault("camera_mount", {})["height_m"] = self.st.height_cm / 100.0
        d["camera_mount"]["pitch_deg"] = float(self.st.pitch_deg)
        d.setdefault("warning", {})["warning_distance_m"] = self.st.warn_dm / 10.0
        d["warning"]["danger_distance_m"] = self.st.danger_dm / 10.0
        d["danger_roi"]["normalized_points"] = \
            [p.tolist() for p in self._roi_pts]

    def save(self, path: str) -> None:
        self.apply_to_config()
        try:
            self.cfg.save(path)
            self.st.saved = True
            print(f"[calib] saved {path}")
        except Exception as exc:
            print(f"[calib] save failed: {exc}")

    def reset(self) -> None:
        self._roi_pts = [p.copy() for p in self._baseline_roi]

    @property
    def roi_points(self) -> list:
        return [p.tolist() for p in self._roi_pts]

    def close_win(self) -> None:
        try:
            cv2.destroyWindow(_WIN)
        except Exception:
            pass