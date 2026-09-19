"""Rear-view monocular depth collision-warning app (NX, single camera).

Pipeline:
  capture thread (RTSP/USB/video, reconnect) -> latest BGR frame
  depth thread  (latest frame -> metric depth map) -> latest depth H/W meters
  main thread   (filters/ground/ROI/obstacles -> temporal -> velocity/TTC ->
                 risk engine -> industrial UI render -> keys/calibration)

One window. Keys:
  Q / ESC quit   D debug views (1..6)   C calibration   F fullscreen
  SPACE pause    R record toggle (canvas MP4)  S save calibration

Headless gate mode (CI/validation):
  --headless --max-frames N --out results/warn_gate.csv
  prints a risk-sequence summary and CSV telemetry; no window.
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import threading
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.camera_source import open_source, open_rtsp  # noqa: E402
from app.warning.alarm import AlarmController  # noqa: E402
from app.warning.calibration_ui import CalibrationUI  # noqa: E402
from app.warning.config import load_config  # noqa: E402
from app.warning.depth_backend import make_depth_engine  # noqa: E402
from app.warning.depth_filters import invalidate, spatial_smooth, validity_mask, TemporalDepthFilter  # noqa: E402
from app.warning.ground_filter import ExpectedGroundDepthFilter  # noqa: E402
from app.warning.logger import TelemetryLogger  # noqa: E402
from app.warning.obstacles import extract_candidates, nearest_candidate  # noqa: E402
from app.warning.person_detection import UltralyticsPersonDetector  # noqa: E402
from app.warning.person_risk import PersonRiskAssessor  # noqa: E402
from app.warning.recorder import DangerRecorder  # noqa: E402
from app.warning.renderer import Renderer, CANVAS_W, CANVAS_H  # noqa: E402
from app.warning.risk_engine import RiskEngine, SAFE, WARNING, DANGER, SYSTEM_ERROR  # noqa: E402
from app.warning.roi import DangerRegion, PhysicalCorridor  # noqa: E402
from app.warning.temporal import DistanceEMA, PresenceVoter  # noqa: E402
from app.warning.velocity_ttc import VelocityTTC  # noqa: E402
from app.web_stream import MJPEGServer  # noqa: E402
from app.geometry.camera_model import CameraModel  # noqa: E402
from configs._runtime_store import load as rt_load, save as rt_save  # noqa: E402

# VLM (semantic add-on) subsystem removed: the rear collision warning app
# runs purely on depth + person detector.

SHARED_FRAME_PATH = os.environ.get("SEG_DEMO_SHARED_REAR", "")


class WarningPipeline:
    """Owns perception state + risk state, updated per processed frame."""

    def __init__(self, cfg) -> None:
        self.cfg = cfg
        cam_cfg = {"camera": cfg.get("camera", {})}
        self.cam_model = CameraModel.from_config(cam_cfg)
        self.ground = ExpectedGroundDepthFilter(
            self.cam_model,
            tolerance_ratio=cfg.get("ground_filter.tolerance_ratio", 0.20),
            enabled=cfg.get("ground_filter.enabled", True),
            extr_from=cfg.data)
        self.roi = DangerRegion(cfg.roi_points)
        self.corridor = PhysicalCorridor(
            self.cam_model, self.ground.extr,
            width_m=cfg.get("collision_corridor.width_m", 1.8),
            max_distance_m=cfg.get("collision_corridor.max_distance_m", 6.0),
            enabled=cfg.get("collision_corridor.enabled", False))

    # DEPRECATED adapter: ground filter built above
    @property
    def has_georef(self) -> bool:
        return self.ground.has_georef

    def process(self, depth_m: np.ndarray) -> dict:
        """depth_m -> filtered perception state dict.

        If cfg pipeline.scale < 1 the whole chain runs at the reduced
        resolution (percentile distances are resolution-independent);
        bboxes are scaled back to the ORIGINAL frame coords (scale_back)."""
        cfg = self.cfg
        scale = float(cfg.get("pipeline.scale", 1.0))
        orig_h, orig_w = depth_m.shape[:2]
        work = depth_m
        if 0.0 < scale < 1.0:
            h2 = max(1, int(round(orig_h * scale)))
            w2 = max(1, int(round(orig_w * scale)))
            work = cv2.resize(depth_m, (w2, h2),
                              interpolation=cv2.INTER_NEAREST)
        min_m = cfg.min_depth_m
        max_m = cfg.max_depth_m
        d0 = invalidate(work, min_m, max_m)
        valid = validity_mask(d0, min_m, max_m)
        temp_alpha = cfg.get("depth.temporal_filter.alpha", 0.25)
        tf = getattr(self, "_tfilter", None)
        if tf is None or tf.alpha != temp_alpha:
            tf = TemporalDepthFilter(alpha=temp_alpha)
            self._tfilter = tf
        d_smooth = tf.update(d0, valid)
        spatial = cfg.get("depth.spatial_filter.enabled", True)
        k = int(cfg.get("depth.spatial_filter.kernel", 3))
        d_smooth = spatial_smooth(d_smooth, kernel=k,
                                  mask=np.isfinite(d_smooth)) \
            if spatial else d_smooth

        pred = d_smooth
        gf = self.ground.apply(pred, np.isfinite(pred))
        cand_mask = (self.roi.mask(*pred.shape)
                     if self.roi is not None else np.ones(pred.shape, bool))
        if gf.has_georef:
            cand = gf.obstacle_cand & cand_mask
        else:
            cand = np.isfinite(pred) & cand_mask
        cands_ = extract_candidates(
            cand, pred,
            min_area_px=int(cfg.get("obstacle.min_area_px", 200) * scale * scale),
            percentile=float(cfg.get("distance.percentile", 10)),
            morphology=bool(cfg.get("obstacle.morphology", True)))
        inv = 1.0 / scale if 0.0 < scale < 1.0 else 1.0
        cands = []
        for c in cands_:
            c.bbox = tuple(int(round(v * inv)) for v in c.bbox)
            c.area_px = int(c.area_px * inv * inv)
            cands.append(c)
        nearest = nearest_candidate(cands)
        return {
            "depth": pred,
            "scale": scale,
            "valid": np.isfinite(d_smooth) & (d_smooth > min_m),
            "ground": gf.ground_mask, "obstacle_cand": cand,
            "candidates": cands, "nearest": nearest,
            "expected": gf.expected_m,
            "roi_mask": self.roi.mask(*pred.shape),
            "raw_valid": valid,
            "has_georef": gf.has_georef,
            "orig_hw": (orig_h, orig_w),
        }


def _display_is_xrdp(display: str) -> bool:
    """True when DISPLAY points at an xrdp virtual X server.

    The xrdp Xorg runs with an explicit ':N' and an xrdp config; the gdm
    console Xorg does not.  Used to warn when a window would open on a
    virtual screen nobody is watching ("can't open the window" bug)."""
    if not display or display.startswith("localhost"):
        return False
    num = display.split(":", 1)[-1].split(".", 1)[0]
    if not num.isdigit():
        return False
    try:
        r = subprocess.run(
            ["pgrep", "-f", f"Xorg :{num}( |$).*xrdp"],
            capture_output=True, timeout=3)
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _parse_cli(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser("warn_app")
    ap.add_argument("--config", default=None)
    ap.add_argument("--camera", default=None, help="rtsp:// or video: or usb:")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--max-frames", type=int, default=0)
    ap.add_argument("--out", default=None, help="CSV/telemetry prefix")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--fullscreen", action="store_true")
    ap.add_argument("--web", action="store_true",
                    help="serve the inference canvas as MJPEG over HTTP")
    ap.add_argument("--port", type=int, default=8080, help="web server port")
    ap.add_argument("--host", default="0.0.0.0", help="web bind address")
    ap.add_argument("--alarm-mode", choices=("none", "bell", "gpio", "serial"),
                    default=None, help="override physical alarm output mode")
    return ap.parse_args(argv)


def main(argv=None) -> None:
    args = _parse_cli(argv)
    cfg = load_config(args.config)
    cfg.data["system"]["debug"] = bool(cfg.get("system.debug", False)
                                       or args.debug)

    rtsp_url = os.environ.get("REAR_CAMERA_URL") or args.camera or cfg.rtsp_url
    if not rtsp_url:
        print("[warn] 没有相机源:请设置 configs/warning.yaml 的 camera.rtsp_url"
              " 或用 --camera 指定(rtsp:// 或 video: 或 usb:)",
              file=sys.stderr)
        raise SystemExit(2)

    # --- runtime knobs (web-controlled, persisted across restart) ---------
    # The web UI persists user toggles into configs/_runtime_overrides.yaml
    # (independent file so configs/warning.yaml is never rewritten —
    # PyYAML cannot preserve its comments/key order). Both MJPEGServer and
    # DangerRecorder read from the same `_runtime` dict, guarded by
    # `_rt_lock`; any web-side toggle calls back into `on_recording_changed`
    # which keeps the recorder's `enabled` in sync.
    _runtime_path = os.environ.get(
        "SEG_DEMO_RUNTIME_OVERRIDES",
        os.path.join("configs", "_runtime_overrides.yaml"))
    rt = rt_load(_runtime_path)
    _rt_lock = threading.Lock()
    _runtime = {
        "danger_m": float(rt.get("danger_m", cfg.get("warning.danger_distance_m", 1.5))),
        "warning_m": float(rt.get("warning_m", cfg.get("warning.warning_distance_m", 3.0))),
        "buzzer": bool(rt.get("buzzer", cfg.get("io.alarm_serial_buzzer", False))),
        "recording": bool(rt.get("recording", False)),
        "save_dir": str(rt.get("save_dir", cfg.get("events.dir", "logs"))),
        "depth_target_fps": float(cfg.get("pipeline.depth_fps", 10.0)),
        "person_target_fps": float(cfg.get("person.max_fps", 5.0)),
        "thermal_state": "normal",
        "degradation_reason": None,
    }

    def _persist_runtime(values: dict) -> None:
        """Write the given subset of runtime knobs to disk."""
        try:
            rt_save(_runtime_path, values)
        except Exception as exc:  # noqa: BLE001
            print(f"[warn:runtime] persist failed: {type(exc).__name__}:"
                  f" {exc}")

    def _on_recording_changed(new_value: bool | None) -> None:
        """Sync the DangerRecorder's enabled flag with the new toggle."""
        if new_value is None:
            return
        with _rt_lock:
            _runtime["recording"] = new_value
        if recorder is not None:
            recorder.enabled = new_value
            recorder.base_dir = _runtime["save_dir"]

    # --- web monitor (optional, --web) --------------------------------------
    web = None
    if args.web:
        try:
            web = MJPEGServer(args.host, args.port, runtime=_runtime,
                              cfg_lock=_rt_lock,
                              persist=lambda vals: (_persist_runtime(vals),
                                                    _on_recording_changed(
                                                        vals.get("recording"))))
        except OSError as exc:
            print(f"[warn:web] cannot bind {args.host}:{args.port} ({exc}) — "
                  "another instance is probably already serving this port; "
                  "stop it or pass a different --port / WEB_PORT")
            raise SystemExit(2)
        web.start()

    # --- engine -------------------------------------------------------------
    print("[warn] building depth engine ...", flush=True)
    scale = float(cfg.get("pipeline.scale", 1.0))
    cam_h = int(cfg.get("camera.height", 1296))
    cam_w = int(cfg.get("camera.width", 2304))
    out_hw = None
    if 0.0 < scale < 1.0:
        out_hw = (max(1, int(round(cam_h * scale))),
                  max(1, int(round(cam_w * scale))))
        print(f"[warn] perception scale={scale} -> out {out_hw}")
    engine = make_depth_engine(cfg, out_hw=out_hw)
    print(f"[warn] backend={engine.name}")

    pip = WarningPipeline(cfg)
    person_detector = None
    person_error = ""
    if cfg.get("person.enabled", True):
        try:
            person_detector = UltralyticsPersonDetector(
                cfg.get("person.engine"),
                cfg.get("person.confidence", 0.45),
                cfg.get("person.iou", 0.5),
                cfg.get("person.input_size", 640))
            print(f"[warn] person backend={person_detector.name}")
        except Exception as exc:
            person_error = str(exc)
            print(f"[warn:person] UNAVAILABLE: {person_error}")
    person_risk = PersonRiskAssessor(
        cfg.get("person.warning_distance_m", 4.0),
        cfg.get("person.danger_distance_m", 2.0),
        cfg.roi_points)
    risk = RiskEngine(
        warning_distance_m=cfg.get("warning.warning_distance_m", 3.0),
        danger_distance_m=cfg.get("warning.danger_distance_m", 1.5),
        warning_ttc_s=cfg.get("warning.warning_ttc_s", 3.0),
        danger_ttc_s=cfg.get("warning.danger_ttc_s", 1.5),
        exit_margin_m=cfg.get("warning.hysteresis.exit_margin_m", 0.3),
        exit_ttc_s=cfg.get("warning.hysteresis.exit_ttc_s", 0.5),
        max_consecutive_bad_frames=cfg.get(
            "watchdog.max_consecutive_bad_frames", 3))
    ema = DistanceEMA(alpha=cfg.get("temporal.ema_alpha", 0.25))
    voter = PresenceVoter(cfg.get("temporal.history_frames", 5),
                          cfg.get("temporal.trigger_frames", 3))
    velttc = VelocityTTC(window_frames=cfg.get("velocity.window_frames", 10),
                         min_closing_speed_mps=cfg.get(
                             "ttc.min_closing_speed_mps", 0.10),
                         enabled=cfg.get("ttc.enabled", True))
    alarm = AlarmController(mode=args.alarm_mode or cfg.get("io.alarm_mode", "bell"),
                            gpio_cmd=cfg.get("io.alarm_gpio_cmd", ""),
                            serial_port=cfg.get("io.alarm_serial_port", "/dev/serial/by-id/usb-1a86_5523-if00-port0"),
                            serial_baud=int(cfg.get("io.alarm_serial_baud", 9600)),
                            serial_buzzer=bool(cfg.get("io.alarm_serial_buzzer", False)))
    logger = TelemetryLogger(base_dir=cfg.get("logger.dir", "logs"),
                             enabled=cfg.get("logger.enabled", True))
    cal = CalibrationUI(cfg)

    # DANGER/WARNING event recorder (file-system writes). Default OFF —
    # the user has to flip the "录制事件" checkbox in the web UI to start
    # writing danger_*.jpg to disk. base_dir follows the runtime override
    # (defaults to configs/warning.yaml: events.dir).
    recorder = DangerRecorder(
        base_dir=_runtime["save_dir"],
        enabled=_runtime["recording"],
        min_interval_s=cfg.get("events.min_interval_s", 2.0))
    print(f"[warn] recorder enabled={recorder.enabled} dir={recorder.base_dir}"
          f" (toggle via web UI)")

    # --- shared state -------------------------------------------------------
    lock_f, lock_d, lock_p = threading.Lock(), threading.Lock(), threading.Lock()
    state = {
        "frame": None, "fidx": 0, "ts": 0.0,           # capture
        "depth": None,                                  # depth, frame id, valid, result time
        "people": None,                                 # people, frame id, times, valid, error
        "running": True, "paused": False,
        "last_cam_ts": time.time(),
        "fps_cap": 0.0,
        "depth_inference_fps": 0.0,
        "person_inference_fps": 0.0,
    }
    vpip = {"level": SAFE, "d_filt": float("nan"), "ttc": None,
            "vel": None, "obs": None, "fps": 0.0}

    # --- capture ------------------------------------------------------------
    def _open_live(url):
        try:
            cap, lab = open_rtsp(url)
            return cap if (cap and cap.isOpened()) else None, f"rtsp:{url}", 0.0
        except Exception as exc:
            print(f"[warn:camera] open_rtsp error: {exc}")
            return None, f"rtsp:{url}", 0.0

    cap_obj, label, _pace = None, "n/a", 0.0
    open_kwargs = dict()
    if rtsp_url.startswith("rtsp"):
        cap_obj, label, _pace = _open_live(rtsp_url)
    else:
        cap_obj, label, _pace = open_source(rtsp_url)

    if cap_obj is None or not cap_obj.isOpened():
        print(f"[warn] cannot open source {rtsp_url}")

    def capture_loop():
        nonlocal cap_obj, label
        last = time.time()
        while state["running"]:
            if state["paused"]:
                time.sleep(0.01)
                continue
            if cap_obj is None or not cap_obj.isOpened():
                time.sleep(1.0)
                if rtsp_url.startswith("rtsp"):
                    cap_obj, label, _pace2 = _open_live(rtsp_url)
                continue
            ok, f = cap_obj.read()
            if not ok:
                if rtsp_url.startswith("rtsp"):
                    # camera gone: reconnect (keep session alive, watchdog flags)
                    time.sleep(1.0)
                    try:
                        cap_obj.release()
                    except Exception:
                        pass
                    cap_obj, label, _pace2 = _open_live(rtsp_url)
                    continue
                # video file exhausted: end the session
                state["running"] = False
                break
            now = time.time()
            with lock_f:
                state["frame"] = f
                state["fidx"] += 1
                state["ts"] = now
                state["last_cam_ts"] = now
                state["fps_cap"] = 1.0 / max(1e-6, now - last)
            last = now
            if _pace > 0:
                time.sleep(_pace)

    def depth_loop():
        seen = -1
        next_due = 0.0
        last_done = 0.0
        while state["running"]:
            with lock_f:
                f, fidx = state["frame"], state["fidx"]
            now_mono = time.monotonic()
            with _rt_lock:
                target_fps = max(
                    1.0, float(_runtime["depth_target_fps"]))
            if f is None or fidx == seen or now_mono < next_due:
                time.sleep(0.002)
                continue
            seen = fidx
            next_due = now_mono + (1.0 / target_fps)
            try:
                d = engine.infer(f)
                ok_d = bool(np.isfinite(d).any())
                done_mono = time.monotonic()
                infer_fps = (1.0 / max(1e-6, done_mono - last_done)
                             if last_done else 0.0)
                last_done = done_mono
                with lock_d:
                    state["depth"] = (d, fidx, ok_d, time.time())
                with lock_f:
                    state["depth_ok"] = ok_d
                    state["depth_ms"] = engine.last_ms
                    previous = float(state.get("depth_inference_fps", 0.0))
                    state["depth_inference_fps"] = (
                        infer_fps if previous <= 0.0
                        else 0.9 * previous + 0.1 * infer_fps)
            except Exception as exc:
                print(f"[warn:depth] {type(exc).__name__}: {exc}")
                with lock_d:
                    state["depth"] = None

    def person_loop():
        seen = -1
        consecutive_failures = 0
        next_due = 0.0
        last_done = 0.0
        while state["running"] and person_detector is not None:
            with lock_f:
                f, fidx, captured_at = (
                    state["frame"], state["fidx"], state["ts"])
            now_mono = time.monotonic()
            with _rt_lock:
                target_fps = max(
                    1.0, float(_runtime["person_target_fps"]))
            if f is None or fidx == seen or now_mono < next_due:
                time.sleep(0.002)
                continue
            seen = fidx
            next_due = now_mono + (1.0 / target_fps)
            try:
                people = person_detector.infer(f)
                consecutive_failures = 0
                done_mono = time.monotonic()
                infer_fps = (1.0 / max(1e-6, done_mono - last_done)
                             if last_done else 0.0)
                last_done = done_mono
                with lock_p:
                    state["people"] = (
                        people, fidx, captured_at, time.time(), True, "")
                with lock_f:
                    previous = float(state.get("person_inference_fps", 0.0))
                    state["person_inference_fps"] = (
                        infer_fps if previous <= 0.0
                        else 0.9 * previous + 0.1 * infer_fps)
            except Exception as exc:
                consecutive_failures += 1
                print(f"[warn:person] {type(exc).__name__}: {exc}")
                with lock_p:
                    state["people"] = (
                        [], fidx, captured_at, time.time(), False, str(exc))
                # A failed first TensorRT initialization can otherwise retry
                # for every capture frame and repeatedly deserialize the
                # engine. Keep the failure visible while backing off.
                time.sleep(min(5.0, 0.25 * (2 ** min(
                    consecutive_failures - 1, 5))))

    # --- start threads ------------------------------------------------------
    tc = threading.Thread(target=capture_loop, daemon=True)
    td = threading.Thread(target=depth_loop, daemon=True)
    tp = threading.Thread(target=person_loop, daemon=True)
    tc.start()
    td.start()
    tp.start()

    # VLM (semantic add-on) subsystem removed — the warning pipeline runs
    # on depth + person detector only. `vlm` and `vlm_tracker` were bound
    # here previously; callers below have been updated to skip them.

    # startup warmup: block until the first depth result is ready (model
    # load + first inference), so gate/headless runs measure real frames
    warmup_deadline = time.time() + 90.0
    while state["depth"] is None and time.time() < warmup_deadline \
            and state["running"]:
        if state["frame"] is None:
            time.sleep(0.05)
        else:
            time.sleep(0.05)
    if state["depth"] is None:
        print("[warn] WARNING: depth engine produced no result within 90s")
    else:
        print(f"[warn] warmup ok (first depth {state['depth'][0].shape})")
        # prebuild the expected-ground map so the first real frame isn't slow
        # (do NOT run pip.process on a dummy zero map here: invalidate() turns
        #  it all-NaN and seeds the temporal filter with NaN state that then
        #  locks the smoothed depth to NaN on every later frame)
        pip.ground.expected_ground_depth(*state["depth"][0].shape[:2])

    renderer = Renderer()
    recording = False
    video_writer = None
    win = "Rear Collision Warning"
    if not args.headless:
        dpy = os.environ.get("DISPLAY", "<unset>")
        if _display_is_xrdp(dpy):
            print(f"[warn] window display={dpy} (xrdp VIRTUAL session — "
                  "visible only while an RDP client is connected; prefer "
                  "./scripts/run_warning.sh which auto-picks a visible display)")
        else:
            print(f"[warn] window display={dpy}")
        try:
            # NOTE: on this OpenCV GTK2 build a window created with the
            # WINDOW_FULLSCREEN *flag* is never mapped on the xrdp display
            # ("window doesn't open" bug).  Create it normal, then switch
            # to fullscreen via setWindowProperty — the reliable route.
            cv2.namedWindow(win, cv2.WINDOW_NORMAL)
            # This GTK2 build keeps the window at its 320x240 default unless
            # explicitly sized (fullscreen masks this in RDP mode).  Under
            # xpra (SSH forwarding) there is no --fullscreen, so size the
            # window to the renderer canvas explicitly.
            try:
                cv2.resizeWindow(win, CANVAS_W, CANVAS_H)
            except cv2.error:
                pass  # some backends reject resize before first imshow
            if args.fullscreen:
                cv2.setWindowProperty(win, cv2.WND_PROP_FULLSCREEN,
                                      cv2.WINDOW_FULLSCREEN)
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] WINDOW ERROR: cannot open window on DISPLAY={dpy}: "
                  f"{exc}")
            print("[warn] fix: run via ./scripts/run_warning.sh (auto-picks a "
                  "visible display), or set DISPLAY=:0 and re-run; "
                  "gate mode: --headless --max-frames N")
            raise SystemExit(2) from exc
        cal.open_win() if cfg.get("debug", False) else None

    # --- helpers -----------------------------------------------------------
    def _on_sigint(_s, _f):
        state["running"] = False
    signal.signal(signal.SIGINT, _on_sigint)
    signal.signal(signal.SIGTERM, _on_sigint)  # clean shutdown on SIGTERM

    def _click_to_window(ev, x, y, _f, _p):
        # forward to calibration ROI drag with normalized coords
        try:
            r = cv2.getWindowImageRect(win)
        except Exception:
            r = (0, 0, CANVAS_W, CANVAS_H)
        cam_w = state["frame"].shape[1] if state["frame"] is not None else 1
        cam_h = state["frame"].shape[0] if state["frame"] is not None else 1
        nx = (x - r[0]) / max(1, r[2]) if r[2] > 0 else 0.0
        ny = (y - r[1]) / max(1, r[3]) if r[3] > 0 else 0.0
        # normalized relative to the video block, not the whole canvas
        # (calibration ROI coordinates are video-normalized 0..1)
        vx, vy, vw, vh = renderer._video_area()
        nx = (x - vx - r[0]) / max(1, r[2] if r[2] > 0 else vw)
        ny = (y - vy - r[1]) / max(1, r[3] if r[3] > 0 else vh)
        nx = min(max(nx, 0.0), 1.0)
        ny = min(max(ny, 0.0), 1.0)
        cal.mouse(event=ev, x=nx, y=ny, flags=_f, draw_scale=None)

    if not args.headless:
        cv2.setMouseCallback(win, _click_to_window)

    fps_ema = 0.0
    frames_done = 0
    _last_forced = 0.0
    print(f"[warn] entering loop: running={state['running']} frame={'None' if state['frame'] is None else state['frame'].shape} depth={'None' if state['depth'] is None else state['depth'][0].shape}")
    try:
        res_last = None
        last_depth_fidx = -1
        # Person inference is asynchronous. Keep the startup interval
        # distinct from a detector that was ready and then went stale.
        person_pipeline_ready = False
        person_last_success_at = None
        p = None
        nearest = None
        dist_raw = d_filt = None
        voted = False
        vel = ttc = None
        prev_final_level = None
        sem_open = None              # V0.3 transition->visible measurement
        _last_track_ids = set()      # V0.3 semantic cache bookkeeping
        _prev_decision_key = None    # V0.4 decision-dataset dedup
        max_loop_fps = max(10.0, float(cfg.get("pipeline.max_fps", 15.0)))
        while state["running"]:
            if args.max_frames and frames_done >= args.max_frames:
                break
            if args.headless:
                # keep UI-less: still render canvas for screenshots
                pass
            t0 = time.time()
            # --- 1. pull latest depth (drop when no new) ---
            with lock_d:
                dd = state["depth"]
            with lock_p:
                pp = state["people"]
            with lock_f:
                frame = state["frame"]
                cam_ts = state.get("last_cam_ts", t0)
                depth_ok = state.get("depth_ok", False)
                depth_ms = state.get("depth_ms", 0.0)
                fidx = state["fidx"]
                capture_fps = state.get("fps_cap", 0.0)
                depth_inference_fps = state.get("depth_inference_fps", 0.0)
                person_inference_fps = state.get("person_inference_fps", 0.0)
            if frame is None:
                time.sleep(0.01)
                continue
            cam_ok = (time.time() - state["ts"]) < \
                cfg.get("watchdog.camera_lost_timeout_s", 5.0)
            now = time.time()
            max_age = float(cfg.get("person.max_result_age_s", 0.5))
            new_depth = dd is not None and dd[1] > last_depth_fidx
            depth_fresh = dd is not None and now - dd[3] <= max_age
            depth_valid = p_ok = bool(depth_fresh and dd[2]) if dd else False
            if new_depth:
                last_depth_fidx = dd[1]
                p = pip.process(dd[0])
                nearest = p["nearest"]
                p_ok = True
                # Temporal state advances once per unique depth frame.
                dist_raw = nearest.distance_m if nearest is not None else None
                d_filt = ema.update(dist_raw)
                present = nearest is not None
                voted = voter.update(present)
                vel, ttc = None, None
                if voted and d_filt is not None:
                    vel, ttc = velttc.update(d_filt, now)
                else:
                    velttc.reset()

            # --- 3. risk (distance only counts when the obstacle is voted) ---
            with _rt_lock:
                danger_m = _runtime["danger_m"]
                warning_m = _runtime["warning_m"]
                buzzer = _runtime["buzzer"]
                depth_target_fps = _runtime["depth_target_fps"]
                person_target_fps = _runtime["person_target_fps"]
                thermal_state = _runtime["thermal_state"]
            risk.danger_d = danger_m
            risk.warn_d = warning_m
            person_risk.danger_distance_m = danger_m
            person_risk.warning_distance_m = warning_m
            if alarm.serial_buzzer != buzzer:
                alarm.set_buzzer(buzzer)
            d_risk = d_filt if voted else None
            # TTC remains visible for engineering review but does not trigger
            # first-version alerts.
            out = risk.update(d_risk, vel, None,
                              camera_stale_s=now - state["ts"],
                              camera_timeout_s=cfg.get(
                                  "watchdog.camera_lost_timeout_s", 5.0),
                              depth_valid=depth_valid,
                              obstacle_present=voted)
            person_fresh = bool(
                pp is not None and pp[4]
                and now - pp[3] <= max_age
                and now - pp[2] <= max_age)
            if pp is not None and pp[4]:
                person_pipeline_ready = True
                person_last_success_at = pp[3]
            people = pp[0] if person_fresh else []
            max_frame_skew = int(cfg.get("person.max_frame_skew", 4))
            person_depth_synced = bool(
                person_fresh and depth_fresh
                and abs(int(pp[1]) - int(dd[1])) <= max_frame_skew)
            pr = person_risk.assess(
                people, dd[0] if person_depth_synced else None,
                frame.shape[:2])
            levels = {SAFE: 0, WARNING: 1, DANGER: 2, SYSTEM_ERROR: 3}
            final_level = (pr.level if levels[pr.level] >= levels[out.level]
                           else out.level)
            person_required = bool(cfg.get("person.required", True))
            person_stale_grace_s = float(
                cfg.get("person.system_error_grace_s", 1.5))
            person_stale_too_long = (
                person_last_success_at is not None
                and now - person_last_success_at > person_stale_grace_s)
            if (person_required and person_pipeline_ready
                    and not person_fresh and person_stale_too_long):
                final_level = SYSTEM_ERROR
            if pr.primary is not None:
                display_distance = (
                    pr.primary.distance_m if pr.primary.distance_valid
                    else float("nan"))
                display_ttc = display_vel = None
            else:
                display_distance = (
                    d_filt if d_filt is not None else float("nan"))
                display_ttc, display_vel = ttc, vel
            vpip.update(level=final_level, d_filt=display_distance,
                        ttc=display_ttc, vel=display_vel,
                        obs=pr.primary if pr.primary is not None
                        else nearest if voted else None)

            prev_final_level = final_level

            # --- 4. render ---
            fps_curr = 1.0 / max(1e-6, t0 - res_last) if res_last else 0.0
            fps_ema = 0.9 * fps_ema + 0.1 * fps_curr if fps_ema else fps_curr
            res_last = t0
            dbg = None
            if cfg.get("debug", False):
                dbg = {
                    "backend": engine.name, "dtype": cfg.get("model.dtype"),
                    "engine ms": depth_ms, "infer fps": engine and 1000.0 / max(engine.last_ms, 1e-3) or 0,
                    "valid depth": int(np.isfinite(dd[0]).sum()) if dd else 0,
                    "d_raw": dist_raw if dist_raw is not None else float("nan"),
                    "d_filt": d_filt if d_filt is not None else float("nan"),
                    "vel m/s": vel if vel is not None else float("nan"),
                    "ttc s": ttc if ttc is not None else float("nan"),
                    "person backend": (person_detector.name
                                       if person_detector else person_error),
                    "person reason": pr.reason,
                    "person depth sync": person_depth_synced,
                    "person error": (pp[5] if pp is not None and not pp[4]
                                     else ""),
                    "people": len(pr.people),
                    "n_cand": len(p["candidates"]) if p else 0,
                    "geo": pip.has_georef,
                }
            canvas = renderer.render(
                frame,
                final_level,
                display_distance, display_ttc, display_vel,
                norm_roi=cal.roi_points if cfg.get("debug", False) and
                                         getattr(args, "debug", False)
                else cfg.roi_points,
                obstacle=vpip["obs"],
                fps=fps_ema, cam_ok=cam_ok,
                ai_ok=bool(depth_valid and (not person_required or person_fresh)),
                debug=dbg,
                depth_map=p["depth"] if p else None,
                ground_mask=p["ground"] if p else None,
                obstacle_mask=p["obstacle_cand"] if p else None,
                roi_mask=p["roi_mask"] if p else None,
                expected=p["expected"] if p else None,
                danger_m=danger_m,
                warn_m=warning_m,
                person_danger_m=danger_m,
                people=pr.people,
                frame_id=fidx)
            if SHARED_FRAME_PATH:
                try:
                    ok_shared, shared_buf = cv2.imencode(
                        ".jpg", canvas, [cv2.IMWRITE_JPEG_QUALITY, 78])
                    if ok_shared:
                        tmp_path = SHARED_FRAME_PATH + ".tmp"
                        with open(tmp_path, "wb") as fh:
                            fh.write(shared_buf.tobytes())
                        os.replace(tmp_path, SHARED_FRAME_PATH)
                except OSError:
                    pass
            if web:
                web.push(canvas, {"level": final_level,
                    "fps": round(fps_ema, 1),
                    "distance": (pr.primary.distance_m
                                 if pr.primary else out.distance_m),
                    "ttc": display_ttc, "vel": display_vel, "cam_ok": cam_ok,
                    "ai_ok": bool(depth_valid and
                                  (not person_required or person_fresh)),
                    "engine": engine.name,
                    "engine_ms": round(depth_ms, 1), "fidx": fidx,
                    "snap_ts": round(now, 3),
                    "capture_fps": round(float(capture_fps), 2),
                    "depth_inference_fps": round(float(depth_inference_fps), 2),
                    "person_inference_fps": round(float(person_inference_fps), 2),
                    "depth_target_fps": round(float(depth_target_fps), 2),
                    "person_target_fps": round(float(person_target_fps), 2),
                    "thermal_state": thermal_state,
                    "depth_result_age_s": (
                        round(max(0.0, now - dd[3]), 3) if dd else None),
                    "person_result_age_s": (
                        round(max(0.0, now - pp[3]), 3) if pp else None),
                    "threat_track": (pr.primary.track_id
                                     if pr.primary is not None else None),
                    "people": len(pr.people), "person_reason": pr.reason,
                    "danger_m": round(risk.danger_d, 2),
                    "warning_m": round(risk.warn_d, 2),
                    "buzzer": alarm.serial_buzzer})
            if not args.headless:
                cv2.imshow(win, canvas)
                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), 27):
                    break
                elif key == ord("d"):
                    renderer.debug_view = renderer.debug_view % 6 + 1
                elif key == ord("f"):
                    renderer.fullscreen = not renderer.fullscreen
                    cv2.setWindowProperty(
                        win, cv2.WND_PROP_FULLSCREEN,
                        cv2.WINDOW_FULLSCREEN if renderer.fullscreen
                        else cv2.WINDOW_NORMAL)
                elif key == ord(" "):
                    state["paused"] = not state["paused"]
                elif key == ord("r"):
                    print("[warn] R recording toggle")
                elif key == ord("c"):
                    cfg.data["system"]["debug"] = True
                    cal.st.active = not cal.st.active
                    if cal.st.active:
                        cal.open_win()
                    else:
                        cal.close_win()
                elif key == ord("s"):
                    cal.save(cfg.get("config") or "configs/warning.yaml")
                if cal.st.active:
                    cal.read_trackbars()
                    cal.apply_to_config()
                    pip.roi = DangerRegion(cfg.roi_points)
            else:
                time.sleep(0.001)

            alarm.set_level(vpip["level"])
            recorder.on_frame(canvas, vpip["level"])

            # telemetry
            if new_depth:
                logger.log(fidx, vpip["level"],
                           dist_raw if dist_raw is not None else float("nan"),
                           d_filt if d_filt is not None else float("nan"),
                           vel, ttc,
                           len(p["candidates"]) if p else 0, depth_valid,
                           fps_ema, backend=engine.name, engine_ms=depth_ms)
            if args.headless and (fidx % 30 == 0 or
                                  vpip["level"] != state.get("_last_level")):
                state["_last_level"] = vpip["level"]
                print(f"[f{fidx}] level={vpip['level']:14s} d_raw="
                      f"{dist_raw if dist_raw is not None else float('nan')} "
                      f"d_filt={vpip['d_filt']} ttc={vpip['ttc']} "
                      f"vel={vpip['vel']} fps={fps_ema:.1f}")
            if new_depth:
                frames_done += 1
            # Preserve rear priority while leaving thermal headroom for the
            # front tracker and cabin detector.
            remaining = (1.0 / max_loop_fps) - (time.time() - t0)
            if remaining > 0:
                time.sleep(remaining)
            if args.max_frames and frames_done >= args.max_frames:
                break
    except KeyboardInterrupt:
        pass
    finally:
        import traceback
        _exc = sys.exc_info()
        if _exc[0] is not None:
            print(f"[warn] LOOP EXCEPTION: {_exc[0].__name__}: {_exc[1]}")
            traceback.print_exc()
        state["running"] = False
        alarm.stop()
        logger.close()
        if not args.headless:
            cal.close_win()
            cv2.destroyAllWindows()
        tc.join(timeout=2.0)
        td.join(timeout=2.0)
        tp.join(timeout=2.0)
        print(f"[warn] session done state={risk.state} frames={frames_done} "
              f"level={vpip['level']}")
        if args.out:
            print(f"[warn] telemetry -> logs/ (CSV)")
        # stop the web monitor before bypassing atexit (os._exit kills
        # daemon threads anyway, but close the listening socket cleanly)
        if web:
            web.stop()
        # same trick as camera_demo: bypass cv2/torch atexit teardown races
        # that hang or double-free on Jetson
        os._exit(0)


if __name__ == "__main__":
    main()
