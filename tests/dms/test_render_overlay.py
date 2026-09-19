"""渲染契约：ASCII 文案纯 ASCII、映射齐全、面板矩形与 hit-test、None 容错。

UI v2 起 `draw_dms_overlay()` 已被规格 §3 S4/§10 D-D 删除，单入口为
`draw_dms_frame(canvas, frame_result, runtime, *, surface, debug, meta)`；
原 `draw_dms_overlay` 用例全部迁到 `draw_dms_frame`。
"""
import numpy as np

from app.dms import render
from app.dms.fatigue import (STATE_ALARM, STATE_DISABLED, STATE_NORMAL,
                             STATE_UNKNOWN, STATE_WARN, FatigueResult,
                             FatigueSignals)
from app.dms.helmet import HelmetVerdict
from app.dms.render import (NOTICE_ASCII, STATE_TEXT_ASCII, VERDICT_TEXT_ASCII,
                            FrameResult, draw_dms_frame, draw_dms_panel,
                            hit_test, map_mouse_to_canvas, verdict_text_ascii)
from app.dms.state import DmsRuntime

CANVAS = np.zeros((480, 640, 3), dtype=np.uint8)


def test_ascii_constants_are_pure_ascii():
    NOTICE_ASCII.encode("ascii")                       # 不抛异常才算通过
    for text in STATE_TEXT_ASCII.values():
        text.encode("ascii")
    for text in VERDICT_TEXT_ASCII.values():
        text.encode("ascii")


def test_text_maps_cover_all_frozen_states_and_verdicts():
    assert set(STATE_TEXT_ASCII) == {STATE_DISABLED, STATE_UNKNOWN,
                                     STATE_NORMAL, STATE_WARN, STATE_ALARM}
    assert STATE_TEXT_ASCII[STATE_WARN] == "DROWSY WARN"
    assert STATE_TEXT_ASCII[STATE_ALARM] == "DROWSY ALARM"
    assert set(VERDICT_TEXT_ASCII) == {"worn", "not_worn", "unknown"}
    assert verdict_text_ascii("worn") == "WORN"
    assert verdict_text_ascii("not_worn") == "NOT WORN"
    assert verdict_text_ascii("unknown") == "UNKNOWN"
    assert verdict_text_ascii("something_else") == "UNKNOWN"


def test_draw_dms_overlay_is_removed_and_single_entry_point_exists():
    """规格 §3 S4 / §10 D-D：`draw_dms_overlay` 被有意删除，只保留单入口。"""
    assert not hasattr(render, "draw_dms_overlay")
    assert callable(render.draw_dms_frame)
    assert callable(render.draw_dms_panel)
    assert callable(render.hit_test)
    assert callable(render.map_mouse_to_canvas)
    assert callable(render.resolve_control_hit)


def test_draw_panel_returns_two_hit_testable_rects():
    runtime = DmsRuntime(fatigue_enabled=True, helmet_enabled=False)
    canvas = np.zeros((480, 640, 3), dtype=np.uint8)
    rects = draw_dms_panel(canvas, runtime)
    assert [rect.name for rect in rects] == ["fatigue_enabled",
                                             "helmet_enabled"]
    first, second = rects
    assert hit_test(rects, first.x + 2, first.y + 2) == "fatigue_enabled"
    assert hit_test(rects, second.x + 6, second.y + 6) == "helmet_enabled"
    assert hit_test(rects, -50, -50) is None
    assert hit_test([], 10, 10) is None
    # 选中/未选中状态可辨（ON 绿 vs OFF 灰），并且绘制确实落在画布上
    on_rect_center = canvas[first.y + 9, first.x + 9]
    off_rect_center = canvas[second.y + 9, second.x + 9]
    assert int(on_rect_center.sum()) != int(off_rect_center.sum())


def test_frame_tolerates_none_and_disabled_results():
    runtime = DmsRuntime(fatigue_enabled=False, helmet_enabled=False)
    canvas = np.zeros((480, 640, 3), dtype=np.uint8)
    layout = draw_dms_frame(canvas, None, runtime, surface="display")
    assert layout.surface == "display"
    draw_dms_frame(canvas, FrameResult(1, 0.0, None, None), runtime,
                   surface="display")
    assert canvas.sum() > 0
    # 两条 surface 的 None 容错
    draw_dms_frame(canvas, None, runtime, surface="stream")

    runtime.set_flag("fatigue_enabled", True)
    signals = FatigueSignals(face_present=True, face_box=(40, 40, 120, 160),
                             track_id=3, eye_dark_ratio=0.9,
                             mouth_open_ratio=0.8)
    result = FatigueResult(STATE_WARN, 0.7, signals)
    helmet = [HelmetVerdict(track_id=5, bbox=(300, 100, 120, 300),
                            head_roi=(330, 100, 84, 90), verdict="worn",
                            reason="color_evidence", helmet_color_ratio=0.8,
                            skin_ratio=0.0, dark_ratio=0.1, conf="mid",
                            age_frames=9)]
    canvas = np.zeros((480, 640, 3), dtype=np.uint8)
    layout = draw_dms_frame(canvas, FrameResult(2, 0.0, result, helmet),
                            runtime, surface="display")
    assert canvas.sum() > 0
    assert [c.name for c in layout.controls] == ["fatigue_enabled",
                                                 "helmet_enabled"]
    assert draw_dms_panel(canvas, runtime)[0].name == "fatigue_enabled"


def test_draw_dms_frame_is_idempotent_on_same_canvas():
    """同坐标同像素：geometry 层重复绘制不得漂移（web+窗口会双调用）。"""
    runtime = DmsRuntime(fatigue_enabled=True, helmet_enabled=False)
    helmet = [HelmetVerdict(track_id=5, bbox=(300, 100, 120, 300),
                            head_roi=(330, 100, 84, 90), verdict="worn",
                            reason="color_evidence", helmet_color_ratio=0.8,
                            skin_ratio=0.0, dark_ratio=0.1, conf="mid",
                            age_frames=9)]
    frame_result = FrameResult(2, 0.0, None, helmet)
    once = np.full((480, 640, 3), 230, dtype=np.uint8)
    twice = once.copy()
    draw_dms_frame(once, frame_result, runtime, surface="stream")
    draw_dms_frame(twice, frame_result, runtime, surface="stream")
    draw_dms_frame(twice, frame_result, runtime, surface="stream")
    assert np.array_equal(once, twice)


def test_stream_geometry_uses_high_contrast_status_tags():
    """Stream labels need solid dark plates and thicker boxes on bright video."""
    runtime = DmsRuntime(fatigue_enabled=True, helmet_enabled=True)
    signals = FatigueSignals(face_present=True, face_box=(40, 80, 180, 180),
                             track_id=3, eye_dark_ratio=0.2,
                             mouth_open_ratio=0.1)
    fatigue = FatigueResult(STATE_NORMAL, 0.0, signals)
    helmet = [HelmetVerdict(track_id=5, bbox=(300, 100, 180, 300),
                            head_roi=(340, 100, 100, 100), verdict="unknown",
                            reason="color_evidence", helmet_color_ratio=0.1,
                            skin_ratio=0.1, dark_ratio=0.1, conf="low",
                            age_frames=9)]
    canvas = np.full((720, 1280, 3), 245, dtype=np.uint8)
    draw_dms_frame(canvas, FrameResult(2, 0.0, fatigue, helmet), runtime,
                   surface="stream")
    # Tags are solid navy plates, not low-contrast colored text on video.
    dark_plate = np.all(canvas < 50, axis=2)
    assert int(dark_plate.sum()) > 1500
    # 3px main boxes leave a visible status-colored vertical edge.
    assert np.any(np.all(canvas[130:250, 300:303] == render.COLOR_UNKNOWN,
                         axis=2))


def test_driver_fatigue_alert_uses_low_battery_icon_instead_of_long_label(monkeypatch):
    captured = []
    original = render._put_text

    def collect(*args, **kwargs):
        captured.append(args[1])
        return original(*args, **kwargs)

    monkeypatch.setattr(render, "_put_text", collect)
    runtime = DmsRuntime(fatigue_enabled=True, helmet_enabled=False)
    signals = FatigueSignals(face_present=True, face_box=(180, 180, 180, 180),
                             track_id=1, eye_dark_ratio=None,
                             mouth_open_ratio=0.8, mouth_open=True,
                             mouth_open_duration_s=3.2, reason="YAWN")
    fatigue = FatigueResult(STATE_ALARM, 1.0, signals)
    canvas = np.zeros((720, 960, 3), dtype=np.uint8)
    runtime.set_fatigue_view(fatigue)
    draw_dms_frame(canvas, FrameResult(1, 0.0, fatigue, None), runtime,
                   surface="stream")
    assert not [text for text in captured if "FATIGUE" in text or "DRIVER |" in text]
    assert np.any(np.all(canvas == render.COLOR_ALARM, axis=2))


def test_map_mouse_to_canvas_handles_scaling_and_fallback():
    assert map_mouse_to_canvas(320, 240, (0, 0, 640, 480), (480, 640)) == \
        (320, 240)
    assert map_mouse_to_canvas(100, 50, (0, 0, 1280, 960), (480, 640)) == \
        (50, 25)
    assert map_mouse_to_canvas(7, 9, None, (480, 640)) == (7, 9)
    assert map_mouse_to_canvas(7, 9, (0, 0, 0, 0), (480, 640)) == (7, 9)
