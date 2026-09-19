"""Web 契约：路由、/state schema、POST 白名单与回读一致（随机端口，不占默认 8010）。

UI v2 起状态与开关全部由页面 DOM 承担，调试字段默认折叠在 `#debug`（带 `hidden`），
`--debug` 时初始展开；推流画面零像素文字（画面渲染见 test_hud_layout.py）。
"""
import json
import urllib.error
import urllib.request

import numpy as np

from app.dms.render import NOTICE_CN
from app.dms.state import DmsRuntime
from app.dms.web import DmsWebServer


class ServerFixture:
    def __init__(self):
        self.runtime = DmsRuntime(fatigue_enabled=True, helmet_enabled=True,
                                  notices=[NOTICE_CN])
        self.server = DmsWebServer("127.0.0.1", 0, runtime=self.runtime,
                                   notices=[NOTICE_CN])
        self.port = int(self.server.server_address[1])
        self.server.start()

    def url(self, path):
        return "http://127.0.0.1:%d%s" % (self.port, path)

    def get(self, path):
        with urllib.request.urlopen(self.url(path), timeout=5) as response:
            return response.status, response.read(), response.headers

    def close(self):
        self.server.stop()


def _fixture():
    return ServerFixture()


def test_page_exposes_two_frozen_checkbox_ids():
    fx = _fixture()
    try:
        status, body, headers = fx.get("/")
        text = body.decode("utf-8")
        assert status == 200
        assert "text/html" in headers.get("Content-Type", "")
        assert 'id="fatigueEnabled"' in text
        assert 'id="helmetEnabled"' in text
        assert "/api/dms_config" in text
        assert "/state" in text
        assert NOTICE_CN in text            # 诚实标注必须出现在页面上
        assert "疲劳检测" in text and "头盔检测" in text
    finally:
        fx.close()


def test_state_schema_keys_and_types():
    fx = _fixture()
    try:
        status, body, _ = fx.get("/state")
        assert status == 200
        payload = json.loads(body.decode("utf-8"))
        assert set(payload) == {"fidx", "ts", "fps", "mode", "cam_ok",
                                "source", "dms", "notices"}
        assert isinstance(payload["fidx"], int)
        assert isinstance(payload["fps"], float)
        assert isinstance(payload["cam_ok"], bool)
        assert payload["notices"] == [NOTICE_CN]
        fatigue = payload["dms"]["fatigue"]
        assert set(fatigue) == {"enabled", "alarm_buzzer", "state", "score", "face_present",
                                "face_box", "eye_dark_ratio",
                                "mouth_open_ratio", "jaw_open", "mouth_open",
                                "eyes_closed", "mouth_open_duration_s",
                                "eye_closed_duration_s", "eye_closure", "perclos",
                                "yawn_count_60s", "yaw_deg", "pitch_deg", "reason",
                                "infer_count", "last_infer_fidx", "last_infer_ms",
                                "inference_fps", "result_age_s"}
        helmet = payload["dms"]["helmet"]
        assert set(helmet) == {"enabled", "infer_count", "last_infer_fidx",
                               "last_infer_ms", "inference_fps",
                               "result_age_s", "target_fps",
                               "thermal_suspended", "verdict_counts", "persons"}
        assert set(payload["dms"]["thermal"]) == {
            "state", "degradation_reason"}
        assert fatigue["enabled"] is True
        assert fatigue["alarm_buzzer"] is False
        assert fatigue["state"] in ("DISABLED", "UNKNOWN", "NORMAL",
                                    "DROWSY_WARN", "DROWSY_ALARM")
        assert helmet["persons"] == []
        # 下面这几个字符串本身就是契约 §7 的禁词：它们只用于断言
        # "禁词不出现在 /state 响应体里"。
        body_text = body.decode("utf-8")
        for forbidden in ("accuracy", "precision", "probability",
                          "confidence", "recall"):
            assert forbidden not in body_text
    finally:
        fx.close()


def test_post_config_applies_and_returns_full_snapshot():
    fx = _fixture()
    try:
        request = urllib.request.Request(
            fx.url("/api/dms_config"),
            data=json.dumps({"fatigue_enabled": False}).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=5) as response:
            assert response.status == 200
            out = json.loads(response.read().decode("utf-8"))
        assert out["ok"] is True
        assert out["fatigue"]["enabled"] is False
        assert out["fatigue"]["state"] == "DISABLED"
        assert out["helmet"]["enabled"] is True
        assert out["dms"]["fatigue"]["enabled"] is False
        assert fx.runtime.fatigue_enabled is False

        # /state 回读必须与 POST 返回一致
        _, body, _ = fx.get("/state")
        payload = json.loads(body.decode("utf-8"))
        assert payload["dms"]["fatigue"]["enabled"] is False
        assert payload["dms"]["fatigue"]["state"] == "DISABLED"

        # 重新打开 -> 恢复 enabled
        request = urllib.request.Request(
            fx.url("/api/dms_config"),
            data=json.dumps({"helmet_enabled": False}).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=5) as response:
            out = json.loads(response.read().decode("utf-8"))
        assert out["helmet"]["enabled"] is False
        assert out["helmet"]["persons"] == []
    finally:
        fx.close()


def test_invalid_json_and_unknown_keys_keep_values():
    fx = _fixture()
    try:
        request = urllib.request.Request(
            fx.url("/api/dms_config"), data=b"{not json",
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=5) as response:
            assert response.status == 200
            out = json.loads(response.read().decode("utf-8"))
        assert out["fatigue"]["enabled"] is True
        assert out["helmet"]["enabled"] is True

        request = urllib.request.Request(
            fx.url("/api/dms_config"),
            data=json.dumps({"unknown": 1, "fatigue_enabled": "yes"}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=5) as response:
            out = json.loads(response.read().decode("utf-8"))
        assert out["fatigue"]["enabled"] is True
        assert fx.runtime.fatigue_enabled is True
    finally:
        fx.close()


def test_health_favicon_404_and_frame_routes():
    fx = _fixture()
    try:
        _, body, _ = fx.get("/health")
        assert json.loads(body.decode("utf-8"))["ok"] is True

        status, body, _ = fx.get("/favicon.ico")
        assert status == 204 and body == b""

        try:
            fx.get("/definitely-not-a-route")
            raise AssertionError("unknown route must be 404")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404

        status, body, headers = fx.get("/frame")
        assert status == 200 and "image/jpeg" in headers.get("Content-Type", "")
        assert len(body) > 100

        fx.server.push(np.zeros((240, 320, 3), dtype=np.uint8),
                       {"fidx": 7, "fps": 12.5, "mode": "web",
                        "cam_ok": True, "source": "synthetic"})
        _, body, _ = fx.get("/frame")
        assert body[:2] == b"\xff\xd8"                 # JPEG SOI
        _, payload, _ = fx.get("/state")
        state = json.loads(payload.decode("utf-8"))
        assert state["fidx"] == 7 and state["cam_ok"] is True
        assert state["source"] == "synthetic"
    finally:
        fx.close()


# -- UI v2 新增断言（规格 §9.3）--------------------------------------------

DEBUG_MARKERS = ("score", "infer", "fidx", "fps", "eye", "mouth", "persons",
                 "wn/u")


def _page(debug):
    server = DmsWebServer("127.0.0.1", 0,
                          runtime=DmsRuntime(fatigue_enabled=True,
                                             helmet_enabled=True,
                                             notices=[NOTICE_CN]),
                          notices=[NOTICE_CN], debug=debug)
    try:
        return server.page_html()
    finally:
        server.server_close()


def test_debug_block_is_hidden_by_default_and_open_with_debug_flag():
    default_page = _page(False)
    debug_page = _page(True)
    assert '<div id="debug" hidden>' in default_page
    assert '<div id="debug" hidden>' not in debug_page
    assert '<div id="debug">' in debug_page


def test_status_panel_replaces_bar_and_exposes_dom_ids():
    page = _page(False)
    assert 'id="bar"' not in page                       # 旧的 12 项状态栏已删除
    for element in ("status", "camState", "faceState", "dbgMode",
                    "dbgPersonList", "fatigueEnabled", "helmetEnabled"):
        assert 'id="%s"' % element in page


def test_notice_appears_exactly_once_on_the_page():
    page = _page(False)
    assert page.count(NOTICE_CN) == 1


def test_default_visible_region_has_no_debug_markers():
    page = _page(False)
    start = page.index("<body>")
    end = page.index('<div id="debug"')
    visible = page[start:end]
    for marker in DEBUG_MARKERS:
        assert marker not in visible, marker
    # 默认可见区必须含 4 类信息：两个开关 + 相机 + 人脸
    for element in ("fatigueState", "helmetState", "camState", "faceState"):
        assert element in visible


def test_page_has_helmet_summary_mapping_and_five_chinese_verdicts():
    page = _page(False)
    assert "function helmetSummary(" in page
    for text in ("已关闭", "无人体框", "已佩戴", "未佩戴", "无法判定"):
        assert text in page, text


def test_css_font_size_floors_and_debug_monospace():
    page = _page(False)
    assert "font-size:16px" in page          # #ctl
    assert "font-size:15px" in page          # #status
    assert "font-size:14px" in page          # #notice
    assert "font-size:13px" in page          # #debug
    assert "monospace" in page
    # 页面内热键 d：含 debugForced 与输入框保护
    assert "debugForced" in page
    assert "TEXTAREA" in page


# -- t12：JS↔Python 映射逐键锁定 + 调试写入门控（t9 F1–F4）-------------------

import colorsys  # noqa: E402  (色族分桶用)
import re  # noqa: E402  (就近放置：仅本节解析 JS 字面量用)

from app.dms import render  # noqa: E402

_DEBUG_WRITER_TOKENS = ("score", "infer", "fidx", "fps", "eye", "mouth")

# 语义档位名（CSS 类）与其色族的对应。**档位名由 STATE_COLOR 的色族派生**，
# 不写第二张手写"状态 -> 类名"表（t15 的核心要求）。
_TIER_BY_FAMILY = {"gray": "k", "amber": "warn", "green": "ok", "red": "bad"}


def bgr_to_rgb(value):
    """OpenCV BGR 元组 -> RGB（跨 BGR/CSS 两域只能比较**色族**，不比较色值）。"""
    return (int(value[2]), int(value[1]), int(value[0]))


def hex_rgb(value):
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def color_family(rgb):
    """粗粒度色族：低饱和 -> gray；否则按 hue 分桶 red/amber/green/blue。

    **刻意不做紧 hue 容差**：render 的 NORMAL 色与 CSS `.ok` 的 hue 相差 55.2°，
    而 DISABLED 在 render 侧是纯灰（s=0，hue 无定义）——任何 `Δhue <= N°` 断言
    都会恒假（t15 已实证）。色族相等才是既**可满足**又有判别力的硬断言。
    """
    r, g, b = [c / 255.0 for c in rgb]
    h, s, _v = colorsys.rgb_to_hsv(r, g, b)
    if s < 0.25:
        return "gray"
    degree = h * 360.0
    if degree < 25.0 or degree >= 335.0:
        return "red"
    if degree < 70.0:
        return "amber"
    if degree < 200.0:
        return "green"
    return "blue"


def derive_state_cls():
    """由 `render.STATE_COLOR` 派生期望的 `状态 -> CSS 档位`（唯一真源是 STATE_COLOR）。"""
    return {state: _TIER_BY_FAMILY[color_family(bgr_to_rgb(color))]
            for state, color in render.STATE_COLOR.items()}


def partition_of(mapping):
    """映射诱导的划分（值 -> 该值对应的键集合），用于分区同构比较。"""
    buckets = {}
    for key, value in mapping.items():
        buckets.setdefault(value, set()).add(key)
    return {frozenset(keys) for keys in buckets.values()}


def css_class_colors(page):
    """抽出页面 CSS 里 `.cls{color:#rrggbb}` 的档位类 -> 色值。"""
    return dict(re.findall(
        r"\.([A-Za-z_][A-Za-z0-9_]*)\{color:(#[0-9a-fA-F]{6})\}", page))


def js_map(page, name):
    """解析页面 JS 里的 `const NAME={...};` 对象字面量 -> dict。"""
    match = re.search(r"const\s+%s\s*=\s*\{(.*?)\}\s*;" % re.escape(name),
                      page, re.S)
    assert match, "JS 里找不到对象字面量 %s" % name
    return dict(re.findall(r'([A-Za-z_][A-Za-z0-9_]*)\s*:\s*"([^"]*)"',
                           match.group(1)))


def js_priority(page):
    """解析 helmetSummary() 里 `if(seen.X) return 'Y';` 的**出现顺序**。"""
    start = page.index("function helmetSummary(")
    body = page[start:]
    end = body.find("\n}")
    if end != -1:
        body = body[:end]
    return re.findall(r"if\(seen\.([A-Za-z_]+)\)\s*return\s*'([a-z_]+)'", body)


def lock_problems(page):
    """返回映射/优先级锁定问题清单；空列表 = 全部锁死。

    这条检查就是 t9 F1–F3 的封堵点：JS 表与聚合规则**逐键/同序**对齐 render.py。
    """
    problems = []
    if js_map(page, "STATE_CN") != render.STATE_TEXT_CN:
        problems.append("STATE_CN != render.STATE_TEXT_CN")
    # STATE_CLS：语义档位同构（t15）——期望值由 STATE_COLOR 派生，**不比较色值**
    state_cls = js_map(page, "STATE_CLS")
    expected_cls = derive_state_cls()
    if state_cls != expected_cls:
        problems.append("STATE_CLS 档位 != 由 STATE_COLOR 派生的期望档位 %r"
                        % (expected_cls,))
    # 分区同构：同色必同档、异色必异档
    by_color = {}
    for state, color in render.STATE_COLOR.items():
        by_color.setdefault(color, set()).add(state_cls.get(state))
    if any(len(tiers) != 1 for tiers in by_color.values()):
        problems.append("STATE_CLS 破坏 STATE_COLOR 分区（同色不同档）")
    resolved = {color: next(iter(tiers))
                for color, tiers in by_color.items() if len(tiers) == 1}
    if len(set(resolved.values())) != len(resolved):
        problems.append("STATE_CLS 给不同颜色分配了同一档（异色同档）")
    # CSS 必须定义所用档位类，且其色族与对应语义的色族一致（容差 = 色族）
    css = css_class_colors(page)
    for state, cls in state_cls.items():
        if cls not in css:
            problems.append("CSS 未定义档位类 .%s（被 %s 使用）" % (cls, state))
            continue
        color = render.STATE_COLOR.get(state)
        if color is None:
            continue
        if color_family(hex_rgb(css[cls])) != color_family(bgr_to_rgb(color)):
            problems.append("CSS .%s 的色族与 %s 的语义色族不一致" % (cls, state))
    if js_map(page, "HELM_CN") != render.HELMET_SUMMARY_CN:
        problems.append("HELM_CN != render.HELMET_SUMMARY_CN")
    if js_map(page, "VERDICT_CN") != render.VERDICT_TEXT_CN:
        problems.append("VERDICT_CN != render.VERDICT_TEXT_CN")
    # 聚合优先级必须与 Python helmet_summary() 同序：not_worn > unknown > worn
    pairs = js_priority(page)
    if [key for key, _ in pairs] != ["not_worn", "unknown"]:
        problems.append("helmetSummary 的判定顺序 != [not_worn, unknown]")
    if [val for _, val in pairs] != ["not_worn", "unknown"]:
        problems.append("helmetSummary 的返回结论与条件不一致")
    return problems


def test_js_mapping_literals_are_locked_to_render_tables():
    """F1–F3：JS 的 4 张表逐键等于 render.py 的表，聚合优先级同序。"""
    problems = lock_problems(_page(False))
    assert problems == [], problems
    # 逐键断言（失败时给出具体键）
    assert js_map(_page(False), "STATE_CN") == render.STATE_TEXT_CN
    assert js_map(_page(False), "HELM_CN") == render.HELMET_SUMMARY_CN
    assert js_map(_page(False), "VERDICT_CN") == render.VERDICT_TEXT_CN

    # STATE_CLS：与由 STATE_COLOR 派生的语义档位**逐状态相等**（t15）
    state_cls = js_map(_page(False), "STATE_CLS")
    assert state_cls == derive_state_cls()
    assert derive_state_cls() == {"DISABLED": "k", "UNKNOWN": "warn",
                                  "NORMAL": "ok", "DROWSY_WARN": "warn",
                                  "DROWSY_ALARM": "bad"}
    # 分区同构：STATE_COLOR 的颜色划分与 STATE_CLS 的档位划分逐块相等
    assert partition_of(render.STATE_COLOR) == partition_of(state_cls)
    # CSS 里确实定义了所用档位类，且其色族与对应语义一致（**不比较色值**）
    css = css_class_colors(_page(False))
    assert set(derive_state_cls().values()) <= set(css)
    for state, cls in derive_state_cls().items():
        assert color_family(hex_rgb(css[cls])) == \
            color_family(bgr_to_rgb(render.STATE_COLOR[state]))

    # 与 Python 聚合规则同序（同时用行为断言交叉验证）
    assert js_priority(_page(False)) == [("not_worn", "not_worn"),
                                         ("unknown", "unknown")]
    for verdicts, expected in ((["worn"], "worn"),
                               (["worn", "unknown"], "unknown"),
                               (["worn", "not_worn"], "not_worn"),
                               ([], "no_person")):
        snap = {"helmet": {"enabled": True,
                           "persons": [{"verdict": v} for v in verdicts]}}
        assert render.helmet_summary(snap) == expected


def _mutate(page, old, new):
    assert old in page, "变异体锚点未命中：%r" % old[:60]
    return page.replace(old, new, 1)


def test_lock_cases_catch_the_three_mutants(capsys):
    """映射/优先级族变异体必须被抓住（t12 起的名；t15 起扩到 5 个变体）。

    用例名**刻意保留**，以便与 t14/t17 证据中对它的引用对齐（t14 曾以它为
    "M1a/M1b/M2/M3a/M3b 全部 CAUGHT"的失败用例名）。
    """
    page = _page(False)
    assert lock_problems(page) == []

    mutants = {
        "M1_HELM_CN_worn_not_worn_swapped": _mutate(
            page, 'not_worn:"未佩戴",unknown:"无法判定",worn:"已佩戴"',
            'not_worn:"已佩戴",unknown:"无法判定",worn:"未佩戴"'),
        "M1b_VERDICT_CN_worn_not_worn_swapped": _mutate(
            page, 'VERDICT_CN={worn:"已佩戴",not_worn:"未佩戴"',
            'VERDICT_CN={worn:"未佩戴",not_worn:"已佩戴"'),
        "M2_drowsy_alarm_to_normal": _mutate(
            page, 'DROWSY_ALARM:"疲劳报警"', 'DROWSY_ALARM:"正常"'),
        "M3a_priority_inverted_worn_first": _mutate(
            page,
            "  if(seen.not_worn) return 'not_worn';\n"
            "  if(seen.unknown) return 'unknown';",
            "  if(seen.worn) return 'worn';\n"
            "  if(seen.not_worn) return 'not_worn';"),
        "M3b_priority_lines_swapped": _mutate(
            page,
            "  if(seen.not_worn) return 'not_worn';\n"
            "  if(seen.unknown) return 'unknown';",
            "  if(seen.unknown) return 'unknown';\n"
            "  if(seen.not_worn) return 'not_worn';"),
    }

    caught = {}
    for name, mutated in mutants.items():
        assert mutated != page, name
        problems = lock_problems(mutated)
        caught[name] = problems
        print("[t12 mutation] %s -> %s" % (name, problems or "NOT CAUGHT"))

    for name, problems in caught.items():
        assert problems, "变异体未被抓住：%s" % name
    assert set(caught) == {"M1_HELM_CN_worn_not_worn_swapped",
                           "M1b_VERDICT_CN_worn_not_worn_swapped",
                           "M2_drowsy_alarm_to_normal",
                           "M3a_priority_inverted_worn_first",
                           "M3b_priority_lines_swapped"}
    # 原始输出留痕（pytest -s 或失败时可见）
    captured = capsys.readouterr().out
    assert "[t12 mutation] M1_HELM_CN_worn_not_worn_swapped ->" in captured


def test_lock_cases_catch_state_cls_tier_mutants(capsys):
    """t15 的直接动因：`STATE_CLS` 档位换错必须被抓住（t12 时实测 NOT CAUGHT）。

    两个方向都跑：`DROWSY_WARN: warn->ok` 与 `NORMAL: ok->warn` / `ok->bad`。
    三者都由**三重独立检查**联合捕捉：派生逐状态相等 + 分区同构 + CSS 色族一致。
    """
    page = _page(False)
    assert lock_problems(page) == []

    mutants = {
        "M4a_drowsy_warn_warn_to_ok": _mutate(
            page, 'DROWSY_WARN:"warn"', 'DROWSY_WARN:"ok"'),
        "M4b_normal_ok_to_warn": _mutate(
            page, 'NORMAL:"ok"', 'NORMAL:"warn"'),
        "M4c_normal_ok_to_bad": _mutate(
            page, 'NORMAL:"ok"', 'NORMAL:"bad"'),
    }

    caught = {}
    for name, mutated in mutants.items():
        assert mutated != page, name
        problems = lock_problems(mutated)
        caught[name] = problems
        print("[t15 mutation] %s -> %s" % (name, problems or "NOT CAUGHT"))

    for name, problems in caught.items():
        assert problems, "变异体未被抓住：%s" % name
    assert set(caught) == {"M4a_drowsy_warn_warn_to_ok",
                           "M4b_normal_ok_to_warn",
                           "M4c_normal_ok_to_bad"}
    # M4a/M4b/M4c 必须**至少**被分区同构或派生相等抓住（不依赖单一断言）
    assert any("分区" in p for p in caught["M4a_drowsy_warn_warn_to_ok"])
    assert any("派生" in p for p in caught["M4b_normal_ok_to_warn"])
    captured = capsys.readouterr().out
    assert "[t15 mutation] M4a_drowsy_warn_warn_to_ok ->" in captured


def test_debug_writer_is_absent_from_static_page_when_debug_off():
    """F4：debug=False 的静态全文不含调试写入器；debug=True 才注入且被开关门控。"""
    off = _page(False)
    on = _page(True)

    for token in _DEBUG_WRITER_TOKENS:
        assert token not in off, token
    assert "writeDebug" not in off
    assert "if(debugOn)" not in off

    # 残留登记：`persons` 在 debug=False 页面上**只应出现 1 次**，
    # 即 helmetSummary() 读取 helmet.persons（§4.2 聚合规则强制要求）。
    # 字面地"全文不含 persons"与 §4.2 互斥（详见 t12 output 的偏差登记）。
    assert off.count("persons") == 1

    # debug=True：写入器在、以调试开关门控、14 类字段访问齐全
    assert "writeDebug" in on
    assert "if(debugOn) writeDebug(d);" in on
    for token in _DEBUG_WRITER_TOKENS + ("persons",):
        assert token in on, token
    for element in ("dbgMode", "dbgSource", "dbgFidx", "dbgFps", "dbgCam",
                    "dbgScore", "dbgEye", "dbgMouth", "dbgFatigueInfer",
                    "dbgFatigueFidx", "dbgFatigueMs", "dbgPersons",
                    "dbgCounts", "dbgHelmetInfer", "dbgHelmetFidx",
                    "dbgHelmetMs", "dbgPersonList"):
        assert 'id="%s"' % element in on
    # debug=False 时这些 id 是**无 token 的 CamelCase 骨架**（不含小写调试 token）
    for element in ("dbgScore", "dbgFidx", "dbgFps", "dbgEye", "dbgMouth"):
        assert 'id="%s"' % element in off
