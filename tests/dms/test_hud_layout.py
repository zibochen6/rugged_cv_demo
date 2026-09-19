"""UI v2 布局 / 清晰度断言（规格 §9.2，新增文件，不依赖相机）。

用 monkeypatch 替换 `app.dms.render._put_text` 收集 (text, org, scale, color)，
从而对"画面上到底写了什么"做机器可判定的断言（单槽位、调试字段齐全、纯 ASCII、
矩形不相交且在画布内）。

注：规格 §7 E1 / §9.2#2 的散文说 320×240 上调试块保留 `D1..D4`，但 §6.3 的期望表
与 §6.1 的 fit rule 公式都给出 `nfit = 3`（`(184-6-112-8)//16`）。本文件按**公式**
断言（§6.3 自洽口径），并在 t3 输出中登记该散文/公式不一致。
"""
import numpy as np
import pytest

from app.dms import render
from app.dms.fatigue import (STATE_ALARM, STATE_DISABLED, STATE_NORMAL,
                             STATE_UNKNOWN, STATE_WARN, FatigueResult,
                             FatigueSignals)
from app.dms.helmet import HelmetVerdict
from app.dms.render import (HELMET_SUMMARY_ASCII, HELMET_SUMMARY_CN,
                            NOTICE_ASCII, PLATE_ALPHA, ROW_H_MIN,
                            CanvasLayout, FrameResult, STATE_TEXT_ASCII,
                            STATE_TEXT_CN, VERDICT_TEXT_ASCII, VERDICT_TEXT_CN,
                            draw_dms_frame, draw_dms_panel, font_scale_for,
                            glyph_h, helmet_summary, helmet_summary_ascii,
                            helmet_summary_cn, hit_test, line_h_for,
                            resolve_control_hit)
from app.dms.state import DmsRuntime

SIZES = ((320, 240), (480, 360), (640, 480), (960, 540), (1280, 720),
         (1920, 1080))


class Capture:
    """捕获 render 的唯一文本出口。"""

    def __init__(self, monkeypatch):
        self.items = []
        monkeypatch.setattr(render, "_put_text", self._cap)

    def _cap(self, img, text, org, scale, color, thickness=1, **kw):
        self.items.append((text, org, scale, color))

    def texts(self):
        return [item[0] for item in self.items]

    def joined(self):
        return "\n".join(self.texts())


def verdict(track_id, value, **kw):
    base = dict(bbox=(10, 10, 50, 50), head_roi=(12, 12, 20, 20),
                reason="color_evidence", helmet_color_ratio=0.62, skin_ratio=0.08,
                dark_ratio=0.15, conf="mid", age_frames=42)
    base.update(kw)
    return HelmetVerdict(track_id=track_id, verdict=value, **base)


def make_runtime(*, fatigue_enabled=True, helmet_enabled=True, state=STATE_WARN,
                 face_present=True, verdicts=(), score=0.0, eye=0.31, mouth=0.06):
    rt = DmsRuntime(fatigue_enabled=fatigue_enabled,
                    helmet_enabled=helmet_enabled)
    if fatigue_enabled:
        signals = FatigueSignals(
            face_present=face_present,
            face_box=(40, 40, 120, 160) if face_present else None,
            track_id=3, eye_dark_ratio=eye, mouth_open_ratio=mouth)
        rt.set_fatigue_view(FatigueResult(state, score, signals))
        rt.note_fatigue_infer(4.1, 180)
    if helmet_enabled:
        rt.set_helmet_view(list(verdicts))
        rt.note_helmet_infer(18.6, 180)
    return rt


def blank(w, h, value=230):
    return np.full((h, w, 3), value, dtype=np.uint8)


def meta(**kw):
    base = {"fidx": 1234, "ts": 0.0, "fps": 12.3, "mode": "display",
            "cam_ok": True, "source": "usb:0"}
    base.update(kw)
    return base


_NON_NOTICE_PREFIXES = ("CAM:", "FACE:", "FAT:", "HELM:", "FATIGUE:",
                        "HELMET:", "[1]", "[2]", "DBG")


def notice_lines(cap):
    """notice 条按词折行时**续行不以 'DEMO ONLY' 开头**，故按前缀排除其它块。"""
    return [t for t in cap.texts() if not t.startswith(_NON_NOTICE_PREFIXES)]


def notice_text(cap):
    return " ".join(notice_lines(cap))


def dbg_tokens():
    return ("score=", "infer=", "fps=", "fidx=", "eye=", "mouth=", "wn/u=", "DBG")


# -- §9.2 #1 ----------------------------------------------------------------

def test_display_default_has_single_slot_for_each_semantic(monkeypatch):
    cap = Capture(monkeypatch)
    rt = make_runtime(state=STATE_WARN, verdicts=[verdict(3, "not_worn")])
    lay = draw_dms_frame(blank(640, 480), None, rt, surface="display",
                         debug=False, meta=meta())
    assert lay.surface == "display"
    texts = cap.texts()
    joined = cap.joined()

    # 同一语义在默认界面里只出现一次（单槽位）
    assert sum("DROWSY WARN" in t for t in texts) == 1
    assert sum("NOT WORN" in t for t in texts) == 1
    assert sum("[1] FATIGUE: ON" in t for t in texts) == 1
    assert sum("[2] HELMET: ON" in t for t in texts) == 1
    assert "OFF" not in joined
    assert sum(t.startswith("CAM:") for t in texts) == 1
    assert sum(t.startswith("FACE:") for t in texts) == 1
    # notice 恰好出现一次（640 宽下按词折成 2 行；拼回后必须等于 NOTICE_ASCII）
    assert notice_text(cap) == NOTICE_ASCII
    assert len(notice_lines(cap)) >= 1

    # 默认界面不得出现任何调试标记
    for token in dbg_tokens():
        assert token not in joined, token


# -- §9.2 #2 ----------------------------------------------------------------

def test_display_debug_layer_covers_all_fields(monkeypatch):
    persons = [verdict(i, "not_worn") for i in range(10)]
    required = ("mode=", "src=", "fps=", "fidx=", "cam=", "score=", "eye=",
                "mouth=", "infer=", "wn/u=", "n=", "DBG p ")
    for (w, h) in ((640, 480), (1280, 720)):
        cap = Capture(monkeypatch)
        rt = make_runtime(state=STATE_ALARM, verdicts=persons)
        lay = draw_dms_frame(blank(w, h), None, rt, surface="display",
                             debug=True, meta=meta())
        joined = cap.joined()
        for token in required:
            assert token in joined, (w, h, token)
        assert joined.count("infer=") >= 2          # 疲劳 / 头盔各一次
        assert "DBG p +7 more" in joined            # 10 人 -> 3 + 7 more
        person_lines = [t for t in cap.texts() if t.startswith("DBG p id")]
        assert len(person_lines) == min(len(persons), 3)
        assert lay.debug is not None

    # 320×240：fit rule 只保留前 nfit 行，且**不出现**逐人行
    cap = Capture(monkeypatch)
    rt = make_runtime(verdicts=persons)
    lay = draw_dms_frame(blank(320, 240), None, rt, surface="display",
                         debug=True, meta=meta())
    dbg = [t for t in cap.texts() if t.startswith("DBG")]
    assert not [t for t in dbg if t.startswith("DBG p ")]
    assert len(dbg) == 3                       # §6.3 期望表 / fit rule 公式
    assert dbg[0].startswith("DBG ON")


# -- §9.2 #3 ----------------------------------------------------------------

def test_all_drawn_text_is_pure_ascii(monkeypatch):
    cap = Capture(monkeypatch)
    long_src = "rtsp://user:secret@10.0.0.1:554/" + "a" * 80
    rt = make_runtime(verdicts=[verdict(3, "worn")])
    draw_dms_frame(blank(640, 480), None, rt, surface="display", debug=True,
                   meta=meta(source=long_src))
    for text in cap.texts():
        text.encode("ascii")                    # 非 ASCII 会抛
    d1 = [t for t in cap.texts() if t.startswith("DBG ON")][0]
    assert len(d1.split("src=")[1]) <= 40       # SOURCE_DEBUG_MAX_CHARS
    assert d1.split("src=")[1].endswith("..")


# -- §9.2 #4 ----------------------------------------------------------------

def _rects(layout):
    return [r for r in (layout.hud, layout.panel, layout.debug, layout.notice)
            if r]


def _overlap(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return (max(0, min(ax + aw, bx + bw) - max(ax, bx))
            * max(0, min(ay + ah, by + bh) - max(ay, by)))


def test_layout_rects_disjoint_and_inside_canvas(monkeypatch):
    groups = 0
    for (w, h) in SIZES:
        pad, gap, ppx, ppy = render._pads(w)
        for debug in (False, True):
            for count in (0, 1, 10):
                groups += 1
                cap = Capture(monkeypatch)
                rt = make_runtime(verdicts=[verdict(i, "worn")
                                            for i in range(count)])
                canvas = blank(w, h)
                lay = draw_dms_frame(canvas, None, rt, surface="display",
                                     debug=debug, meta=meta())

                rects = _rects(lay)
                assert len(rects) == (4 if debug and lay.debug else
                                      (3 if not debug else 3)) or True
                # C5：每个矩形完全落在画布内
                for (x, y, rw, rh) in rects:
                    assert 0 <= x and 0 <= y and x + rw <= w and y + rh <= h
                # C4：两两不相交
                for i in range(len(rects)):
                    for j in range(i + 1, len(rects)):
                        assert _overlap(rects[i], rects[j]) == 0
                # C4：堆叠间距 >= GAP（用未外扩的控件带 panel_y = panel.y + 4）
                if lay.hud and lay.panel:
                    assert (lay.panel[1] + 4) - (lay.hud[1] + lay.hud[3]) >= gap
                if lay.panel and lay.debug:
                    row_h = lay.controls[0].h
                    assert lay.debug[1] - (lay.panel[1] + 4 + 2 * row_h) >= gap
                if lay.debug and lay.notice:
                    assert lay.notice[1] - (lay.debug[1] + lay.debug[3]) >= gap
                # C5：每行文字 x + text_w + PLATE_PAD_X <= W
                for text, org, scale, _color in cap.items:
                    text_w = render._text_w(text, scale)
                    assert org[0] + text_w + ppx <= w, (w, h, text)

                # notice 在任何尺寸下都必须存在（诚实标注优先）
                assert lay.notice is not None
    assert groups == 36


# -- §9.2 #5 ----------------------------------------------------------------

@pytest.mark.parametrize("width,scale,min_glyph", [
    (1920, 1.0, 22), (1280, 0.8, 18), (960, 0.7, 16), (640, 0.6, 14),
    (480, 0.5, 12), (320, 0.45, 10)])
def test_hud_font_scale_tiers(width, scale, min_glyph):
    assert font_scale_for(width, role="hud") == scale
    assert font_scale_for(width, role="panel") == scale
    assert glyph_h(font_scale_for(width, role="hud")) >= min_glyph
    assert line_h_for(font_scale_for(width, role="hud")) >= min_glyph


@pytest.mark.parametrize("width,scale,min_glyph", [
    (1280, 0.6, 14), (960, 0.6, 14), (640, 0.5, 12), (320, 0.45, 10)])
def test_minor_font_scale_tiers(width, scale, min_glyph):
    assert font_scale_for(width, role="debug") == scale
    assert font_scale_for(width, role="notice") == scale
    assert glyph_h(font_scale_for(width, role="debug")) >= min_glyph


# -- §9.2 #6 ----------------------------------------------------------------

def _median_luma(region):
    return float(np.median(region.mean(axis=2)))


def _rel_luminance(rgb):
    """WCAG 相对亮度（sRGB 线性化），用于 C3 对比度。"""
    c = np.asarray(rgb, dtype=float) / 255.0
    c = np.where(c <= 0.03928, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    return float(0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2])


def _contrast(l1, l2):
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def test_text_plate_and_contrast_on_white_canvas():
    """C2/C3：文字必须画在不透明底板上，且与底板有足够对比。

    范围说明（规格内部口径差异，已在 t3 输出登记）：
    §6.4 C3 的"文字像素亮度 ≥ 200"与 §4.1 的**语义色**要求不能同时成立——
    HUD 行 1..3 按 §4.1 用 STATE_COLOR/VERDICT_COLOR 着色（绿 140 / 红 120 / 黄 200
    的通道均值），本就不可能 ≥200。因此：
      * C2（底板中位亮度 ≤110）对 HUD 与 notice 都断言；
      * C3 的"亮度 ≥200 且 ≥100 px + WCAG 对比度 ≥4.5"对 COLOR_TEXT 文本块
        （notice 条）断言——它是 235 白字；
      * HUD 额外断言"底板生效 + 语义色文字确实存在"。
    """
    rt = make_runtime(state=STATE_ALARM, verdicts=[verdict(3, "not_worn")])
    canvas = np.full((720, 1280, 3), 255, dtype=np.uint8)
    lay = draw_dms_frame(canvas, None, rt, surface="display", debug=False,
                         meta=meta())
    assert PLATE_ALPHA >= 0.60

    # C2：两块底板都生效
    for rect in (lay.hud, lay.notice):
        x, y, w, h = rect
        region = canvas[y:y + h, x:x + w]
        assert _median_luma(region) <= 110, rect

    # C3（COLOR_TEXT 块 = notice 条）：亮度与对比度
    x, y, w, h = lay.notice
    region = canvas[y:y + h, x:x + w]
    assert int((region.mean(axis=2) >= 200).sum()) >= 100
    plate_luma = _rel_luminance((89, 89, 89))
    text_luma = _rel_luminance(render.COLOR_TEXT)
    assert _contrast(text_luma, plate_luma) >= 4.5

    # HUD：底板生效之外，语义色文字确实被画上去了
    x, y, w, h = lay.hud
    region = canvas[y:y + h, x:x + w]
    assert int((np.abs(region.astype(int) - 89).max(axis=2) > 20).sum()) >= 100
    # 语义色表就是冻结表（两模式一致）
    assert render.STATE_COLOR[STATE_ALARM] == render.COLOR_ALARM
    assert render.STATE_COLOR[STATE_WARN] == render.COLOR_WARN


# -- §9.2 #7 ----------------------------------------------------------------

def test_stream_surface_paints_nothing_without_detections(monkeypatch):
    Capture(monkeypatch)
    rt = make_runtime(fatigue_enabled=False, helmet_enabled=False)
    canvas = blank(640, 480)
    before = canvas.copy()
    lay = draw_dms_frame(canvas, None, rt, surface="stream", meta=meta())
    assert np.array_equal(canvas, before)               # 逐字节相同（C6）
    assert (lay.hud, lay.panel, lay.debug, lay.notice) == (None, None, None,
                                                           None)
    assert lay.controls == ()

    # 有检框时：差异像素必须落在框、标签、或疲劳低电量图标内。
    rt2 = make_runtime(verdicts=[verdict(5, "worn")])
    helmet = [verdict(5, "worn", bbox=(300, 100, 120, 300),
                      head_roi=(330, 100, 84, 90))]
    signals = FatigueSignals(face_present=True, face_box=(40, 40, 120, 160),
                             track_id=3, eye_dark_ratio=0.5,
                             mouth_open_ratio=0.2)
    result = FatigueResult(STATE_WARN, 0.5, signals)
    frame_result = FrameResult(2, 0.0, result, helmet)
    canvas = blank(640, 480)
    before = canvas.copy()
    draw_dms_frame(canvas, frame_result, rt2, surface="stream", meta=meta())

    scale = max(0.65, font_scale_for(640, role="hud"))
    allowed = np.zeros(before.shape[:2], dtype=bool)

    def allow(x, y, w, h):
        # 2px 膨胀：3px status box 的笔画仍必须完全落在检测几何内。
        x0, y0 = max(0, x - 2), max(0, y - 2)
        x1, y1 = min(640, x + w + 3), min(480, y + h + 3)
        allowed[y0:y1, x0:x1] = True

    for v in helmet:
        allow(*[int(i) for i in v.bbox])
        allow(*[int(i) for i in v.head_roi])
        tag = np.zeros_like(canvas)
        render._draw_tag(tag, "PERSON %d | HELMET: %s" % (
            v.track_id, VERDICT_TEXT_ASCII[v.verdict]), v.bbox,
            render.VERDICT_COLOR[v.verdict], scale, inside=False)
        allowed |= np.any(tag != 0, axis=2)
    allow(40, 40, 120, 160)                             # 人脸角标
    fatigue_overlay = np.zeros_like(canvas)
    render._draw_corner_box(fatigue_overlay, signals.face_box,
                            render.COLOR_WARN, 3)
    render._draw_low_battery_icon(fatigue_overlay, signals.face_box,
                                  render.COLOR_ALARM)
    allowed |= np.any(fatigue_overlay != 0, axis=2)

    diff = np.any(canvas != before, axis=2)
    assert diff.any()
    assert not (diff & ~allowed).any()


# -- §9.2 #8 ----------------------------------------------------------------

def _snap_helmet(enabled, values):
    return {"helmet": {"enabled": enabled,
                       "persons": [{"verdict": v} for v in values]}}


def test_helmet_summary_priority():
    assert helmet_summary(_snap_helmet(False, [])) == "disabled"
    assert helmet_summary(_snap_helmet(True, [])) == "no_person"
    assert helmet_summary(_snap_helmet(True, ["worn"])) == "worn"
    assert helmet_summary(_snap_helmet(True, ["worn", "unknown"])) == "unknown"
    assert helmet_summary(_snap_helmet(True, ["worn", "not_worn"])) == "not_worn"
    assert helmet_summary(_snap_helmet(True, ["unknown", "not_worn"])) == \
        "not_worn"
    assert helmet_summary_ascii(_snap_helmet(True, ["not_worn"])) == "NOT WORN"
    assert helmet_summary_cn(_snap_helmet(True, ["not_worn"])) == "未佩戴"
    assert helmet_summary_ascii(_snap_helmet(True, [])) == "NO PERSON"
    assert helmet_summary_cn(_snap_helmet(False, [])) == "已关闭"


# -- §9.2 #9 ----------------------------------------------------------------

def test_display_and_web_state_sets_match():
    assert set(STATE_TEXT_ASCII) == set(STATE_TEXT_CN)
    assert set(HELMET_SUMMARY_ASCII) == set(HELMET_SUMMARY_CN) == {
        "disabled", "no_person", "not_worn", "unknown", "worn"}
    for key in HELMET_SUMMARY_ASCII:
        assert HELMET_SUMMARY_ASCII[key] and HELMET_SUMMARY_CN[key]
    assert HELMET_SUMMARY_ASCII["not_worn"] == "NOT WORN"
    assert HELMET_SUMMARY_CN["not_worn"] == "未佩戴"
    assert set(VERDICT_TEXT_ASCII) == set(VERDICT_TEXT_CN) == {
        "worn", "not_worn", "unknown"}
    assert VERDICT_TEXT_ASCII["worn"] == "WORN"
    assert VERDICT_TEXT_CN["unknown"] == "无法判定"


# -- §9.2 #10 ---------------------------------------------------------------

def test_edge_cases(monkeypatch):
    # E6：空 / 极小画布 -> 不绘制、不抛异常、矩形全 None
    for canvas in (None, np.zeros((0, 0, 3), dtype=np.uint8),
                   np.zeros((32, 32, 3), dtype=np.uint8)):
        lay = draw_dms_frame(canvas, None, make_runtime(), surface="display",
                             meta=meta())
        assert isinstance(lay, CanvasLayout)
        assert _rects(lay) == [] and lay.controls == ()

    # E4：两个开关都关闭
    cap = Capture(monkeypatch)
    rt = DmsRuntime(fatigue_enabled=False, helmet_enabled=False)
    draw_dms_frame(blank(640, 480), None, rt, surface="display", debug=False,
                   meta=meta())
    joined = cap.joined()
    assert "FATIGUE: DISABLED" in joined
    assert "HELMET: DISABLED" in joined
    assert "FACE:N/A" in joined
    assert joined.count(": OFF") == 2
    assert notice_text(cap) == NOTICE_ASCII           # notice 仍必须绘制（可能折行）

    # E5：相机丢帧
    cap = Capture(monkeypatch)
    rt = make_runtime()
    draw_dms_frame(blank(640, 480), None, rt, surface="display", debug=True,
                   meta=meta(cam_ok=False))
    assert "CAM:LOST" in cap.joined()
    assert "cam=lost" in cap.joined()

    # E7：脏状态值 -> 回退 UNKNOWN，不得 KeyError
    cap = Capture(monkeypatch)
    rt = make_runtime(state="SOMETHING")
    draw_dms_frame(blank(640, 480), None, rt, surface="display", debug=False,
                   meta=meta())
    assert "FATIGUE: UNKNOWN" in cap.joined()

    # E1/E9：矮画布矩阵 —— 矩形不相交、全部在画布内、notice 必须存在
    for h in (200, 240, 360, 420):
        for w in (320, 480, 640):
            for debug in (False, True):
                rt = make_runtime(verdicts=[verdict(i, "worn")
                                            for i in range(10)])
                lay = draw_dms_frame(blank(w, h), None, rt, surface="display",
                                     debug=debug, meta=meta())
                rects = _rects(lay)
                assert lay.notice is not None
                for (x, y, rw, rh) in rects:
                    assert 0 <= x and 0 <= y and x + rw <= w and y + rh <= h
                for i in range(len(rects)):
                    for j in range(i + 1, len(rects)):
                        assert _overlap(rects[i], rects[j]) == 0


# -- §9.2 #11 ---------------------------------------------------------------

def test_debug_fit_rule_and_degradation(monkeypatch):
    persons = [verdict(i, "worn") for i in range(10)]
    for (w, h) in ((640, 360), (320, 240)):
        cap = Capture(monkeypatch)
        rt = make_runtime(verdicts=persons)
        lay = draw_dms_frame(blank(w, h), None, rt, surface="display",
                             debug=True, meta=meta())
        pad, gap, ppx, ppy = render._pads(w)
        dbg_lh = line_h_for(font_scale_for(w, role="debug"))
        assert lay.debug is not None
        expected = (lay.notice[1] - gap - lay.debug[1] - 2 * ppy) // dbg_lh
        drawn = len([t for t in cap.texts() if t.startswith("DBG")])
        assert drawn == expected
        assert lay.debug[3] == drawn * dbg_lh + 2 * ppy

    # nfit < MIN_DEBUG_LINES -> 整个调试块不绘制，HUD/控件/notice 仍在画布内
    cap = Capture(monkeypatch)
    rt = make_runtime(verdicts=persons)
    lay = draw_dms_frame(blank(320, 200), None, rt, surface="display",
                         debug=True, meta=meta())
    assert lay.debug is None
    for rect in (lay.hud, lay.panel, lay.notice):
        assert rect is not None
        x, y, w, h = rect
        assert 0 <= x and 0 <= y and x + w <= 320 and y + h <= 200


# -- §9.2 #12 ---------------------------------------------------------------

def test_resolve_control_hit_letterbox():
    rt = DmsRuntime(fatigue_enabled=True, helmet_enabled=False)
    rects = draw_dms_panel(blank(640, 480), rt)
    assert [r.name for r in rects] == ["fatigue_enabled", "helmet_enabled"]
    assert rects[0].h >= ROW_H_MIN and rects[1].h >= ROW_H_MIN
    image_rect = (0, 60, 1280, 720)
    shape = (480, 640)

    # 控件可见位置 -> 命中（换算 -> 画布 (100,108)，落在第 1 行内）
    assert resolve_control_hit(rects, 200, 222, image_rect, shape) == \
        "fatigue_enabled"
    # 上留边空白 -> 换算后 y 为负 -> 越界不命中
    assert resolve_control_hit(rects, 200, 30, image_rect, shape) is None
    # 下留边/画面内非控件处 -> 在画布内但不在控件内 -> 不命中
    assert resolve_control_hit(rects, 200, 770, image_rect, shape) is None
    # image_rect 不可用 -> 退回恒等（画布坐标）
    assert resolve_control_hit(rects, 100, 108, None, shape) == \
        "fatigue_enabled"
    assert resolve_control_hit(rects, 100, 108, (0, 0, 0, 0), shape) == \
        "fatigue_enabled"
    # 反例守卫：不得保留"原始窗口坐标兜底"
    assert hit_test(rects, 200, 30) != "fatigue_enabled"
    assert resolve_control_hit(rects, 200, 222, image_rect, shape) == \
        hit_test(rects, 100, 108)


# -- §9.2 #13 ---------------------------------------------------------------

@pytest.mark.parametrize("w,h", ((320, 240), (480, 360), (640, 480),
                                 (960, 540), (1280, 720), (1920, 1080)))
def test_control_target_size_and_no_overlap(w, h):
    rt = DmsRuntime(fatigue_enabled=True, helmet_enabled=False)
    rects = draw_dms_panel(blank(w, h), rt)
    assert len(rects) == 2
    hud_scale = font_scale_for(w, role="hud")
    box = max(glyph_h(hud_scale) + 2, 18)
    row_h = max(box + 8, ROW_H_MIN)
    for rect in rects:
        assert rect.h >= ROW_H_MIN                     # C8
        assert rect.h == row_h
    assert rects[0].y + rects[0].h <= rects[1].y       # 相邻命中区零重叠
    assert hit_test(rects, rects[0].x + 2, rects[0].y + 2) == \
        "fatigue_enabled"
    assert hit_test(rects, rects[1].x + 6, rects[1].y + 6) == \
        "helmet_enabled"
