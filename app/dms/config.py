"""DMS demo configuration (configs/dms.yaml) with safe defaults.

镜像 app/warning/config.py 的成熟形态：DEFAULTS 与 configs/dms.yaml 一一对应，
yaml 缺失/损坏时回落到默认值并打印一行可读诊断（fail-visible，而不是 fail-closed）。
"""
from __future__ import annotations

import os
from typing import Any

import yaml

DEFAULT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))),
    "configs", "dms.yaml")

# 默认值与 configs/dms.yaml 内容一致（yaml 缺失时行为不变）。
DEFAULTS: dict[str, Any] = {
    "system": {"debug": False},
    "camera": {
        "source": "usb:0",
        "width": 1280,
        "height": 720,
        "fps": 30,
        "open_timeout_s": 8.0,
    },
    "runtime": {"headless": False, "state_dump_interval_s": 2.0},
    "fatigue": {
        "enabled": True,
        "backend": "landmarks",
        "model_asset": "models/mediapipe/face_landmarker.task",
        "haar_dir": "",
        "face": {
            "scale_factor": 1.1,
            "min_neighbors": 5,
            "min_size_px": 48,
            "min_face_h_px": 48,
            "face_lost_warn_s": 2.0,
        },
        "roi": {
            "eye_x": [0.15, 0.85],
            "eye_y": [0.20, 0.52],
            "mouth_x": [0.28, 0.72],
            "mouth_y": [0.62, 0.92],
        },
        "signal": {
            "eye_dark_thresh": 60,
            "mouth_dark_thresh": 55,
            "eye_dark_enter": 0.55,
            "mouth_open_enter": 0.45,
            "cascade_crosscheck": False,
        },
        "state_machine": {
            "window_frames": 90,
            "ema_alpha": 0.25,
            "w_eye": 1.0,
            "w_mouth": 1.0,
            "warn_enter": 0.60,
            "alarm_enter": 0.80,
            "exit_enter": 0.35,
            "warn_confirm_frames": 15,
            "alarm_confirm_frames": 15,
            "exit_confirm_frames": 45,
            "warn_hold_s": 5.0,
        },
        "thresholds": {
            "signal_ema_alpha": 0.35,
            "eye_closure_enter": 0.45, "eye_warn_s": 1.5,
            "eye_alarm_s": 3.0, "perclos_warn": 0.40,
            "perclos_alarm": 0.55, "perclos_min_window_s": 10.0,
            "yawn_open_enter": 0.30,
            "yawn_warn_s": 1.5, "yawn_alarm_s": 3.0, "yawn_alarm_count": 3,
            "recovery_s": 1.0,
            "pose_yaw_deg": 30.0, "pose_pitch_deg": 25.0,
            "pose_warn_s": 3.0, "pose_alarm_s": 5.0,
        },
    },
    "helmet": {
        "enabled": True,
        "backend": "model",
        "engine": "models/tensorrt/ppe_hard_hat_fp16.engine",
        "manifest": "models/manifests/ppe_hard_hat.experimental.json",
        "input_size": 640,
        "confidence": 0.45,
        "iou": 0.50,
        "association_iou": 0.15,
        # 人体检测器的分数阈值（契约 §4.10 冻结键名；它不是对演示输出的结论）
        "person_confidence": 0.45,
        "person_iou": 0.50,
        "person_imgsz": 640,
        "head_roi_frac": 0.30,
        "head_roi_w_frac": 0.70,
        "head_roi_min_px": 12,
        "roi_margin_px": 4,
        "min_person_h_px": 80,
        "min_head_area_px": 400,
        "rule": {
            "worn_color_ratio": 0.35,
            "worn_skin_max": 0.25,
            "not_worn_skin_ratio": 0.45,
            "no_color_ratio": 0.05,
            "dark_max": 0.60,
        },
        "colors": {
            "yellow": {"h": [20, 35], "s_min": 70, "v_min": 70, "enabled": True},
            "orange": {"h": [8, 20], "s_min": 70, "v_min": 70, "enabled": True},
            "red": {"h": [[0, 8], [170, 179]], "s_min": 70, "v_min": 70,
                    "enabled": True},
            "blue": {"h": [95, 125], "s_min": 70, "v_min": 70, "enabled": True},
            "white": {"h": [0, 179], "s_min": 0, "s_max": 40, "v_min": 170,
                      "enabled": True},
        },
        "skin": {"cr": [133, 173], "cb": [77, 127]},
        "smooth": {"k_verdict_frames": 5},
    },
    "web": {
        # 8000 已被 Calibration Studio 占用，禁止使用。
        "port": 8010,
        "host": "0.0.0.0",
    },
    "ui": {
        "panel_x": 16,
        "panel_y": 96,
        "line_h": 26,
        "font_scale": 0.55,
        "hotkey_fatigue": "1",
        "hotkey_helmet": "2",
    },
    "logger": {"enabled": True, "dir": "logs", "events_file": "dms_events.jsonl"},
    "notices": {
        "cn": "演示级实现：未做 PERCLOS 标定；无近红外相机时夜间/强逆光不可用；"
              "结果不构成安全认证。",
        "ascii": "DEMO ONLY: no PERCLOS calibration; no IR camera -> "
                 "unusable at night; not a certified safety device.",
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


class DmsConfig:
    """Typed-ish accessor over the merged config dict."""

    def __init__(self, data: dict, raw: dict | None = None) -> None:
        self.data = data
        # 文件里显式写过的键（用于区分 cli / config / default 来源）
        self.raw = raw if raw is not None else {}

    def get(self, path: str, default: Any = None) -> Any:
        node: Any = self.data
        for key in path.split("."):
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def has_raw(self, path: str) -> bool:
        node: Any = self.raw
        for key in path.split("."):
            if not isinstance(node, dict) or key not in node:
                return False
            node = node[key]
        return True

    @property
    def notices_cn(self) -> str:
        return str(self.get("notices.cn", DEFAULTS["notices"]["cn"]))

    @property
    def notices_ascii(self) -> str:
        return str(self.get("notices.ascii", DEFAULTS["notices"]["ascii"]))

    def save(self, path: str) -> None:
        with open(path, "w") as fh:
            yaml.safe_dump(self.data, fh, default_flow_style=False,
                           sort_keys=False, allow_unicode=True)


def load_dms_config(path: str | None = None) -> DmsConfig:
    """Load dms.yaml merged over defaults; missing file => defaults only."""
    p = path or DEFAULT_PATH
    raw: dict = {}
    if p and os.path.exists(p):
        try:
            with open(p) as fh:
                loaded = yaml.safe_load(fh) or {}
            if isinstance(loaded, dict):
                raw = loaded
            else:
                print(f"[dms:config] {p} is not a mapping — using defaults")
        except Exception as exc:  # broken yaml: run defaults, log loudly
            print(f"[dms:config] cannot parse {p}: {exc} — using defaults")
    elif path:
        print(f"[dms:config] cannot read {p}: file does not exist — "
              "using defaults")
    return DmsConfig(_deep(DEFAULTS, raw), raw=raw)
