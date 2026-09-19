"""Lightweight MJPEG web monitor for the warning canvas.

Serves the rendered inference canvas over HTTP so a LAN browser can watch
the live demo in headless mode (no local X display needed). Zero extra
dependencies: stdlib `http.server` + `cv2` (already in the venv).

Routes:
  GET /         HTML page (live <img src="/stream"> + telemetry readout)
  GET /stream   multipart/x-mixed-replace MJPEG of the latest canvas
  GET /frame    single latest JPEG frame (one-shot, convenience/testing)
  GET /state    JSON telemetry dict (level/fps/distance/ttc/.../fidx)
  GET /health   {"ok": true, "frames": <pushed-frame count>}

Latency design:
  - `MJPEGServer.push(canvas_bgr, meta)` (main inference thread) only COPIES
    the canvas and stores it — `cv2.imencode` is NOT done on the perception
    path anymore (it used to cost ~16ms/frame at 1280x720 q80 even with zero
    viewers).
  - a dedicated encoder thread converts the NEWEST raw canvas to a downscaled
    960x540 q70 JPEG, but ONLY while at least one /stream client is connected.
    HTTP handler threads just read the latest encoded bytes under a lock.
"""
from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

import cv2
import numpy as np

_BOUNDARY = "frame"
JPEG_QUALITY = 80         # used by /frame only as a fallback baseline
WEB_MAX_W = 960           # web stream width (local window stays 1280x720)
WEB_JPEG_QUALITY = 70     # web stream JPEG quality (bytes ~2.7x smaller)
MAX_STREAM_FPS = 30       # cap /stream send rate (inference loop is lower)


class _Handler(BaseHTTPRequestHandler):
    # quiet stderr access log — one line per request would flood at 20 fps
    def log_message(self, *args):  # noqa: BLE001
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")  # LAN + tailscale
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: BLE001 - dispatch
        srv: "MJPEGServer" = self.server  # type: ignore[assignment]
        path = self.path.split("?", 1)[0]
        if path == "/":
            self._send(200, _HTML_PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/health":
            with srv._lock:
                body = json.dumps({"ok": True, "frames": srv._frames}).encode()
            self._send(200, body, "application/json")
        elif path == "/state":
            with srv._lock:
                meta = dict(srv._meta) if srv._meta else {}
                # V0.4: the frame currently sitting in the stream buffer —
                # a browser that just decoded /stream output is roughly at
                # this fidx; lag vs the live meta fidx is visible.
                meta["stream_fidx"] = srv._enc_frame_fidx
                meta["stream_age_ms"] = round(
                    (time.time() - srv._enc_ts) * 1000.0, 1) \
                    if srv._enc_ts > 0.0 else None
            self._send(200, json.dumps(meta, default=str).encode("utf-8"),
                       "application/json")
        elif path == "/stream":
            self._stream(srv)
        elif path == "/frame":
            self._frame(srv)
        elif path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")  # quiet the browser 404
        else:
            self._send(404, b"not found\n", "text/plain")

    def do_POST(self):  # noqa: BLE001 - dispatch
        srv: "MJPEGServer" = self.server  # type: ignore[assignment]
        path = self.path.split("?", 1)[0]
        if path != "/api/config":
            self._send(404, b"not found\n", "text/plain")
            return
        try:
            n = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(n) if n > 0 else b"{}"
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:  # noqa: BLE001 - bad JSON -> no-op
            data = {}
        if not isinstance(data, dict):
            data = {}
        out = srv.apply_config(data)
        code = 200 if out.get("ok", True) else 422
        self._send(code, json.dumps(out).encode("utf-8"), "application/json")

    def _stream(self, srv: "MJPEGServer") -> None:
        srv._clients_inc()
        try:
            self.send_response(200)
            self.send_header("Age", "0")
            self.send_header("Cache-Control", "no-cache, private")
            self.send_header("Pragma", "no-cache")
            self.send_header("Content-Type",
                             f"multipart/x-mixed-replace; boundary={_BOUNDARY}")
            self.end_headers()
            delay = 1.0 / MAX_STREAM_FPS
            head = (b"--" + _BOUNDARY.encode() + b"\r\n"
                    b"Content-Type: image/jpeg\r\n")
            while True:
                with srv._lock:
                    jpg = srv._jpg
                if jpg is None:
                    jpg = _placeholder_jpg()  # warming up — show something
                try:
                    self.wfile.write(head
                                     + b"Content-Length: " +
                                     str(len(jpg)).encode()
                                     + b"\r\n\r\n" + jpg + b"\r\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return  # client closed the tab
                time.sleep(delay)
        finally:
            srv._clients_dec()

    def _frame(self, srv: "MJPEGServer") -> None:
        """One-shot latest JPEG (encodes on demand if needed)."""
        with srv._lock:
            raw = srv._raw
        if raw is None:
            self._send(200, _placeholder_jpg(), "image/jpeg")
            return
        try:
            img = _web_sized(raw)
            ok, buf = cv2.imencode(
                ".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, WEB_JPEG_QUALITY])
            body = buf.tobytes() if ok else _placeholder_jpg()
        except Exception:  # noqa: BLE001
            body = _placeholder_jpg()
        self._send(200, body, "image/jpeg")


def _web_sized(raw: np.ndarray) -> np.ndarray:
    """Downscale to WEB_MAX_W (aspect kept) for the web stream only."""
    h, w = raw.shape[:2]
    if w <= WEB_MAX_W:
        return raw
    s = WEB_MAX_W / float(w)
    return cv2.resize(raw, (WEB_MAX_W, int(round(h * s))),
                      interpolation=cv2.INTER_AREA)


def _placeholder_jpg() -> bytes:
    """Tiny JPEG shown before the first real frame is pushed (cached)."""
    if _placeholder_jpg._cache is None:  # type: ignore[attr-defined]
        img = np.zeros((60, 320, 3), dtype=np.uint8)
        cv2.putText(img, "seg_demo: warming up ...", (8, 36),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1,
                    cv2.LINE_AA)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 70])
        _placeholder_jpg._cache = buf.tobytes() if ok else b""  # type: ignore[attr-defined]
    return _placeholder_jpg._cache  # type: ignore[attr-defined]


_placeholder_jpg._cache = None  # type: ignore[attr-defined]


_HTML_PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>seg_demo monitor</title>
<style>
  body{margin:0;background:#0c0f14;color:#e6e9ef;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
  img{display:block;width:100%;max-width:1280px;margin:0 auto;border-bottom:1px solid #222}
  #bar{display:flex;flex-wrap:wrap;gap:.4rem 1.2rem;padding:.5rem .8rem;font-size:13px}
  #ctl{display:flex;flex-wrap:wrap;gap:1rem;align-items:center;padding:.5rem .8rem;font-size:13px;border-bottom:1px solid #222}
  #ctl label{display:flex;align-items:center;gap:.5rem;cursor:pointer}
  #ctl input[type=range]{width:180px}
  #dangerVal{color:#60a5fa;min-width:3.2em}
  .k{color:#8b93a7} .ok{color:#34d399} .bad{color:#f87171} a{color:#60a5fa}
</style></head><body>
<img src="/stream" alt="live inference canvas">
<div id="ctl">
  <label>报警距离 <input type="range" id="danger" min="0.2" max="5.0" step="0.1" value="1.5"> <span id="dangerVal">-</span></label>
  <label><input type="checkbox" id="buzzer"> 蜂鸣器</label>
  <label><input type="checkbox" id="recording"> 录制事件 <span id="recState" class="k">off</span></label>
</div>
<div id="bar"><span class="k">connecting ...</span></div>
<script>
const LVL={SAFE:"ok",WARNING:"ok",DANGER:"bad","SYSTEM ERROR":"bad"};
const dangerEl=document.getElementById('danger');
const buzzerEl=document.getElementById('buzzer');
const recordingEl=document.getElementById('recording');
async function postConfig(d){
  try{ await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)}); }catch(e){}
}
let dragTimer=null;
dangerEl.addEventListener('input',function(){
  document.getElementById('dangerVal').textContent=dangerEl.value+'m';
  clearTimeout(dragTimer);
  dragTimer=setTimeout(()=>postConfig({danger_m:parseFloat(dangerEl.value)}),250);
});
buzzerEl.addEventListener('change',function(){ postConfig({buzzer:buzzerEl.checked}); });
recordingEl.addEventListener('change',function(){ postConfig({recording:recordingEl.checked}); });
async function tick(){
  try{
    const r=await fetch("/state",{cache:"no-store"});
    if(!r.ok)throw 0;
    const d=await r.json();
    if(document.activeElement!==dangerEl && d.danger_m!==undefined){
      dangerEl.value=d.danger_m; document.getElementById('dangerVal').textContent=d.danger_m+'m';
    }
    if(document.activeElement!==buzzerEl && d.buzzer!==undefined){
      buzzerEl.checked=!!d.buzzer;
    }
    if(document.activeElement!==recordingEl && d.recording!==undefined){
      recordingEl.checked=!!d.recording;
      document.getElementById('recState').textContent = d.recording?'on':'off';
    }
    const f=v=>(v===null||v===undefined)?'-':(typeof v==='number'?Math.round(v*100)/100:v);
    document.getElementById('bar').innerHTML=
      '<span><span class="k">level</span> <span class="'+(LVL[d.level]||'')+'">'+(d.level||'-')+'</span></span>'+
      '<span><span class="k">fps</span> '+f(d.fps)+'</span>'+
      '<span><span class="k">dist</span> '+f(d.distance)+'m</span>'+
      '<span><span class="k">ttc</span> '+f(d.ttc)+'s</span>'+
      '<span><span class="k">vel</span> '+f(d.vel)+'</span>'+
      '<span><span class="k">cam</span> '+(d.cam_ok?'<span class="ok">ok</span>':'<span class="bad">lost</span>')+'</span>'+
      '<span><span class="k">ai</span> '+(d.ai_ok?'<span class="ok">ok</span>':'<span class="bad">bad</span>')+'</span>'+
      '<span><span class="k">engine</span> '+f(d.engine)+'</span>'+
      '<span><span class="k">infer</span> '+f(d.engine_ms)+'ms</span>'+
      '<span><span class="k">fidx</span> '+f(d.fidx)+'</span>'+
      '<span><span class="k">stream</span> '+f(d.stream_fidx)+' <span class="'+( (d.stream_fidx!==undefined && d.fidx!==undefined && (d.fidx-d.stream_fidx)>5) ? 'bad':'ok' )+'">'+( (d.stream_fidx!==undefined && d.fidx!==undefined && (d.fidx-d.stream_fidx)>5) ? 'STATE LAG ~'+(d.fidx-d.stream_fidx)+'f':'OK' )+'</span></span>'+
      '<span><span class="k">threat</span> '+(d.threat_track||'-')+'</span>';
  }catch(e){document.getElementById('bar').innerHTML='<span class="bad">state fetch failed</span>';}
}
tick(); setInterval(tick,1000);
</script>
</body></html>
"""


class MJPEGServer(ThreadingHTTPServer):
    """HTTP server exposing the latest pushed canvas as an MJPEG stream.

    `daemon_threads` + `allow_reuse_address` so a quick restart (or
    `os._exit` teardown) doesn't trip "address already in use" or hang on
    a lingering stream handler.
    """
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, host: str = "0.0.0.0", port: int = 8080,
                 runtime: dict | None = None,
                 cfg_lock: threading.Lock | None = None,
                 persist: Optional["callable"] = None) -> None:
        super().__init__((host, port), _Handler)
        self._jpg: Optional[bytes] = None   # latest encoded web frame
        self._raw: Optional[np.ndarray] = None  # latest canvas copy
        self._raw_fid = 0
        self._enc_fid = -1
        self._enc_ts = 0.0                 # V0.4: ts of the frame in _jpg
        self._enc_frame_fidx = -1          # V0.4: capture fidx of _jpg frame
        self._meta: dict = {}
        self._frames = 0
        self._clients = 0
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._enc_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        # Runtime knobs surfaced to the web UI. `recording` and `save_dir`
        # are not consumed by MJPEGServer itself — they're read by the
        # recorder wired in app/warn_app.py. We still expose them through
        # /state so the toggle reflects in the UI immediately.
        defaults = {
            "danger_m": 1.5,
            "warning_m": 3.0,
            "buzzer": False,
            "recording": False,   # default: do NOT save danger frames
            "save_dir": "events",
            "depth_target_fps": 10.0,
            "person_target_fps": 5.0,
            "thermal_state": "normal",
            "degradation_reason": None,
        }
        self._runtime = runtime if runtime is not None else {}
        for key, value in defaults.items():
            self._runtime.setdefault(key, value)
        self._cfg_lock = cfg_lock if cfg_lock is not None else threading.Lock()
        self._persist = persist  # optional Callable[[dict], None]

    def _clients_inc(self) -> None:
        with self._lock:
            self._clients += 1

    def _clients_dec(self) -> None:
        with self._lock:
            self._clients = max(0, self._clients - 1)

    def _encoder_loop(self) -> None:
        """Encode the NEWEST canvas only while a client is watching."""
        while not self._stop.is_set():
            if self._clients <= 0:
                self._stop.wait(0.05)
                continue
            with self._lock:
                fid = self._raw_fid
                raw = self._raw
            if raw is None or fid == self._enc_fid:
                self._stop.wait(0.01)  # no new frame yet
                continue
            try:
                img = _web_sized(raw)
                ok, buf = cv2.imencode(
                    ".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, WEB_JPEG_QUALITY])
                jpg = buf.tobytes() if ok else b""
            except Exception:  # noqa: BLE001 - never break the server
                jpg = b""
            with self._lock:
                self._jpg = jpg
                self._enc_fid = fid
                self._enc_ts = time.time()
                self._enc_frame_fidx = int((self._meta or {}).get("fidx", -1)
                                           or -1)

    def apply_config(self, data: dict) -> dict:
        """Thread-safe update of the shared runtime knobs.

        Supported keys: ``danger_m`` (float), ``buzzer`` (bool),
        ``recording`` (bool). Unknown keys are silently ignored. When a
        ``persist`` callback was injected via the constructor, every
        accepted value is also mirrored into the on-disk runtime
        overrides file so the toggle survives a restart.
        """
        changed: dict = {}
        try:
            danger = float(data.get("danger_m", self._runtime["danger_m"]))
            warning = float(data.get("warning_m", self._runtime["warning_m"]))
        except (TypeError, ValueError):
            return {"ok": False, "code": "INVALID_REAR_THRESHOLDS",
                    "message": "thresholds must be numbers"}
        if not (0.3 <= danger <= 5.0 and 0.5 <= warning <= 10.0
                and danger < warning):
            return {"ok": False, "code": "INVALID_REAR_THRESHOLDS",
                    "message": "danger_m must be lower than warning_m"}
        with self._cfg_lock:
            if "danger_m" in data or "warning_m" in data:
                self._runtime["danger_m"] = danger
                self._runtime["warning_m"] = warning
                changed["danger_m"] = danger
                changed["warning_m"] = warning
            if "buzzer" in data:
                self._runtime["buzzer"] = bool(data["buzzer"])
                changed["buzzer"] = self._runtime["buzzer"]
            if "recording" in data:
                self._runtime["recording"] = bool(data["recording"])
                changed["recording"] = self._runtime["recording"]
            for key, low, high in (
                ("depth_target_fps", 1.0, 10.0),
                ("person_target_fps", 1.0, 5.0),
            ):
                if key in data:
                    try:
                        value = float(data[key])
                    except (TypeError, ValueError):
                        continue
                    self._runtime[key] = min(high, max(low, value))
                    changed[key] = self._runtime[key]
            if data.get("thermal_state") in (
                    "normal", "constrained", "critical"):
                self._runtime["thermal_state"] = data["thermal_state"]
                changed["thermal_state"] = data["thermal_state"]
                reason = data.get("degradation_reason")
                self._runtime["degradation_reason"] = (
                    str(reason) if reason else None)
                changed["degradation_reason"] = self._runtime[
                    "degradation_reason"]
        persistent = {
            key: value for key, value in changed.items()
            if key in {"danger_m", "warning_m", "buzzer", "recording"}
        }
        if persistent and self._persist is not None:
            try:
                self._persist(persistent)
            except Exception as exc:  # noqa: BLE001 - never break the response
                print(f"[warn:web] persist failed: {type(exc).__name__}: {exc}")
        return {"ok": True, **dict(self._runtime)}

    def push(self, canvas_bgr: np.ndarray, meta: dict) -> None:
        """Stash a COPY of the latest canvas (NO encoding on this thread).

        Called from the main inference thread. Because the renderer reuses
        its canvas array in place, we must copy — a ~2ms memcpy is far
        cheaper than the old ~16ms synchronous imencode, and the encode now
        happens in the dedicated encoder thread only when a client watches.
        """
        try:
            raw = np.ascontiguousarray(canvas_bgr).copy()
        except Exception:  # noqa: BLE001 - never break the loop over a bad frame
            return
        with self._lock:
            self._raw = raw
            self._raw_fid += 1
            self._meta = meta or {}
            self._frames += 1

    def start(self) -> None:
        self._thread = threading.Thread(target=self.serve_forever,
                                        name="mjpeg-server", daemon=True)
        self._thread.start()
        self._enc_thread = threading.Thread(target=self._encoder_loop,
                                            name="mjpeg-encoder", daemon=True)
        self._enc_thread.start()
        for ip in _lan_ips():
            print(f"[warn:web] http://{ip}:{self.server_address[1]}/"
                  f"  (LAN browser)")

    def stop(self) -> None:
        try:
            self._stop.set()
            self.shutdown()
            self.server_close()
        except Exception:  # noqa: BLE001
            pass
        print("[warn:web] stopped")


def _lan_ips() -> list:
    """Best-effort list of IPv4s to print as connect URLs."""
    ips = ["127.0.0.1"]
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ":" not in ip and ip not in ips:
                ips.append(ip)
    except Exception:  # noqa: BLE001
        pass
    return ips
