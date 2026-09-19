"""Overlay rendering for the DMS demo (UI v2: single compact HUD + debug layer).

硬约束（契约 H11，已核实）: cv2.putText **无法渲染中文**。因此 display 画面文字
一律 ASCII；中文只出现在 web 页面 DOM、docs、tests 与 /state.notices。

两套 surface profile（单一合成边界，禁止第二套绘制实现）:
  surface="stream"  -> 极简驾驶员状态徽标 + 风险框；**零** HUD/调试/notice/控件像素
  surface="display" -> 极简驾驶员状态 + HUD + 控件行 + (debug) 调试块 + notice 条

布局模型见规格 §6.1/§6.2：先算底部 notice（可用区上界），再自上而下堆叠
HUD -> 控件行 -> 调试块；全部矩形两两不相交且完全落在画布内。

颜色约定（冻结，两模式一致）:
  ON=绿 (80,220,120)  OFF=灰 (140,140,140)  DROWSY_WARN=黄 (60,200,255)
  DROWSY_ALARM=红 (60,60,240)  worn=绿  not_worn=红  unknown=黄  DISABLED=灰
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

from app.dms.fatigue import (STATE_ALARM, STATE_DISABLED, STATE_NORMAL,
                             STATE_UNKNOWN, STATE_WARN)

Rect = Tuple[int, int, int, int]

NOTICE_CN = ("演示级实现：未做 PERCLOS 标定；无近红外相机时夜间/强逆光不可用；"
             "结果不构成安全认证。")
NOTICE_ASCII = ("DEMO ONLY: no PERCLOS calibration; no IR camera -> "
                "unusable at night; not a certified safety device.")

STATE_TEXT_ASCII = {
    STATE_DISABLED: "DISABLED",
    STATE_UNKNOWN: "UNKNOWN",
    STATE_NORMAL: "NORMAL",
    STATE_WARN: "DROWSY WARN",
    STATE_ALARM: "DROWSY ALARM",
}
STATE_TEXT_CN = {
    STATE_DISABLED: "已关闭",
    STATE_UNKNOWN: "未知",
    STATE_NORMAL: "正常",
    STATE_WARN: "疲劳预警",
    STATE_ALARM: "疲劳报警",
}
VERDICT_TEXT_ASCII = {"worn": "WORN", "not_worn": "NOT WORN", "unknown": "UNKNOWN"}
VERDICT_TEXT_CN = {"worn": "已佩戴", "not_worn": "未佩戴", "unknown": "无法判定"}

# 头盔结论聚合（§4.2，纯展示聚合，不是检测逻辑）
HELMET_SUMMARY_ASCII = {
    "disabled": "DISABLED",
    "no_person": "NO PERSON",
    "not_worn": "NOT WORN",
    "unknown": "UNKNOWN",
    "worn": "WORN",
}
HELMET_SUMMARY_CN = {
    "disabled": "已关闭",
    "no_person": "无人体框",
    "not_worn": "未佩戴",
    "unknown": "无法判定",
    "worn": "已佩戴",
}

FACE_LOST_ASCII = "FACE LOST"

COLOR_ON = (80, 220, 120)
COLOR_OFF = (140, 140, 140)
COLOR_WARN = (60, 200, 255)
COLOR_ALARM = (60, 60, 240)
COLOR_UNKNOWN = (60, 200, 255)
COLOR_DISABLED = (140, 140, 140)
COLOR_TEXT = (235, 235, 235)
COLOR_DIM = (170, 170, 170)

STATE_COLOR = {
    STATE_DISABLED: COLOR_DISABLED,
    STATE_UNKNOWN: COLOR_UNKNOWN,
    STATE_NORMAL: COLOR_ON,
    STATE_WARN: COLOR_WARN,
    STATE_ALARM: COLOR_ALARM,
}
VERDICT_COLOR = {"worn": COLOR_ON, "not_worn": COLOR_ALARM,
                 "unknown": COLOR_UNKNOWN}
HELMET_SUMMARY_COLOR = {
    "disabled": COLOR_DISABLED,
    "no_person": COLOR_DISABLED,
    "not_worn": COLOR_ALARM,
    "unknown": COLOR_UNKNOWN,
    "worn": COLOR_ON,
}

FONT = cv2.FONT_HERSHEY_SIMPLEX
WINDOW_NAME = "DMS helmet demo"

# -- 冻结常量（规格 §6.2 / §8.1）-------------------------------------------
PLATE_ALPHA = 0.65            # 底板不透明度（下限 0.60）
ROW_H_MIN = 28                # 可点目标高度下限（C8）
MAX_DEBUG_PERSONS = 3         # 调试层逐人行上限
MIN_DEBUG_LINES = 2           # 低于此值不绘制调试块
SOURCE_DEBUG_MAX_CHARS = 40   # 调试行里 source 的字符数上限（超出以 .. 结尾）
LINE_H_FACTOR = 1.6
THICKNESS_MIN_SCALE = 0.6     # THICKNESS = 2 if scale >= 0.6 else 1（§6.2）
HOTKEY_DEBUG = ord("d")       # 热键 d（写在代码里，不进 configs/dms.yaml）

# 分档常量（数值即验收值，§6.2）
_HUD_TIERS = ((1920, 1.0), (1280, 0.8), (960, 0.7), (640, 0.6), (480, 0.5))
_MINOR_TIERS = ((960, 0.6), (480, 0.5))
_MINOR_SCALE = 0.45
_HUD_MIN_SCALE = 0.45


def state_text_ascii(state: Optional[str]) -> str:
    return STATE_TEXT_ASCII.get(str(state), "UNKNOWN")


def verdict_text_ascii(verdict: Optional[str]) -> str:
    return VERDICT_TEXT_ASCII.get(str(verdict), "UNKNOWN")


def font_scale_for(width: int, *, role: str) -> float:
    """按画布宽度分档取字号（§6.2）。role ∈ {hud,panel,debug,notice}。"""
    w = int(width)
    tiers = _HUD_TIERS if role in ("hud", "panel") else _MINOR_TIERS
    for floor, scale in tiers:
        if w >= floor:
            return float(scale)
    return float(_HUD_MIN_SCALE if role in ("hud", "panel") else _MINOR_SCALE)


def glyph_h(scale: float) -> int:
    """实测字形高度（cv2.getTextSize）。"""
    return int(cv2.getTextSize("X", FONT, float(scale), 1)[0][1])


def line_h_for(scale: float) -> int:
    """行高 = round(glyph_h * LINE_H_FACTOR)（§6.2）。"""
    return int(round(glyph_h(scale) * LINE_H_FACTOR))


def thickness_for(scale: float) -> int:
    """笔画粗细（§6.2 冻结）：scale >= 0.6 用 2，否则 1（保证 C3 文字亮度）。"""
    return 2 if float(scale) >= THICKNESS_MIN_SCALE else 1


def helmet_summary(snapshot: Any) -> str:
    """头盔结论聚合（§4.2）：not_worn > unknown > worn，保守优先。"""
    helmet = (snapshot or {}).get("helmet", {}) or {}
    if not helmet.get("enabled", False):
        return "disabled"
    verdicts = {str(p.get("verdict", "unknown"))
                for p in (helmet.get("persons", []) or [])}
    if not verdicts:
        return "no_person"
    if "not_worn" in verdicts:
        return "not_worn"
    if "unknown" in verdicts:
        return "unknown"
    return "worn"


def helmet_summary_ascii(snapshot: Any) -> str:
    return HELMET_SUMMARY_ASCII.get(helmet_summary(snapshot), "UNKNOWN")


def helmet_summary_cn(snapshot: Any) -> str:
    return HELMET_SUMMARY_CN.get(helmet_summary(snapshot), "未知")


@dataclass(frozen=True)
class ControlRect:
    name: str
    x: int
    y: int
    w: int
    h: int


@dataclass(frozen=True)
class CanvasLayout:
    """一次 draw_dms_frame 的几何结果（测试据此做 C4/C5 断言）。"""

    surface: str
    hud: Optional[Rect] = None
    panel: Optional[Rect] = None
    debug: Optional[Rect] = None
    notice: Optional[Rect] = None
    controls: Tuple[ControlRect, ...] = ()


class FrameResult:
    """主循环每帧的结果（疲劳/头盔各自可能为 None = 该路关闭）。"""

    __slots__ = ("fidx", "ts", "fatigue", "helmet")

    def __init__(self, fidx: int, ts: float, fatigue: Any = None,
                 helmet: Any = None) -> None:
        self.fidx = fidx
        self.ts = ts
        self.fatigue = fatigue
        self.helmet = helmet


# -- 文本出口 ---------------------------------------------------------------

def _put_text(img: np.ndarray, text: str, org: Tuple[int, int], scale: float,
              color: Tuple[int, int, int], thickness: int = 1,
              aa: bool = True) -> None:
    """唯一文本出口（供测试捕获）。

    防御性 ASCII（§6.7）：未来若有人误传中文，画面只出 '?'，不留乱码方块。

    `aa=False` 用 LINE_8（硬像素）绘制：**几何层**（框上短标签）在"web + 有窗口"
    时会被绘制两次（§8.3），只有硬像素能做到"同一坐标同一像素"的幂等；HUD/调试/
    notice 只画一次，保持抗锯齿以利可读性。
    """
    text = str(text)
    if not text.isascii():
        text = "".join(ch if ch.isascii() else "?" for ch in text)
    cv2.putText(img, text, org, FONT, float(scale), color, int(thickness),
                cv2.LINE_AA if aa else cv2.LINE_8)


def _text_w(text: str, scale: float) -> int:
    return int(cv2.getTextSize(str(text), FONT, float(scale), 1)[0][0])


def _fit_text(text: str, scale: float, max_width: int) -> str:
    """超宽时从尾部删字符并补 '..'，保证宽度不越界（§6.2）。"""
    text = str(text)
    if max_width <= 0:
        return ""
    if _text_w(text, scale) <= max_width:
        return text
    while text and _text_w(text + "..", scale) > max_width:
        text = text[:-1]
    return text + ".."


def _wrap_text(text: str, scale: float, max_width: int) -> List[str]:
    """按词折行（notice 条用）。单词本身超宽时交给 _fit_text 截断。"""
    lines: List[str] = []
    current = ""
    for word in str(text).split(" "):
        candidate = (current + " " + word).strip()
        if _text_w(candidate, scale) <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return [_fit_text(line, scale, max_width) for line in lines] or [""]


# -- 底板 -------------------------------------------------------------------

def _clip_rect(rect: Rect, shape: Sequence[int]) -> Rect:
    height, width = int(shape[0]), int(shape[1])
    x, y, w, h = [int(v) for v in rect]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(width, x + w), min(height, y + h)
    return (x0, y0, max(0, x1 - x0), max(0, y1 - y0))


def _clamp_rect(rect: Rect, width: int, height: int) -> Rect:
    """保证矩形完全落在画布内（C5）：左块 x>0 时要扣掉自身偏移。"""
    x, y, w, h = [int(v) for v in rect]
    x = max(0, min(x, width))
    y = max(0, min(y, height))
    w = max(0, min(w, width - x))
    h = max(0, min(h, height - y))
    return (x, y, w, h)


def _fill_plate(canvas: np.ndarray, rect: Rect) -> None:
    """半透明深色底板（C2：白底上底板中位亮度 <= 110）。"""
    x, y, w, h = _clip_rect(rect, canvas.shape[:2])
    if w <= 0 or h <= 0:
        return
    sub = canvas[y:y + h, x:x + w]
    dark = cv2.addWeighted(sub, 1.0 - PLATE_ALPHA,
                           np.zeros_like(sub), PLATE_ALPHA, 0.0)
    canvas[y:y + h, x:x + w] = dark


# -- 布局参数 ---------------------------------------------------------------

def _pads(width: int) -> Tuple[int, int, int, int]:
    full = int(width) >= 480
    return (8 if full else 4, 8 if full else 6,
            8 if full else 6, 6 if full else 4)


def _is_compact(width: int, height: int) -> bool:
    return (int(width) < 480) or (int(height) < 360)


def _panel_rows(width: int, hud_scale: float) -> Tuple[int, int]:
    """(BOX, ROW_H)：可点目标高度 >= ROW_H_MIN（C8）。"""
    box = max(glyph_h(hud_scale) + 2, 18)
    row_h = max(box + 8, ROW_H_MIN)
    return box, row_h


def _snapshot_of(runtime: Any) -> Dict[str, Any]:
    try:
        return runtime.snapshot() if runtime is not None else {}
    except Exception:  # noqa: BLE001 - never break the loop over a snapshot
        return {}


def _meta_get(meta: Optional[dict], key: str, default: Any) -> Any:
    if not isinstance(meta, dict):
        return default
    value = meta.get(key, default)
    return default if value is None else value


def _hud_segments(snapshot: Dict[str, Any], meta: Optional[dict],
                  compact: bool) -> List[List[Tuple[str, Tuple[int, int, int]]]]:
    """HUD 每行的 (text, color) 段列表（默认界面：单槽位，§4.1）。"""
    fatigue = snapshot.get("fatigue", {}) or {}
    fatigue_enabled = bool(fatigue.get("enabled", False))
    face_present = bool(fatigue.get("face_present", False))
    state = str(fatigue.get("state", STATE_UNKNOWN))

    cam_ok = bool(_meta_get(meta, "cam_ok", True))
    cam_text = "CAM:OK" if cam_ok else "CAM:LOST"
    cam_color = COLOR_ON if cam_ok else COLOR_ALARM
    if not fatigue_enabled:
        face_text, face_color = "FACE:N/A", COLOR_DISABLED
    elif face_present:
        face_text, face_color = "FACE:OK", COLOR_ON
    else:
        face_text, face_color = "FACE:LOST", COLOR_ALARM

    state_color = STATE_COLOR.get(state, COLOR_UNKNOWN)
    summary = helmet_summary(snapshot)
    helm_color = HELMET_SUMMARY_COLOR.get(summary, COLOR_UNKNOWN)
    if compact:
        return [
            [(cam_text, cam_color), (face_text, face_color)],
            [("FAT: " + state_text_ascii(state), state_color),
             ("HELM: " + HELMET_SUMMARY_ASCII[summary], helm_color)],
        ]
    return [
        [(cam_text, cam_color), (face_text, face_color)],
        [("FATIGUE: " + state_text_ascii(state), state_color)],
        [("HELMET: " + HELMET_SUMMARY_ASCII[summary], helm_color)],
    ]


def _joined(segments: Sequence[Tuple[str, Tuple[int, int, int]]]) -> str:
    return "  ".join(text for text, _color in segments)


def _debug_lines(snapshot: Dict[str, Any], meta: Optional[dict]) -> List[str]:
    """调试层固定行 D1..D6 + 逐人行 D7 + 溢出提示 D8（§5.2）。"""
    fatigue = snapshot.get("fatigue", {}) or {}
    helmet = snapshot.get("helmet", {}) or {}
    counts = helmet.get("verdict_counts", {}) or {}
    persons = helmet.get("persons", []) or []

    source = str(_meta_get(meta, "source", ""))
    if len(source) > SOURCE_DEBUG_MAX_CHARS:
        source = source[:max(0, SOURCE_DEBUG_MAX_CHARS - 2)] + ".."
    fps = _meta_get(meta, "fps", None)
    cam_ok = bool(_meta_get(meta, "cam_ok", True))
    mode = str(_meta_get(meta, "mode", "display"))
    top_fidx = int(_meta_get(meta, "fidx", 0))

    def num(value: Any, nd: int = 2) -> str:
        if value is None:
            return "-"
        try:
            return "%.*f" % (nd, float(value))
        except (TypeError, ValueError):
            return "-"

    lines = [
        "DBG ON  mode=%s  src=%s" % (mode, source or "-"),
        "DBG fps=%s  fidx=%d  cam=%s"
        % (num(fps, 1) if fps is not None else "-", top_fidx,
           "ok" if cam_ok else "lost"),
        "DBG fat score=%s  eye=%s  mouth=%s"
        % (num(fatigue.get("score"), 2), num(fatigue.get("eye_dark_ratio"), 2),
           num(fatigue.get("mouth_open_ratio"), 2)),
        "DBG fat infer=%d  fidx=%d  %sms"
        % (int(fatigue.get("infer_count", 0) or 0),
           int(fatigue.get("last_infer_fidx", -1) or -1),
           num(fatigue.get("last_infer_ms"), 1)),
        "DBG helm n=%d  wn/u=%d/%d/%d"
        % (len(persons), int(counts.get("worn", 0) or 0),
           int(counts.get("not_worn", 0) or 0),
           int(counts.get("unknown", 0) or 0)),
        "DBG helm infer=%d  fidx=%d  %sms"
        % (int(helmet.get("infer_count", 0) or 0),
           int(helmet.get("last_infer_fidx", -1) or -1),
           num(helmet.get("last_infer_ms"), 1)),
    ]
    for person in persons[:MAX_DEBUG_PERSONS]:
        lines.append(
            "DBG p id%d %s/%s/%s %s/%s/%s a=%d"
            % (int(person.get("track_id", -1)),
               VERDICT_TEXT_ASCII.get(str(person.get("verdict")), "UNKNOWN"),
               str(person.get("reason") or "-"),
               str(person.get("conf") or "-"),
               num(person.get("helmet_color_ratio"), 2),
               num(person.get("skin_ratio"), 2),
               num(person.get("dark_ratio"), 2),
               int(person.get("age_frames", 0) or 0)))
    extra = len(persons) - MAX_DEBUG_PERSONS
    if extra > 0:
        lines.append("DBG p +%d more" % extra)
    return lines


# -- 主绘制入口 -------------------------------------------------------------

def draw_dms_frame(canvas: np.ndarray, frame_result: Optional[FrameResult],
                   runtime: Any, *, surface: str = "display",
                   debug: bool = False, meta: Optional[dict] = None,
                   panel_xy: Optional[Tuple[int, int]] = None,
                   font_scale: Optional[float] = None) -> CanvasLayout:
    """唯一绘制入口（§8.1）。原地绘制并返回本次几何。

    `panel_xy` / `font_scale` 形参保留但忽略（旧签名兼容）；几何一律来自规格
    §6.1/§6.2，因此同一坐标同一像素 —— 重复调用是幂等的。
    """
    surface = "stream" if str(surface) == "stream" else "display"
    if canvas is None or getattr(canvas, "size", 0) == 0:
        return CanvasLayout(surface=surface)
    height, width = int(canvas.shape[0]), int(canvas.shape[1])
    if width < 64 or height < 64:
        return CanvasLayout(surface=surface)

    snapshot = _snapshot_of(runtime)
    _draw_geometry(canvas, frame_result, snapshot, surface)
    if surface == "stream":
        # 推流画面：只有检测框 + 短标签（零 HUD/调试/notice/控件像素，C6）
        return CanvasLayout(surface="stream")
    return _draw_display_layer(canvas, snapshot, meta, bool(debug))


def _draw_display_layer(canvas: np.ndarray, snapshot: Dict[str, Any],
                        meta: Optional[dict], debug: bool) -> CanvasLayout:
    height, width = int(canvas.shape[0]), int(canvas.shape[1])
    pad, gap, ppx, ppy = _pads(width)
    compact = _is_compact(width, height)
    hud_scale = font_scale_for(width, role="hud")
    dbg_scale = font_scale_for(width, role="debug")
    hud_lh = line_h_for(hud_scale)
    dbg_lh = line_h_for(dbg_scale)
    box, row_h = _panel_rows(width, hud_scale)

    notice_text = str(snapshot.get("_notice", NOTICE_ASCII))
    # 左对齐块从 x=pad 起画，文字可用宽度必须扣掉 pad 与两侧 PLATE_PAD_X（C5）
    fit_w = max(64, width - pad - 2 * ppx)

    def notice_layout(single: bool) -> Tuple[List[str], int]:
        if single:
            lines = [_fit_text(notice_text, dbg_scale, fit_w)]
        else:
            lines = _wrap_text(notice_text, dbg_scale, max(64, width - 16))
        return lines, len(lines) * dbg_lh + 2 * ppy

    def hud_layout(single: bool) -> Tuple[List[List[Tuple[str, Tuple[int, int, int]]]],
                                         int]:
        rows = _hud_segments(snapshot, meta, compact)
        if single:
            rows = [_single_hud_row(snapshot, meta, compact)]
        return rows, len(rows) * hud_lh + 2 * ppy

    # 降级链（§6.1）：正常 -> notice 单行 -> HUD 单行 -> 只剩 notice 单行
    for single_notice, single_hud in ((False, False), (True, False),
                                      (True, True)):
        lines, notice_h = notice_layout(single_notice)
        hud_rows, hud_h = hud_layout(single_hud)
        notice_top = height - notice_h
        panel_y = pad + hud_h + gap
        if panel_y + 2 * row_h + gap <= notice_top:
            return _paint_display(canvas, snapshot, meta, debug, pad, gap, ppx,
                                  ppy, hud_scale, dbg_scale, hud_lh, dbg_lh,
                                  box, row_h, hud_rows, lines, notice_h,
                                  notice_top, panel_y)

    # 极端矮画布：只画 notice 单行（诚实标注在任何尺寸下都不放弃）
    lines, notice_h = notice_layout(True)
    notice_top = height - notice_h
    _draw_notice(canvas, lines, dbg_scale, dbg_lh, ppx, ppy, width, notice_top,
                 notice_h)
    if debug:
        print("[dms] overlay degraded: canvas %dx%d too small for controls"
              % (width, height), flush=True)
    return CanvasLayout(surface="display",
                        notice=(0, notice_top, width, notice_h))


def _single_hud_row(snapshot: Dict[str, Any], meta: Optional[dict],
                    compact: bool) -> List[Tuple[str, Tuple[int, int, int]]]:
    """HUD 强制单行（降级链第 3 步）。"""
    rows = _hud_segments(snapshot, meta, compact)
    if len(rows) == 1:
        return rows[0]
    flat = [seg for row in rows for seg in row]
    out: List[Tuple[str, Tuple[int, int, int]]] = []
    for text, color in flat:
        if text.startswith("FAT: "):
            text = "FAT:" + text[4:]
        elif text.startswith("FATIGUE: "):
            text = "FAT:" + text[9:]
        elif text.startswith("HELM: "):
            text = "HELM:" + text[6:]
        elif text.startswith("HELMET: "):
            text = "HELM:" + text[8:]
        out.append((text, color))
    return out


def _paint_display(canvas: np.ndarray, snapshot: Dict[str, Any],
                   meta: Optional[dict], debug: bool, pad: int, gap: int,
                   ppx: int, ppy: int, hud_scale: float, dbg_scale: float,
                   hud_lh: int, dbg_lh: int, box: int, row_h: int,
                   hud_rows: List[List[Tuple[str, Tuple[int, int, int]]]],
                   notice_lines: List[str], notice_h: int, notice_top: int,
                   panel_y: int) -> CanvasLayout:
    height, width = int(canvas.shape[0]), int(canvas.shape[1])

    hud_w = max([_text_w(_joined(row), hud_scale) for row in hud_rows] or [0])
    hud_rect = _clamp_rect((pad, pad, hud_w + 2 * ppx,
                            len(hud_rows) * hud_lh + 2 * ppy), width, height)
    _fill_plate(canvas, hud_rect)
    glyph = glyph_h(hud_scale)
    for index, row in enumerate(hud_rows):
        line_top = hud_rect[1] + ppy + index * hud_lh
        baseline = line_top + glyph + max(0, (hud_lh - glyph) // 2)
        _draw_segments(canvas, row, hud_rect[0] + ppx, baseline, hud_scale)

    controls = _panel_controls(snapshot, width, hud_scale, panel_y, box, row_h)
    _paint_panel(canvas, snapshot, controls, panel_y, box, row_h, pad, hud_scale)
    row_w = max([rect.w for rect in controls] or [0])
    panel_rect = _clamp_rect((pad - 4, panel_y - 4, row_w + 8, 2 * row_h + 8),
                             width, height)

    debug_rect: Optional[Rect] = None
    if debug:
        dbg_y = panel_y + 2 * row_h + gap
        available = notice_top - gap - dbg_y - 2 * ppy
        nfit = int(available // dbg_lh) if dbg_lh > 0 else 0
        if nfit >= MIN_DEBUG_LINES:
            dbg_fit_w = max(64, width - pad - 2 * ppx)
            kept = [_fit_text(line, dbg_scale, dbg_fit_w)
                    for line in _debug_lines(snapshot, meta)[:nfit]]
            dbg_w = max([_text_w(line, dbg_scale) for line in kept] or [0])
            debug_rect = _clamp_rect((pad, dbg_y, dbg_w + 2 * ppx,
                                      len(kept) * dbg_lh + 2 * ppy),
                                     width, height)
            _fill_plate(canvas, debug_rect)
            dbg_glyph = glyph_h(dbg_scale)
            for index, line in enumerate(kept):
                line_top = debug_rect[1] + ppy + index * dbg_lh
                baseline = line_top + dbg_glyph + max(
                    0, (dbg_lh - dbg_glyph) // 2)
                _put_text(canvas, line, (debug_rect[0] + ppx, baseline),
                          dbg_scale, COLOR_DIM, thickness_for(dbg_scale))
        else:
            print("[dms] debug layer suppressed: canvas %dx%d too short"
                  % (width, height), flush=True)

    _draw_notice(canvas, notice_lines, dbg_scale, dbg_lh, ppx, ppy, width,
                 notice_top, notice_h)
    return CanvasLayout(surface="display", hud=hud_rect, panel=panel_rect,
                        debug=debug_rect,
                        notice=(0, notice_top, width, notice_h),
                        controls=tuple(controls))


def _draw_segments(canvas: np.ndarray,
                   segments: Sequence[Tuple[str, Tuple[int, int, int]]],
                   x: int, baseline: int, scale: float) -> None:
    """一行内的多段文本（各自颜色）；段间固定两个空格（§4.1）。"""
    cursor = int(x)
    gap_w = _text_w("  ", scale)
    for index, (text, color) in enumerate(segments):
        if index:
            cursor += gap_w
        _put_text(canvas, text, (cursor, int(baseline)), scale, color,
                  thickness_for(scale))
        cursor += _text_w(text, scale)


def _draw_notice(canvas: np.ndarray, lines: Sequence[str], scale: float,
                 line_h: int, ppx: int, ppy: int, width: int, notice_top: int,
                 notice_h: int) -> None:
    _fill_plate(canvas, (0, notice_top, width, notice_h))
    glyph = glyph_h(scale)
    for index, line in enumerate(lines):
        line_top = notice_top + ppy + index * line_h
        baseline = line_top + glyph + max(0, (line_h - glyph) // 2)
        _put_text(canvas, line, (ppx, baseline), scale, COLOR_TEXT,
                  thickness_for(scale))


def _panel_controls(snapshot: Dict[str, Any], width: int, hud_scale: float,
                    panel_y: int, box: int, row_h: int) -> List[ControlRect]:
    """控件行几何（与绘制同源，§6.2）：命中高 = ROW_H，相邻两行零重叠。"""
    fatigue_enabled = bool((snapshot.get("fatigue", {}) or {})
                           .get("enabled", False))
    helmet_enabled = bool((snapshot.get("helmet", {}) or {})
                          .get("enabled", False))
    pad = _pads(width)[0]
    rows = (
        ("fatigue_enabled", "[1] FATIGUE", fatigue_enabled),
        ("helmet_enabled", "[2] HELMET", helmet_enabled),
    )
    rects: List[ControlRect] = []
    for index, (name, label, enabled) in enumerate(rows):
        text = "%s: %s" % (label, "ON" if enabled else "OFF")
        rects.append(ControlRect(name=name, x=pad - 4, y=panel_y + index * row_h,
                                 w=box + 18 + _text_w(text, hud_scale),
                                 h=row_h))
    return rects


def _paint_panel(canvas: np.ndarray, snapshot: Dict[str, Any],
                 controls: Sequence[ControlRect], panel_y: int, box: int,
                 row_h: int, pad: int, hud_scale: float) -> None:
    """绘制控件块（含底板）与两行复选框；几何来自 _panel_controls。"""
    if controls:
        block_w = max(rect.w for rect in controls) + 8
        _fill_plate(canvas, (pad - 4, panel_y - 4, block_w, 2 * row_h + 8))
    fatigue_enabled = bool((snapshot.get("fatigue", {}) or {})
                           .get("enabled", False))
    helmet_enabled = bool((snapshot.get("helmet", {}) or {})
                          .get("enabled", False))
    labels = ("[1] FATIGUE", "[2] HELMET")
    states = (fatigue_enabled, helmet_enabled)
    for index in range(2):
        enabled = states[index]
        color = COLOR_ON if enabled else COLOR_OFF
        box_y = panel_y + index * row_h + (row_h - box) // 2
        cv2.rectangle(canvas, (pad, box_y), (pad + box, box_y + box), color, 2)
        if enabled:
            cv2.rectangle(canvas, (pad + 4, box_y + 4),
                          (pad + box - 4, box_y + box - 4), color, -1)
        text = "%s: %s" % (labels[index], "ON" if enabled else "OFF")
        _put_text(canvas, text, (pad + box + 10, box_y + box - 1), hud_scale,
                  color, thickness_for(hud_scale))


def draw_dms_panel(canvas: np.ndarray, runtime: Any, *,
                   panel_xy: Optional[Tuple[int, int]] = None,
                   font_scale: Optional[float] = None) -> List[ControlRect]:
    """绘制画面内两行复选框；返回命中矩形（与绘制**同一次调用**同源）。

    `panel_xy` / `font_scale` 保留但忽略（旧签名兼容，§8.1）。
    """
    if canvas is None or getattr(canvas, "size", 0) == 0:
        return []
    height, width = int(canvas.shape[0]), int(canvas.shape[1])
    if width < 64 or height < 64:
        return []
    snapshot = _snapshot_of(runtime)
    pad, gap, _ppx, ppy = _pads(width)
    hud_scale = font_scale_for(width, role="hud")
    box, row_h = _panel_rows(width, hud_scale)
    hud_rows = _hud_segments(snapshot, meta=None,
                             compact=_is_compact(width, height))
    panel_y = pad + len(hud_rows) * line_h_for(hud_scale) + 2 * ppy + gap
    controls = _panel_controls(snapshot, width, hud_scale, panel_y, box, row_h)
    _paint_panel(canvas, snapshot, controls, panel_y, box, row_h, pad, hud_scale)
    return controls


def _draw_geometry(canvas: np.ndarray, frame_result: Optional[FrameResult],
                   snapshot: Dict[str, Any], surface: str) -> None:
    """Render compact driver state; reserve full boxes for actual risk."""
    label_scale = max(0.65, font_scale_for(int(canvas.shape[1]), role="hud"))
    fatigue_state = str(getattr(getattr(frame_result, "fatigue", None), "state", "")
                        or (snapshot.get("fatigue", {}) or {}).get("state", STATE_UNKNOWN))

    if frame_result is not None and frame_result.helmet:
        for verdict in frame_result.helmet:
            color = VERDICT_COLOR.get(verdict.verdict, COLOR_UNKNOWN)
            _draw_box(canvas, verdict.bbox, color, 3)
            _draw_box(canvas, verdict.head_roi, color, 2)
            label = "PERSON %d | HELMET: %s" % (
                verdict.track_id, verdict_text_ascii(verdict.verdict))
            _draw_tag(canvas, label, verdict.bbox, color, label_scale,
                      inside=False)

    signals = getattr(frame_result.fatigue, "signals", None) if (
        frame_result is not None and frame_result.fatigue is not None) else None
    if signals is None:
        return
    fatigue = snapshot.get("fatigue", {}) or {}
    label, color, risk = _driver_status(fatigue_state, fatigue, signals)
    if label and not (risk and signals is not None and signals.face_box):
        _draw_driver_badge(canvas, label, color, surface, label_scale)
    if signals is not None and signals.face_box:
        if risk:
            _draw_corner_box(canvas, signals.face_box, color, 3)
            _draw_low_battery_icon(canvas, signals.face_box, COLOR_ALARM)
        else:
            _draw_corner_box(canvas, signals.face_box, color, 2)


def _driver_status(state: str, fatigue: Dict[str, Any], signals: Any) -> Tuple[str, Tuple[int, int, int], bool]:
    """Return one user-facing driver state, prioritizing safety over detail."""
    if not fatigue.get("enabled", False):
        return "", COLOR_DISABLED, False
    face_present = bool(getattr(signals, "face_present", fatigue.get("face_present", False)))
    if not face_present:
        return "FACE NOT VISIBLE", COLOR_UNKNOWN, False
    if state == STATE_ALARM:
        return "FATIGUE ALERT", COLOR_ALARM, True
    if state == STATE_WARN:
        return "FATIGUE WARNING", COLOR_WARN, True
    eyes_closed = bool(getattr(signals, "eyes_closed", fatigue.get("eyes_closed", False)))
    if eyes_closed:
        return "EYES CLOSED", COLOR_WARN, False
    mouth_open = bool(getattr(signals, "mouth_open", fatigue.get("mouth_open", False)))
    if mouth_open:
        return "MOUTH OPEN", COLOR_WARN, False
    return "DRIVER ATTENTIVE", COLOR_ON, False


def _draw_driver_badge(canvas: np.ndarray, text: str,
                       accent: Tuple[int, int, int], surface: str,
                       scale: float) -> None:
    """Small, solid status badge that stays legible without obscuring the face."""
    height, width = canvas.shape[:2]
    padding_x, padding_y, stripe_w = 10, 7, 5
    thickness = max(1, thickness_for(scale))
    text = _fit_text(text, scale, max(1, width - 2 * padding_x - stripe_w - 20))
    if not text:
        return
    (text_w, text_h), baseline = cv2.getTextSize(text, FONT, scale, thickness)
    badge_w = min(width - 2, text_w + 2 * padding_x + stripe_w)
    badge_h = text_h + baseline + 2 * padding_y
    pad = max(12, int(round(width * 0.012)))
    # The web stream has no HUD, so the lower-left corner is quiet. The local
    # desktop GUI reserves that corner for the demo notice, so use upper-right.
    if surface == "stream":
        x = min(pad, max(0, width - badge_w))
        y = max(0, height - pad - badge_h)
    else:
        x = max(0, width - pad - badge_w)
        y = min(pad, max(0, height - badge_h))
    cv2.rectangle(canvas, (x, y), (x + badge_w - 1, y + badge_h - 1),
                  (12, 18, 30), -1)
    cv2.rectangle(canvas, (x, y), (x + stripe_w - 1, y + badge_h - 1),
                  accent, -1)
    _put_text(canvas, text, (x + stripe_w + padding_x, y + padding_y + text_h),
              scale, COLOR_TEXT, thickness, aa=False)


def _draw_corner_box(canvas: np.ndarray, box: Sequence[int],
                     color: Tuple[int, int, int], thickness: int) -> None:
    """Use four unobtrusive corner marks instead of a full normal-state box."""
    try:
        x, y, w, h = [int(v) for v in box]
    except (TypeError, ValueError):
        return
    if w <= 0 or h <= 0:
        return
    height, width = canvas.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(width - 1, x + w), min(height - 1, y + h)
    if x1 <= x0 or y1 <= y0:
        return
    length = max(8, min(28, min(x1 - x0, y1 - y0) // 4))
    for p0, p1 in (
        ((x0, y0), (x0 + length, y0)), ((x0, y0), (x0, y0 + length)),
        ((x1, y0), (x1 - length, y0)), ((x1, y0), (x1, y0 + length)),
        ((x0, y1), (x0 + length, y1)), ((x0, y1), (x0, y1 - length)),
        ((x1, y1), (x1 - length, y1)), ((x1, y1), (x1, y1 - length)),
    ):
        cv2.line(canvas, p0, p1, color, int(thickness), lineType=cv2.LINE_8)


def _rounded_rect(canvas: np.ndarray, x: int, y: int, width: int, height: int,
                  radius: int, color: Tuple[int, int, int], thickness: int) -> None:
    """OpenCV-compatible rounded rectangle for the alert icon."""
    radius = max(1, min(int(radius), width // 2, height // 2))
    x1, y1 = x + width - 1, y + height - 1
    cv2.rectangle(canvas, (x + radius, y), (x1 - radius, y1), color, thickness)
    cv2.rectangle(canvas, (x, y + radius), (x1, y1 - radius), color, thickness)
    for center in ((x + radius, y + radius), (x1 - radius, y + radius),
                   (x + radius, y1 - radius), (x1 - radius, y1 - radius)):
        cv2.circle(canvas, center, radius, color, thickness, lineType=cv2.LINE_8)


def _draw_low_battery_icon(canvas: np.ndarray, box: Sequence[int],
                           color: Tuple[int, int, int]) -> None:
    """Draw the supplied red low-battery mark centered above a fatigue alert."""
    try:
        x, y, width, height = [int(v) for v in box]
    except (TypeError, ValueError):
        return
    if width <= 0 or height <= 0:
        return
    image_h, image_w = canvas.shape[:2]
    body_w = max(54, min(160, int(round(width * 0.72))))
    body_h = max(26, int(round(body_w * 0.42)))
    terminal_w = max(7, int(round(body_h * 0.18)))
    full_w = body_w + terminal_w
    icon_x = max(0, min(x + (width - full_w) // 2, image_w - full_w))
    icon_y = max(3, y - body_h - max(8, body_h // 4))
    radius = max(5, body_h // 4)

    # A solid red outer casing, black cavity, red low-charge cell, and left tab
    # reproduce the supplied marker while staying readable at stream resolution.
    _rounded_rect(canvas, icon_x + terminal_w, icon_y, body_w, body_h, radius,
                  color, -1)
    tab_h = max(8, body_h // 2)
    tab_y = icon_y + (body_h - tab_h) // 2
    _rounded_rect(canvas, icon_x, tab_y, terminal_w + 4, tab_h,
                  max(3, tab_h // 3), color, -1)
    inset = max(5, body_h // 7)
    _rounded_rect(canvas, icon_x + terminal_w + inset, icon_y + inset,
                  body_w - 2 * inset, body_h - 2 * inset,
                  max(3, radius - inset), (8, 8, 8), -1)
    charge_w = max(10, int(round((body_w - 2 * inset) * 0.14)))
    charge_x = icon_x + terminal_w + body_w - inset - charge_w
    _rounded_rect(canvas, charge_x, icon_y + inset + 2, charge_w,
                  max(4, body_h - 2 * inset - 4), max(2, charge_w // 3),
                  color, -1)


def _draw_tag(canvas: np.ndarray, text: str, box: Sequence[int],
              accent: Tuple[int, int, int], scale: float, *, inside: bool) -> None:
    """Draw one readable, deterministic tag without changing detection data.

    The solid dark plate keeps the stream geometry idempotent: the DMS web
    server and local GUI may draw the same frame twice before it is encoded.
    """
    try:
        x, y, _w, h = [int(value) for value in box]
    except (TypeError, ValueError):
        return
    height, width = canvas.shape[:2]
    padding_x, padding_y, stripe_w = 8, 6, 5
    thickness = max(2, thickness_for(scale))
    max_text_width = max(1, width - 2 * padding_x - stripe_w - 4)
    text = _fit_text(text, scale, max_text_width)
    if not text:
        return
    (text_w, text_h), baseline = cv2.getTextSize(text, FONT, scale, thickness)
    tag_w = min(width, text_w + 2 * padding_x + stripe_w)
    tag_h = text_h + baseline + 2 * padding_y
    tag_x = max(0, min(x, width - tag_w))
    preferred_y = y + 3 if inside else y - tag_h - 4
    if preferred_y < 0:
        preferred_y = min(max(0, y + h + 4), max(0, height - tag_h))
    tag_y = max(0, min(preferred_y, max(0, height - tag_h)))
    cv2.rectangle(canvas, (tag_x, tag_y),
                  (tag_x + tag_w - 1, tag_y + tag_h - 1), (12, 18, 30), -1)
    cv2.rectangle(canvas, (tag_x, tag_y),
                  (tag_x + stripe_w - 1, tag_y + tag_h - 1), accent, -1)
    _put_text(canvas, text,
              (tag_x + stripe_w + padding_x, tag_y + padding_y + text_h),
              scale, COLOR_TEXT, thickness, aa=False)


def _draw_box(canvas: np.ndarray, box: Sequence[int],
              color: Tuple[int, int, int], thickness: int) -> None:
    try:
        x, y, w, h = [int(v) for v in box]
    except (TypeError, ValueError):
        return
    if w <= 0 or h <= 0:
        return
    h_img, w_img = canvas.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(w_img - 1, x + w), min(h_img - 1, y + h)
    if x1 <= x0 or y1 <= y0:
        return
    cv2.rectangle(canvas, (x0, y0), (x1, y1), color, int(thickness))


# -- 命中测试 ---------------------------------------------------------------

def hit_test(rects: Sequence[ControlRect], x: int, y: int) -> Optional[str]:
    for rect in rects or ():
        if rect.x <= x <= rect.x + rect.w and rect.y <= y <= rect.y + rect.h:
            return rect.name
    return None


def map_mouse_to_canvas(mx: int, my: int, image_rect: Optional[Sequence[int]],
                        canvas_shape: Sequence[int]) -> Tuple[int, int]:
    """窗口坐标 -> canvas 坐标（按实际显示区域线性换算；不可用时恒等）。"""
    if not image_rect or len(image_rect) < 4:
        return int(mx), int(my)
    rx, ry, rw, rh = [int(v) for v in image_rect[:4]]
    height, width = int(canvas_shape[0]), int(canvas_shape[1])
    if rw <= 0 or rh <= 0:
        return int(mx), int(my)
    cx = (mx - rx) * width / float(rw)
    cy = (my - ry) * height / float(rh)
    return int(round(cx)), int(round(cy))


def resolve_control_hit(rects: Sequence[ControlRect], mx: int, my: int,
                        image_rect: Optional[Sequence[int]],
                        canvas_shape: Sequence[int]) -> Optional[str]:
    """窗口坐标 -> 命中的控件名（留边/缩放安全，§8.1 / C9 / E10）。

    `image_rect` 可用时**只**用换算后的画布坐标做 hit-test；换算后落在画布外
    即不命中。**禁止**再用原始窗口坐标兜底 —— 那正是留边场景假命中的根因。
    """
    if (image_rect and len(image_rect) >= 4
            and int(image_rect[2]) > 0 and int(image_rect[3]) > 0):
        cx, cy = map_mouse_to_canvas(mx, my, image_rect, canvas_shape)
        height, width = int(canvas_shape[0]), int(canvas_shape[1])
        if not (0 <= cx < width and 0 <= cy < height):
            return None
        return hit_test(rects, cx, cy)
    return hit_test(rects, int(mx), int(my))
