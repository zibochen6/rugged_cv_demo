"""Core (AI-free) unit tests for the warning subsystem.

Run:  .venv/bin/python -m pytest tests/test_warning_core.py -q
These cover the deterministic math/state parts: validity filtering, percentile
distance sampling (noise robustness), optimized expected-ground depth vs the
per-point reference, EMA/voting, velocity regression, TTC gating, risk state
machine + hysteresis + watchdog, ROI polygon.
"""
from __future__ import annotations

import numpy as np
import pytest

import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.warning.depth_filters import validity_mask, invalidate, spatial_smooth  # noqa: E402
from app.warning.obstacles import extract_candidates, nearest_candidate  # noqa: E402
from app.warning.temporal import DistanceEMA, PresenceVoter  # noqa: E402
from app.warning.velocity_ttc import VelocityTTC  # noqa: E402
from app.warning.risk_engine import RiskEngine, SAFE, WARNING, DANGER, SYSTEM_ERROR  # noqa: E402
from app.warning.roi import DangerRegion  # noqa: E402
from app.warning.config import load_config, DEFAULTS  # noqa: E402


def test_validity_mask_rejects_garbage():
    d = np.array([[0.5, 0.0, np.nan, np.inf, -1.0, 15.0, 0.25, 10.0]],
                 dtype=np.float32)
    m = validity_mask(d, 0.3, 10.0)
    assert m.tolist()[0] == [True, False, False, False, False, False,
                             False, True]


def test_spatial_median_kills_isolated_spike():
    d = np.ones((9, 9), dtype=np.float32) * 2.0
    d[4, 4] = 0.2              # single-pixel noise spike
    out = spatial_smooth(d, kernel=3)
    assert out[4, 4] > 1.5     # median removed the spike
    assert out[0, 0] == pytest.approx(2.0)


def test_percentile_sampling_ignores_min_spike():
    # spec §12: distance must NOT be depth.min(); percentile-10 absorbs a
    # small number of near-zero noise pixels
    h = w = 200
    depth = np.ones((h, w), dtype=np.float32) * 3.0
    mask = np.zeros((h, w), dtype=np.uint8)
    mask[50:150, 50:150] = 1
    mask[90:110, 90:110] = 0    # 400 px hole irrelevant
    rng = np.random.default_rng(0)
    noise = np.zeros((h, w), dtype=bool)
    noise[:60, :60] = True
    depth[noise & (mask > 0)] = 0.01     # < 1% of region: min() would return .01
    cands = extract_candidates(mask, depth, min_area_px=100,
                               percentile=10.0, morphology=False)
    n = nearest_candidate(cands)
    assert n is not None
    assert n.distance_m > 2.5 and n.distance_m <= 3.0


def test_obstacles_area_and_components():
    m = np.zeros((100, 100), dtype=np.uint8)
    m[10:20, 10:20] = 1          # 100 px -> below min_area
    m[40:80, 40:80] = 1          # 1600 px -> kept
    d = np.ones((100, 100), dtype=np.float32) * 2.0
    cands = extract_candidates(m, d, min_area_px=200,
                               percentile=10.0, morphology=False)
    assert len(cands) == 1
    assert cands[0].area_px == 1600


def test_expected_ground_depth_matches_reference():
    from app.geometry.camera_model import CameraModel
    from app.geometry.ground_plane import GroundExtrinsics
    from app.warning.ground_filter import ExpectedGroundDepthFilter
    cam = CameraModel(1280, 720, 1000.0, 1000.0, 640.0, 360.0)
    extr = GroundExtrinsics(height_m=1.5, pitch_deg=25.0)
    f = ExpectedGroundDepthFilter(cam, 0.2, True)
    exp = f.expected_ground_depth(720, 1280)
    # reference: per-point API at a few pixels
    for u, v in [(640, 500), (300, 700), (1000, 600), (640, 380)]:
        x, z = extr.pixel_to_ground(u, v, cam)
        ref = np.sqrt(x * x + z * z + extr.height_m ** 2)  # euclid from camera
        # expected stores camera-forward Z; compare forward Z instead
        rc = extr.ground_point_to_camera(np.array([x, 0.0, z]))
        ref_z = rc[2]
        assert exp[v, u] == pytest.approx(ref_z, rel=1e-4)


def test_ground_filter_marks_closer_pixels_as_obstacle():
    from app.geometry.camera_model import CameraModel
    from app.warning.ground_filter import ExpectedGroundDepthFilter
    cam = CameraModel(640, 360, 500.0, 500.0, 320.0, 180.0)
    f = ExpectedGroundDepthFilter(cam, 0.20, True)
    exp = f.expected_ground_depth(360, 640)
    # below-horizon pixel has finite expected depth; make prediction 40% closer
    h, w = 360, 640
    u, v = 320, 200
    pred = np.full((h, w), np.nan, dtype=np.float32)
    valid = np.isfinite(exp)
    pred[valid] = exp[valid]                      # everywhere ground-like
    pred[v, u] = exp[v, u] * 0.5                  # one closer object
    res = f.apply(pred, valid)
    assert res.obstacle_cand[v, u]
    assert not res.ground_mask[v, u]
    # far pixel still nothing
    v2, u2 = 320, 340
    pred[v2, u2] = exp[v2, u2] * 2.0
    res = f.apply(pred, valid)
    assert not res.obstacle_cand[v2, u2]


def test_danger_region_polygon():
    roi = DangerRegion([[0.2, 1.0], [0.8, 1.0], [0.63, 0.42], [0.37, 0.42]])
    m = roi.mask(720, 1280)
    assert m.shape == (720, 1280)
    assert m[700, 640]        # bottom center inside
    assert not m[100, 640]    # top center outside
    assert not m[700, 50]     # bottom left outside


def test_ema_and_voting():
    ema = DistanceEMA(0.25)
    assert ema.update(3.0) == 3.0
    assert ema.update(2.0) == pytest.approx(2.75)
    assert ema.update(None) == pytest.approx(2.75)
    v = PresenceVoter(5, 3)
    out = [v.update(True), v.update(True), v.update(True),
           v.update(False), v.update(False), v.update(False)]
    assert out == [False, False, True, True, True, False]


def test_velocity_ttc_regression_and_gate():
    vt = VelocityTTC(window_frames=5, min_closing_speed_mps=0.10)
    t0 = 100.0
    closing = None
    for i in range(5):
        d = 5.0 - 0.5 * i          # approaching at 0.5 m/... per 0.1s sample
        closing, ttc = vt.update(d, t0 + i * 0.1)
    assert closing is not None and closing == pytest.approx(5.0, rel=0.05)
    assert ttc is not None and 0.5 < ttc < 2.0
    # gate: too slow -> TTC None
    vt2 = VelocityTTC(window_frames=5, min_closing_speed_mps=0.10)
    for i in range(5):
        closing, ttc = vt2.update(5.0 - 0.001 * i, t0 + i * 0.1)
    assert ttc is None


def test_risk_hysteresis_and_watchdog():
    r = RiskEngine(3.0, 1.5, 3.0, 1.5, 0.3, 0.5, 3)
    # far -> SAFE
    o = r.update(5.0, None, None, 0.0, 5.0, True, True)
    assert o.level == SAFE
    # 2.8 m -> WARNING
    o = r.update(2.8, None, None, 0.0, 5.0, True, True)
    assert o.level == WARNING
    # 3.1 m (within margin, not above 3.3) -> stays WARNING (hysteresis)
    o = r.update(3.1, None, None, 0.0, 5.0, True, True)
    assert o.level == WARNING
    # 3.5 m -> back to SAFE
    o = r.update(3.5, None, None, 0.0, 5.0, True, True)
    assert o.level == SAFE
    # 1.2 m -> DANGER
    o = r.update(1.2, None, None, 0.0, 5.0, True, True)
    assert o.level == DANGER
    # 1.6 m (inside exit margin) -> stays DANGER
    o = r.update(1.6, None, None, 0.0, 5.0, True, True)
    assert o.level == DANGER
    # 2.0 m -> exits DANGER into WARNING (still <3.0)
    o = r.update(2.0, None, None, 0.0, 5.0, True, True)
    assert o.level == WARNING
    # watchdog: 3 bad frames -> SYSTEM ERROR (never SAFE)
    levels = [r.update(2.0, None, None, 0.0, 5.0, False, True).level
              for _ in range(3)]
    assert levels[-1] == SYSTEM_ERROR
    # camera lost
    o = r.update(2.0, None, None, 9.9, 5.0, True, True)
    assert o.level == SYSTEM_ERROR
    # recovery
    o = r.update(5.0, None, None, 0.1, 5.0, True, True)
    assert o.level == SAFE


def test_ttc_rule_triggers_warning():
    r = RiskEngine(3.0, 1.5, 3.0, 1.5, 0.3, 0.5, 3)
    o = r.update(4.5, -1.8, 2.5, 0.0, 5.0, True, True)  # TTC 2.5s
    assert o.level == WARNING
    o = r.update(4.5, -2.8, 1.6, 0.0, 5.0, True, True)  # TTC 1.6s
    assert o.level == WARNING    # above danger_ttc 1.5
    o = r.update(4.5, -3.0, 1.4, 0.0, 5.0, True, True)  # TTC 1.4s
    assert o.level == DANGER


def test_config_defaults_and_merge():
    cfg = load_config(None)  # no file on NX test env -> defaults
    assert cfg.min_depth_m == 0.3
    assert cfg.height_m == 1.5
    assert len(cfg.roi_points) == 4


def test_temporal_filter_recovers_from_all_nan_history():
    from app.warning.depth_filters import TemporalDepthFilter
    tf = TemporalDepthFilter(alpha=0.25)
    nan_map = np.full((4, 4), np.nan, dtype=np.float32)
    bad = np.zeros((4, 4), dtype=bool)
    for _ in range(3):  # e.g. a warmup pass over an all-invalid map
        out = tf.update(nan_map, bad)
    assert not np.isfinite(out).any()
    real = np.ones((4, 4), dtype=np.float32) * 2.0
    good = np.ones((4, 4), dtype=bool)
    out = tf.update(real, good)  # must NOT stay NaN-locked
    assert np.isfinite(out).all()
    assert out[0, 0] == pytest.approx(2.0)  # no valid prev -> take current
    out2 = tf.update(real, good)
    assert out2[0, 0] == pytest.approx(2.0)


def test_pipeline_survives_zero_warmup():
    # regression: warn_app warmup used to feed an all-zero map (all-NaN after
    # invalidate) through process(); the temporal filter then locked the
    # smoothed depth to NaN forever and no obstacle was ever reported
    from app.warn_app import WarningPipeline
    cfg = load_config(None)
    pip = WarningPipeline(cfg)
    h, w = 360, 640
    pip.process(np.zeros((h, w), dtype=np.float32))  # the old warmup call
    exp = pip.ground.expected_ground_depth(h, w)
    pred = np.full((h, w), np.nan, dtype=np.float32)
    valid = np.isfinite(exp)
    pred[valid] = exp[valid]                       # ground-like everywhere
    pred[200:340, 260:380] = exp[200:340, 260:380] * 0.4  # close obstacle
    p = pip.process(pred)
    assert len(p["candidates"]) >= 1
    assert p["nearest"] is not None

if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  {fn.__name__}: PASS")
    print("\nGATE: PASS")
