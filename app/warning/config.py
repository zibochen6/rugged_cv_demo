"""Warning-system configuration (configs/warning.yaml) with defaults.

Flat dict access with dot-free helper; every section has safe defaults so the
app still runs when the yaml is missing/incomplete (fail-visible at runtime).
"""
from __future__ import annotations

import os
from typing import Any

import yaml

DEFAULT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "configs", "warning.yaml")

# deep-merged defaults (mirrors the spec §42 plus platform adaptations)
DEFAULTS: dict[str, Any] = {
    "system": {"backend": "pytorch", "debug": False, "device": "cuda"},
    "pipeline": {"scale": 1.0},
    "camera": {
        "rtsp_url": "",        # "rtsp://USER:PASSWORD@CAMERA_IP:554/"
        "width": 1280, "height": 720, "fps": 30, "flip": False,
    },
    "model": {
        "engine": "models/tensorrt/depth_metric_small_518_fp16.engine",
        "onnx": "models/onnx/depth_metric_small_518.onnx",
        "checkpoint": "checkpoints/depth_anything_v2_metric_indoor_small",
        "input_longest_edge": 518,
        "dtype": "fp16",
    },
    "depth": {
        "min_depth_m": 0.3, "max_depth_m": 10.0,
        "spatial_filter": {"enabled": True, "kernel": 3},
        "temporal_filter": {"enabled": True, "alpha": 0.25},
    },
    "camera_mount": {"height_m": 1.5, "pitch_deg": 25.0,
                     "roll_deg": 0.0, "yaw_deg": 0.0},
    "danger_roi": {
        "normalized_points": [[0.20, 1.0], [0.80, 1.0], [0.63, 0.42], [0.37, 0.42]],
    },
    "collision_corridor": {"enabled": False, "width_m": 1.8, "max_distance_m": 6.0},
    "ground_filter": {"enabled": True, "method": "expected_depth",
                      "tolerance_ratio": 0.20, "min_height_px": 5},
    "obstacle": {"min_area_px": 200, "morphology": True, "min_frames": 3},
    "distance": {"method": "percentile", "percentile": 10},
    "temporal": {"ema_alpha": 0.25, "history_frames": 5, "trigger_frames": 3},
    "ttc": {"enabled": True, "min_closing_speed_mps": 0.10},
    "velocity": {"window_frames": 10},
    "warning": {
        "warning_distance_m": 3.0, "danger_distance_m": 1.5,
        "warning_ttc_s": 3.0, "danger_ttc_s": 1.5,
        "hysteresis": {"exit_margin_m": 0.3, "exit_ttc_s": 0.5},
    },
    "person": {
        "enabled": True,
        "required": True,
        "engine": "models/tensorrt/yolov8n_person_fp16.engine",
        "confidence": 0.45,
        "iou": 0.50,
        "input_size": 640,
        "warning_distance_m": 4.0,
        "danger_distance_m": 2.0,
        "max_result_age_s": 0.5,
    },
    "depth_calibration": {"enabled": False, "scale": 1.0, "offset": 0.0},
    "logger": {"enabled": True, "dir": "logs"},
    "events": {"screenshot_enabled": True, "min_interval_s": 2.0, "dir": "logs"},
    "watchdog": {
        "max_consecutive_bad_frames": 3,
        "camera_lost_timeout_s": 5.0,
    },
}


def _deep(base: dict, extra: dict) -> dict:
    """extra overrides base, section-wise."""
    out = dict(base)
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep(out[k], v)
        else:
            out[k] = v
    return out


class WarnConfig:
    """Typed-ish accessor over the merged config dict."""

    def __init__(self, data: dict) -> None:
        self.data = data

    def get(self, path: str, default: Any = None) -> Any:
        node: Any = self.data
        for key in path.split("."):
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    # conveniences -----------------------------------------------------------
    @property
    def rtsp_url(self) -> str:
        return str(self.get("camera.rtsp_url", ""))

    @property
    def backend(self) -> str:
        return str(self.get("system.backend", "pytorch"))

    @property
    def min_depth_m(self) -> float:
        return float(self.get("depth.min_depth_m", 0.3))

    @property
    def max_depth_m(self) -> float:
        return float(self.get("depth.max_depth_m", 10.0))

    @property
    def height_m(self) -> float:
        return float(self.get("camera_mount.height_m", 1.5))

    @property
    def pitch_deg(self) -> float:
        return float(self.get("camera_mount.pitch_deg", 25.0))

    @property
    def roi_points(self) -> list:
        return [list(map(float, p)) for p in
                self.get("danger_roi.normalized_points",
                         [[0.20, 1.0], [0.80, 1.0], [0.63, 0.42], [0.37, 0.42]])]

    def save(self, path: str) -> None:
        with open(path, "w") as fh:
            yaml.safe_dump(self.data, fh, default_flow_style=False,
                           sort_keys=False, allow_unicode=True)


def load_config(path: str | None = None) -> WarnConfig:
    """Load warning.yaml merged over defaults; missing file => defaults only."""
    p = path or DEFAULT_PATH
    data: dict = {}
    if p and os.path.exists(p):
        try:
            with open(p) as fh:
                data = yaml.safe_load(fh) or {}
        except Exception as exc:  # broken yaml: run defaults, log loudly
            print(f"[warn:config] cannot parse {p}: {exc} — using defaults")
    return WarnConfig(_deep(DEFAULTS, data))
