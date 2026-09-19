#!/usr/bin/env python3
"""DMS demo entry point — USB 疲劳检测 + 头盔佩戴检测（演示级）。

用法:
  .venv/bin/python -u app/dms_app.py --mode web      --camera usb:0
  .venv/bin/python -u app/dms_app.py --mode display  --camera usb:0
  .venv/bin/python -u app/dms_app.py --mode web --headless --camera synthetic

诚实口径: 疲劳使用 MediaPipe Face Landmarker 的眼部/口部 blendshape 与头部姿态，
再由固定时间阈值生成可解释状态；未做驾驶员个体标定，无近红外夜间/强逆光不可用。
头盔使用实验性的社区 PPE 模型。两者都不构成安全认证。详见 docs/dms_helmet_demo.md。

退出码（契约 §4.9 冻结）:
  0 正常结束   2 配置/参数错误   3 相机源不可用   4 display 无可用 X
  5 web 端口被占用
任何非零退出都必须给出**单行可操作结论**，不得把 traceback 作为用户可见错误
（traceback 只在 --debug 下作为附加内容）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.dms.camera import CameraUnavailable, open_dms_source  # noqa: E402
from app.dms.config import load_dms_config  # noqa: E402
from app.dms.events import DmsEventLogger  # noqa: E402
from app.dms.face import FaceDetector, HaarUnavailable  # noqa: E402
from app.dms.fatigue import (FatigueEngine, LandmarkFatigueEngine,  # noqa: E402
                              LandmarkUnavailable)
from app.dms.helmet import HelmetEngine, make_person_detector  # noqa: E402
from app.dms.ppe import ModelHelmetEngine, PpeUnavailable, TensorRTPpeDetector  # noqa: E402
from app.dms.render import (HOTKEY_DEBUG, FrameResult, WINDOW_NAME,  # noqa: E402
                            draw_dms_frame, map_mouse_to_canvas,
                            resolve_control_hit)
from app.dms.state import DmsRuntime  # noqa: E402
from app.dms.web import DmsWebServer  # noqa: E402

EXIT_OK = 0
EXIT_BAD_ARGS = 2
EXIT_CAMERA = 3
EXIT_DISPLAY = 4
EXIT_PORT = 5

STATE_DUMP_INTERVAL_S = 2.0
CAMERA_HINT = ("use --camera usb:<idx> | rtsp://... | video:<file> | "
               "image:<path> | synthetic")
BUSY_HINT = ("/dev/video0 is probably held by another process (Calibration "
             "Studio uses it by default).")
BUSY_HINT2 = ("Stop that consumer, or use --camera video:<file> / "
              "image:<path> / synthetic.")

ENGINE_HINTS = {
    "fatigue": "fatigue.backend=%s is unsupported; use landmarks",
    "helmet": "helmet.backend=%s is unsupported; use model",
}


def _parse_cli(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        "dms_app", description="DMS helmet/fatigue demo (demo level)")
    parser.add_argument("--mode", choices=("web", "display"), default="web")
    parser.add_argument("--headless", action="store_true",
                        help="web 模式：只服务，不创建窗口")
    # 契约 §12 C1b：一个参数两个写法
    parser.add_argument("--camera", "--source", dest="camera", default=None,
                        help="usb:<idx> | rtsp://... | video:<file> | "
                             "image:<path> | synthetic（--source 是同义写法）")
    parser.add_argument("--config", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--host", default=None)
    parser.add_argument("--fatigue", choices=("on", "off"), default=None)
    parser.add_argument("--helmet", choices=("on", "off"), default=None)
    parser.add_argument("--max-frames", type=int, default=0)
    parser.add_argument("--run-seconds", type=float, default=0.0)
    parser.add_argument("--out", default=None,
                        help="保留参数：本 demo 只写事件 JSONL，不写 CSV")
    parser.add_argument("--fullscreen", action="store_true")
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args(argv)


def _emit(message: str, code: int, prefix: str = "ERROR") -> None:
    print(f"[dms] {prefix}: {message}", file=sys.stderr, flush=True)
    raise SystemExit(code)


def display_available(display: str = "") -> bool:
    """DISPLAY 有可用的本地 X socket（或 ssh -X 主机前缀）。"""
    value = display or os.environ.get("DISPLAY", "")
    if not value:
        return False
    if ":" not in value:
        return False
    host, rest = value.split(":", 1)
    if host not in ("", "unix"):
        return True                      # ssh -X 转发：交给客户端
    number = rest.split(".")[0]
    if not number.isdigit():
        return False
    return os.path.exists(f"/tmp/.X11-unix/X{number}")


def _resolve_switch(cli_value, cfg, path: str):
    """优先级: cli > configs/dms.yaml > DEFAULTS（契约 §5.2）。"""
    if cli_value is not None:
        return cli_value == "on", "cli"
    if cfg.has_raw(path):
        return bool(cfg.get(path)), "config"
    return bool(cfg.get(path)), "default"


def process_frame(frame, gray, fidx: int, *, runtime: DmsRuntime,
                  fatigue_engine=None, helmet_engine=None,
                  person_detector=None, run_fatigue: bool = True,
                  cached_fatigue=None, run_helmet: bool = True,
                  cached_helmet=None) -> FrameResult:
    """一帧的推理门控（契约 §3.4 冻结结构）。

    `infer_count` 只在真正调用了该路引擎时自增；关闭的路径**不会**进入分支
    （不是"跑完不画"），因此关闭后计数必然冻结。
    """
    result = FrameResult(fidx, time.time())
    if runtime.fatigue_enabled and fatigue_engine is not None and run_fatigue:
        started = time.perf_counter()
        fatigue_result = fatigue_engine.update(frame, gray)
        runtime.note_fatigue_infer(
            (time.perf_counter() - started) * 1000.0, fidx)
        runtime.set_fatigue_view(fatigue_result)
        result.fatigue = fatigue_result
    elif runtime.fatigue_enabled and fatigue_engine is not None:
        result.fatigue = cached_fatigue
    else:
        runtime.set_fatigue_view(None)

    if (runtime.helmet_enabled and runtime.helmet_inference_allowed
            and helmet_engine is not None and run_helmet):
        started = time.perf_counter()
        detections = (person_detector.infer(frame)
                      if person_detector is not None else [])
        verdicts = helmet_engine.update(frame, detections)
        runtime.note_helmet_infer(
            (time.perf_counter() - started) * 1000.0, fidx)
        runtime.set_helmet_view(verdicts)
        result.helmet = verdicts
    elif runtime.helmet_enabled and helmet_engine is not None:
        result.helmet = cached_helmet
    else:
        runtime.set_helmet_view(None)
    return result


class _LazyPpeDetector:
    """TensorRT PPE detector loads once; failure is cached and visible."""

    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self.detector = None
        self.loaded = False

    def get(self):
        if not self.loaded:
            self.loaded = True
            print("[dms] helmet: loading experimental PPE TensorRT model ...", flush=True)
            try:
                self.detector = TensorRTPpeDetector(
                    str(self.cfg.get("helmet.engine", "")),
                    input_size=int(self.cfg.get("helmet.input_size", 640)),
                    confidence=float(self.cfg.get("helmet.confidence", 0.45)),
                    iou=float(self.cfg.get("helmet.iou", 0.50)))
            except PpeUnavailable as exc:
                print("[dms] ERROR: helmet model unavailable: %s" % exc,
                      file=sys.stderr, flush=True)
                self.detector = None
        return self.detector

    @property
    def name(self) -> str:
        return str(getattr(self.detector, "name", "-"))


def _open_camera(source: str, cfg, debug: bool,
                 provenance: str = "config"):
    try:
        return open_dms_source(
            source, open_timeout_s=float(cfg.get("camera.open_timeout_s", 8.0)),
            debug=debug, fps=float(cfg.get("camera.fps", 30)),
            width=int(cfg.get("camera.width", 0)),
            height=int(cfg.get("camera.height", 0)),
            provenance=provenance)
    except CameraUnavailable as exc:
        if exc.reason == "no_first_frame":
            timeout = float(cfg.get("camera.open_timeout_s", 8.0))
            print(f"[dms] ERROR: camera source '{source}' open failed: "
                  f"no first frame within {timeout:.1f}s", file=sys.stderr)
            print(f"[dms]   {BUSY_HINT}", file=sys.stderr)
            print(f"[dms]   {BUSY_HINT2}", file=sys.stderr)
        else:
            print(f"[dms] ERROR: camera source '{source}' open failed: "
                  f"{exc}", file=sys.stderr)
            print(f"[dms]   {CAMERA_HINT}", file=sys.stderr)
            if source.startswith("usb:"):
                # 契约 §3.5：本机驱动在 open 阶段就拒绝第二个打开者，
                # 因此 usb 源的 open 失败必须给出"被别的进程占着"的病因提示
                # （仍是退出码 3 / 单行 ERROR / 无 traceback）。
                print(f"[dms]   {BUSY_HINT}", file=sys.stderr)
                print(f"[dms]   {BUSY_HINT2}", file=sys.stderr)
        sys.stderr.flush()
        raise SystemExit(EXIT_CAMERA)


def _source_size(cap) -> str:
    """实际分辨率（驱动可能给出与请求不同的模式，如实打印出来）。"""
    try:
        return "%dx%d" % (int(cap.width), int(cap.height))
    except Exception:  # noqa: BLE001
        pass
    try:
        return "%dx%d" % (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                          int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    except Exception:  # noqa: BLE001
        return "unknown"


def _run(args: argparse.Namespace) -> int:
    if args.mode == "display" and args.headless:
        _emit("--mode display --headless is not supported: "
              "use --mode web --headless", EXIT_BAD_ARGS)

    cfg = load_dms_config(args.config)

    for scope in ("fatigue", "helmet"):
        backend = str(cfg.get(f"{scope}.backend", ""))
        implemented = {"fatigue": ("landmarks",),
                       "helmet": ("model",)}[scope]
        if backend not in implemented:
            _emit(ENGINE_HINTS[scope] % backend, EXIT_BAD_ARGS)

    fatigue_on, fatigue_src = _resolve_switch(args.fatigue, cfg,
                                              "fatigue.enabled")
    helmet_on, helmet_src = _resolve_switch(args.helmet, cfg, "helmet.enabled")
    if args.fatigue is not None or args.helmet is not None:
        source_label = "cli"
    elif fatigue_src == "config" or helmet_src == "config":
        source_label = "config"
    else:
        source_label = "default"
    print(f"[dms] switches: fatigue={'ON' if fatigue_on else 'OFF'} "
          f"helmet={'ON' if helmet_on else 'OFF'} (source: {source_label})",
          flush=True)

    runtime = DmsRuntime(
        fatigue_enabled=fatigue_on,
        helmet_enabled=helmet_on,
        fatigue_alarm_buzzer=bool(cfg.get("fatigue.alarm_buzzer", False)),
        notices=[cfg.notices_cn],
    )
    initial_thermal = os.environ.get("DMS_THERMAL_STATE", "normal")
    if initial_thermal in ("constrained", "critical"):
        runtime.apply_config({
            "thermal_state": initial_thermal,
            "helmet_target_fps": 2.0 if initial_thermal == "constrained" else 0.0,
            "helmet_suspended": initial_thermal == "critical",
            "degradation_reason": "Hub thermal policy active at startup",
        })
    events = DmsEventLogger(
        path=os.path.join(str(cfg.get("logger.dir", "logs")),
                          str(cfg.get("logger.events_file",
                                      "dms_events.jsonl"))),
        enabled=bool(cfg.get("logger.enabled", True)))
    events.log("session", {
        "mode": args.mode, "camera": str(args.camera
                                         or cfg.get("camera.source", "")),
        "fatigue_enabled": fatigue_on, "helmet_enabled": helmet_on,
        "fatigue_backend": str(cfg.get("fatigue.backend", "")),
        "helmet_backend": str(cfg.get("helmet.backend", "")),
        "note": "experimental models; fatigue is landmark-derived and PPE "
                "is a community hard-hat model, not certified safety logic"})

    source = str(args.camera or os.environ.get("DMS_CAMERA")
                 or cfg.get("camera.source", "usb:0"))
    if os.environ.get("DMS_FIXED_USB_ONLY") == "1" and source != "usb:0":
        _emit("Visual Hub fixes the cabin camera to usb:0; synthetic/RTSP fallback is disabled",
              EXIT_BAD_ARGS)
    if args.camera is not None:
        camera_from = "cli"
    elif cfg.has_raw("camera.source"):
        camera_from = "config"
    else:
        camera_from = "default"
    if args.port is not None or args.host is not None:
        web_from = "cli"
    elif cfg.has_raw("web.port") or cfg.has_raw("web.host"):
        web_from = "config"
    else:
        web_from = "default"
    if cfg.has_raw("camera.width") or cfg.has_raw("camera.height"):
        resolution_from = "config"
    else:
        resolution_from = "default"

    cap, label = _open_camera(source, cfg, bool(args.debug),
                              provenance=resolution_from)
    size = _source_size(cap)
    # 契约 §4.9 冻结的启动摘要第三行（来源标签）；与下面信息更全的一行并存
    print(f"[dms] camera: {label} (source: {camera_from})", flush=True)
    print(f"[dms] camera: {label} {size} (camera_source: {source}; from: {camera_from})", flush=True)

    port = int(args.port if args.port is not None
               else cfg.get("web.port", 8010))
    host = str(args.host or cfg.get("web.host", "0.0.0.0"))
    web = None
    if args.mode == "web":
        try:
            web = DmsWebServer(host, port, runtime=runtime,
                               notices=[cfg.notices_cn],
                               debug=bool(args.debug))
        except OSError as exc:
            try:
                cap.release()
            except Exception:  # noqa: BLE001
                pass
            _emit(f"cannot bind {host}:{port} ({exc}) — the port is in use; "
                  f"stop that process or pass --port / DMS_PORT", EXIT_PORT)

    want_window = (not args.headless) and display_available()
    if args.mode == "display" and not want_window:
        if web is not None:
            web.server_close()
        try:
            cap.release()
        except Exception:  # noqa: BLE001
            pass
        _emit("no usable X display for --mode display; use --mode web or set "
              "GUI_DISPLAY=:0", EXIT_DISPLAY)

    fatigue_engine = None
    try:
        fatigue_engine = LandmarkFatigueEngine(cfg, on_event=events.log)
        print("[dms] fatigue: backend=landmarks model=FaceLandmarker", flush=True)
    except LandmarkUnavailable as exc:
        print(f"[dms] ERROR: fatigue: {exc} — fatigue detection disabled",
              file=sys.stderr, flush=True)
        runtime.set_flag("fatigue_enabled", False)
        events.log("error", {"scope": "fatigue", "error": str(exc)})

    helmet_engine = ModelHelmetEngine(
        min_overlap=float(cfg.get("helmet.association_iou", 0.15)),
        on_event=events.log)
    lazy_person = _LazyPpeDetector(cfg)
    # 在建窗之前预加载人体模型：GTK 起来之后再加载 torch/TensorRT 会长时间阻塞主循环
    # （真机实测 5–10s，期间画面与 /state 都停在旧状态），Jetson 上还叠加 GTK+TRT 的
    # static-TLS 风险（脚本用 LD_PRELOAD 规避）。预加载只影响"模型什么时候载入"，
    # 不影响"真停止"：关闭头盔时一律不调用推理，infer_count 仍然冻结。
    if runtime.helmet_inference_allowed:
        # 只在头盔**启用**时预加载：关闭状态下不该付 torch/TRT 的启动与显存代价，
        # 也不该给已关闭的路写误导性 error 事件（reviewer observation #1 + captain 第 2 条）。
        # 预加载放在建窗之前，避免 GTK 就绪后再加载导致主循环阻塞 5–10s（脚本另有 LD_PRELOAD 规避）。
        lazy_person.get()
        if lazy_person.detector is None:
            runtime.set_flag("helmet_enabled", False)
            events.log("error", {"scope": "helmet",
                                 "error": "person model unavailable"})
    print(f"[dms] helmet: backend=model detector={lazy_person.name}",
          flush=True)

    if web is not None:
        web.start()
        # 契约 §4.9 冻结的启动摘要串（含端口来源 provenance）
        print(f"[dms] web listening on {host}:{port} (source: {web_from})",
              flush=True)
        print(f"[dms] web: mode=web headless={bool(args.headless)}",
              flush=True)

    exit_code = EXIT_OK
    try:
        exit_code = _run_loop(args, cfg, runtime, events, cap, label, web,
                              fatigue_engine, helmet_engine, lazy_person,
                              want_window)
    except KeyboardInterrupt:
        exit_code = EXIT_OK
    except CameraUnavailable as exc:
        timeout = float(cfg.get("camera.open_timeout_s", 8.0))
        print(f"[dms] ERROR: camera source '{source}' open failed: {exc}",
              file=sys.stderr)
        print(f"[dms]   {BUSY_HINT}", file=sys.stderr)
        print(f"[dms]   {BUSY_HINT2}", file=sys.stderr)
        exit_code = EXIT_CAMERA
    finally:
        try:
            cap.release()
        except Exception:  # noqa: BLE001
            pass
        if want_window:
            try:
                cv2.destroyAllWindows()
            except Exception:  # noqa: BLE001
                pass
        events.log("session", {"event": "stop", "exit_code": exit_code})
        events.close()
        if web is not None:
            web.stop()
            print("[dms] web: stopped", flush=True)
    print(f"[dms] session done frames={_FRAMES['n']} exit={exit_code} "
          f"fatigue={runtime.snapshot()['fatigue']['state']} "
          f"helmet_persons={len(runtime.snapshot()['helmet']['persons'])}",
          flush=True)
    return exit_code


_FRAMES = {"n": 0}


def _run_loop(args, cfg, runtime, events, cap, label, web, fatigue_engine,
              helmet_engine, lazy_person, want_window) -> int:
    ui_cfg = cfg.get("ui", {}) or {}
    panel_xy = (int(ui_cfg.get("panel_x", 16)), int(ui_cfg.get("panel_y", 96)))
    line_h = int(ui_cfg.get("line_h", 26))
    font_scale = float(ui_cfg.get("font_scale", 0.55))
    hot_fatigue = ord(str(ui_cfg.get("hotkey_fatigue", "1"))[:1])
    hot_helmet = ord(str(ui_cfg.get("hotkey_helmet", "2"))[:1])
    notices_ascii = cfg.notices_ascii

    mouse_state = {"rects": [], "shape": (0, 0), "image_rect": None}

    def on_mouse(event, mx, my, _flags, _param):
        if event != cv2.EVENT_LBUTTONDOWN:
            return
        # 留边/缩放安全（E10/C9）：image_rect 可用时**只**用换算后的画布坐标做
        # hit-test，越界即不命中；**不再**拿原始窗口坐标兜底（那是假命中根因）。
        cx, cy = map_mouse_to_canvas(mx, my, mouse_state["image_rect"],
                                     mouse_state["shape"])
        name = resolve_control_hit(mouse_state["rects"], mx, my,
                                   mouse_state["image_rect"],
                                   mouse_state["shape"])
        if args.debug:
            print(f"[dms] mouse: window=({mx},{my}) "
                  f"image_rect={mouse_state['image_rect']} "
                  f"canvas=({cx},{cy}) hit={name}", flush=True)
        if name is None:
            return
        current = (runtime.fatigue_enabled if name == "fatigue_enabled"
                   else runtime.helmet_enabled)
        runtime.set_flag(name, not current)

    window_ready = False
    window_failed = False
    # UI 调试层开关：只存在于主循环局部变量（禁止进 DmsRuntime.snapshot()，
    # 否则会破 /state schema）。初值 = --debug；热键 d 运行时翻转。
    debug_on = bool(args.debug)
    prev_fatigue = runtime.fatigue_enabled
    prev_helmet = runtime.helmet_enabled
    helmet_unavailable = lazy_person.loaded and lazy_person.detector is None
    fidx = 0
    frames = 0
    bad_reads = 0
    fps_ema = 0.0
    last_ts = 0.0
    started_at = time.time()
    last_dump = 0.0
    fatigue_every = max(1, int(cfg.get("fatigue.infer_interval_frames", 4)))
    cached_fatigue = None
    cached_helmet = None
    next_helmet_at = 0.0
    max_loop_fps = max(8.0, float(cfg.get("runtime.max_fps", 10.0)))

    while True:
        loop_started = time.time()
        ok = False
        frame = None
        try:
            ok, frame = cap.read()
        except Exception as exc:  # noqa: BLE001
            print(f"[dms] camera read error: {type(exc).__name__}: {exc}",
                  flush=True)
            ok = False
        if not ok or frame is None or getattr(frame, "size", 0) == 0:
            bad_reads += 1
            if bad_reads >= 10:
                raise CameraUnavailable(
                    "no frame within 10 consecutive read attempts",
                    "no_first_frame")
            time.sleep(0.02)
            continue
        bad_reads = 0
        fidx += 1
        frames += 1
        _FRAMES["n"] = frames
        now = time.time()
        if last_ts > 0.0:
            instant = 1.0 / max(1e-6, now - last_ts)
            fps_ema = instant if fps_ema <= 0.0 else 0.9 * fps_ema + 0.1 * instant
        last_ts = now
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        # -- 开关：运行时切换在下一个循环边界生效（契约 §5.4）----------------
        fatigue_on = runtime.fatigue_enabled
        if fatigue_on != prev_fatigue:
            if fatigue_engine is not None:
                fatigue_engine.set_enabled(fatigue_on)
            events.log("switch", {"name": "fatigue_enabled", "value": fatigue_on,
                                  "fidx": fidx, "source": "runtime"})
            print(f"[dms] switch: fatigue={'ON' if fatigue_on else 'OFF'} "
                  f"(runtime) fidx={fidx}", flush=True)
            prev_fatigue = fatigue_on
        helmet_on = runtime.helmet_enabled
        if helmet_on != prev_helmet:
            helmet_engine.reset()
            events.log("switch", {"name": "helmet_enabled", "value": helmet_on,
                                  "fidx": fidx, "source": "runtime"})
            print(f"[dms] switch: helmet={'ON' if helmet_on else 'OFF'} "
                  f"(runtime) fidx={fidx}", flush=True)
            prev_helmet = helmet_on

        helmet_target_fps = runtime.helmet_target_fps
        now_mono = time.monotonic()
        run_helmet = bool(
            helmet_on and runtime.helmet_inference_allowed
            and helmet_target_fps > 0.0 and now_mono >= next_helmet_at)
        detector_handle = None
        if run_helmet and not helmet_unavailable:
            detector_handle = lazy_person.get()
            if detector_handle is None:
                helmet_unavailable = True
                print("[dms] ERROR: helmet: person model unavailable — "
                      "helmet detection disabled", file=sys.stderr, flush=True)
                events.log("error", {"scope": "helmet",
                                     "error": "person model unavailable"})
                runtime.set_flag("helmet_enabled", False)
                helmet_on = False
                run_helmet = False
        if run_helmet:
            next_helmet_at = now_mono + (1.0 / helmet_target_fps)

        frame_result = process_frame(
            frame, gray, fidx, runtime=runtime, fatigue_engine=fatigue_engine,
            helmet_engine=helmet_engine, person_detector=detector_handle,
            run_fatigue=(fidx == 1 or fidx % fatigue_every == 0),
            cached_fatigue=cached_fatigue, run_helmet=run_helmet,
            cached_helmet=cached_helmet)
        if frame_result.fatigue is not None:
            cached_fatigue = frame_result.fatigue
        if frame_result.helmet is not None:
            cached_helmet = frame_result.helmet

        canvas = frame
        meta = {"fidx": fidx, "ts": now, "fps": round(fps_ema, 2),
                "mode": args.mode, "cam_ok": True, "source": label}
        # 先推流：stream 面只画检测框 + 短标签（零 HUD/调试/notice/控件像素）
        if web is not None:
            draw_dms_frame(canvas, frame_result, runtime, surface="stream",
                           meta=meta)
            web.push(canvas, meta)
        if want_window:
            # display 面：HUD + 控件行 + (debug_on) 调试块 + notice 条
            layout = draw_dms_frame(canvas, frame_result, runtime,
                                    surface="display", debug=debug_on,
                                    meta=meta)
            mouse_state["rects"] = list(layout.controls)
            mouse_state["shape"] = canvas.shape[:2]

        if args.mode == "display" and now - last_dump >= STATE_DUMP_INTERVAL_S:
            last_dump = now
            print("[dms:state] " + json.dumps(runtime.snapshot(),
                                              default=str), flush=True)

        if want_window and not window_failed:
            try:
                if not window_ready:
                    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
                    # 让初始窗口与画布 1:1，鼠标坐标换算才有确定的意义
                    cv2.resizeWindow(WINDOW_NAME, int(canvas.shape[1]),
                                     int(canvas.shape[0]))
                    cv2.setMouseCallback(WINDOW_NAME, on_mouse)
                    if args.fullscreen:
                        cv2.setWindowProperty(
                            WINDOW_NAME, cv2.WND_PROP_FULLSCREEN,
                            cv2.WINDOW_FULLSCREEN)
                    window_ready = True
                cv2.imshow(WINDOW_NAME, canvas)
                try:
                    mouse_state["image_rect"] = cv2.getWindowImageRect(
                        WINDOW_NAME)
                except Exception:  # noqa: BLE001
                    mouse_state["image_rect"] = None
                key = cv2.waitKey(1) & 0xFF
                if key == HOTKEY_DEBUG:
                    # 只翻转展示布尔：不动 runtime、不影响 infer_count/fidx/事件
                    debug_on = not debug_on
                elif key == hot_fatigue:
                    runtime.set_flag("fatigue_enabled",
                                     not runtime.fatigue_enabled)
                elif key == hot_helmet:
                    runtime.set_flag("helmet_enabled",
                                     not runtime.helmet_enabled)
                elif key in (ord("q"), 27):
                    break
            except cv2.error as exc:
                window_failed = True
                print(f"[dms] display: window unavailable ({exc}) — "
                      "continuing without a window", file=sys.stderr,
                      flush=True)
                try:
                    cv2.destroyAllWindows()
                except Exception:  # noqa: BLE001
                    pass
        elif args.mode == "display":
            # 无窗口的 display 模式不应存在（前面已判定），保底让出 CPU
            time.sleep(0.005)

        remaining = (1.0 / max_loop_fps) - (time.time() - loop_started)
        if remaining > 0:
            time.sleep(remaining)
        if args.max_frames and frames >= int(args.max_frames):
            break
        if args.run_seconds and (now - started_at) >= float(args.run_seconds):
            break
    return EXIT_OK


def main(argv=None) -> int:
    args = _parse_cli(argv)
    try:
        return _run(args)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - 单行结论，traceback 只在 --debug
        if args.debug:
            import traceback
            traceback.print_exc()
        print(f"[dms] ERROR: unexpected failure: {type(exc).__name__}: {exc}",
              file=sys.stderr, flush=True)
        return EXIT_BAD_ARGS


if __name__ == "__main__":
    _code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    # 同 app/warn_app.py: 绕过 cv2/torch 的 atexit 竞态（Jetson 上会挂住）
    os._exit(int(_code))
