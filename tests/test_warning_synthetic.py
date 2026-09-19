"""Synthetic perception-chain gate (Test 1/2/5/6 offline equivalents).

Builds synthetic camera-aligned BGR frames + synthetic metric depth:

  A. clear floor        (depth == expected ground depth)
  B. box at 1.0 m in ROI
  C. box at 3.0 m in ROI
  D. ground only but close (no obstacle -> must NOT warn)
  E. single-frame noise spike (must NOT trigger DANGER via voting)
  F. three invalid depth frames -> SYSTEM ERROR

and checks the WarningPipeline candidate extraction + RiskEngine levels. Runs
without a camera and is deterministic.

The scenarios are stateful (DistanceEMA + 3/5 persistence voting + VelocityTTC),
so every test builds its own filter chain instead of sharing one.
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.warning.config import load_config  # noqa: E402
from app.warn_app import WarningPipeline  # noqa: E402
from app.warning.risk_engine import (RiskEngine, SAFE, WARNING,  # noqa: E402
                                     DANGER, SYSTEM_ERROR)
from app.warning.temporal import DistanceEMA, PresenceVoter  # noqa: E402
from app.warning.velocity_ttc import VelocityTTC  # noqa: E402

W, H = 1280, 720


def _cfg_fullres():
    """Config with scale=1.0 so the synthetic scenes (built at full res)
    compare against full-res expected-depth maps (sub-pixel realignment
    at scale<1 would create false edge candidates)."""
    cfg = load_config(None)
    cfg.data.setdefault("pipeline", {})["scale"] = 1.0
    return cfg


def scene(cfg, box_m=None, box_fract=(0.5, 0.68)):
    """Return (bgr_frame, depth_map).

    depth = expected ground except for one rectangular 'box' region whose
    depth is set to box_m (closer than ground -> obstacle).
    """
    pip = WarningPipeline(cfg)
    exp = pip.ground.expected_ground_depth(H, W)
    depth = np.where(np.isfinite(exp), exp, 8.0).astype(np.float32)
    frame = np.zeros((H, W, 3), dtype=np.uint8)
    if box_m is not None:
        cx, cy = int(W * box_fract[0]), int(H * box_fract[1])
        bw, bh = 160, 220
        depth[cy - bh // 2: cy + bh // 2, cx - bw // 2: cx + bw // 2] = box_m
        frame[cy - bh // 2: cy + bh // 2, cx - bw // 2: cx + bw // 2] = 255
    return frame, depth


def _process(cfg, depth):
    return WarningPipeline(cfg).process(depth)


class _Chain:
    """RiskEngine plus the same EMA / 3-of-5 voter / TTC wiring warn_app uses."""

    def __init__(self) -> None:
        self.risk = RiskEngine()
        self.ema = DistanceEMA(0.25)
        self.voter = PresenceVoter(5, 3)
        self.ttc = VelocityTTC()

    def feed(self, result):
        nearest = result["nearest"]
        distance = nearest.distance_m if nearest is not None else None
        filtered = self.ema.update(distance)
        voted = self.voter.update(nearest is not None)
        risk_distance = filtered if voted else None
        if voted and filtered is not None:
            velocity, ttc = self.ttc.update(filtered, 0.0)
        else:
            velocity, ttc = None, None
        return self.risk.update(risk_distance, velocity, ttc, 0.0, 5.0,
                                True, voted).level


def test_clear_floor_is_safe():
    cfg = _cfg_fullres()
    result = _process(cfg, scene(cfg)[1])
    assert result["nearest"] is None, "clear floor must have no candidate"
    assert _Chain().feed(result) == SAFE


def test_box_at_1m_warns_after_voting_persistence():
    cfg = _cfg_fullres()
    result = _process(cfg, scene(cfg, 1.0)[1])
    assert result["nearest"] is not None
    chain = _Chain()
    level = None
    for _ in range(3):          # 3/5 voting needs consecutive presence
        level = chain.feed(result)
    assert level in (WARNING, DANGER), f"box at 1.0m got {level}"


def test_box_at_3m_is_detected():
    cfg = _cfg_fullres()
    result = _process(cfg, scene(cfg, 3.0)[1])
    assert result["nearest"] is not None


def test_ground_within_tolerance_is_not_an_obstacle():
    """Ground reading 15% closer than expected is model bias, not an obstacle."""
    cfg = _cfg_fullres()
    _, depth = scene(cfg)
    biased = np.where(np.isfinite(depth), depth * 0.85, 8.0).astype(np.float32)
    assert _process(cfg, biased)["nearest"] is None


def test_level_settles_back_to_safe_after_the_obstacle_leaves():
    cfg = _cfg_fullres()
    obstacle = _process(cfg, scene(cfg, 1.0)[1])
    clear = _process(cfg, scene(cfg)[1])
    chain = _Chain()
    for _ in range(3):
        chain.feed(obstacle)
    level = None
    for _ in range(3):          # persistence clears after 3 absent frames
        level = chain.feed(clear)
    assert level == SAFE, f"must settle to SAFE, got {level}"


def test_single_frame_spike_does_not_trigger_danger():
    cfg = _cfg_fullres()
    spike = _process(cfg, scene(cfg, 0.5, (0.5, 0.5))[1])
    clear = _process(cfg, scene(cfg)[1])
    chain = _Chain()
    levels = [chain.feed(spike), chain.feed(clear), chain.feed(clear)]
    assert DANGER not in levels, levels


def test_three_invalid_depth_frames_raise_system_error():
    chain = _Chain()
    level = None
    for _ in range(3):
        level = chain.risk.update(None, None, None, 0.0, 5.0, False, True).level
    assert level == SYSTEM_ERROR