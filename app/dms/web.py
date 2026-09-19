"""Zero-dependency MJPEG web UI for the DMS demo (UI v2: DOM status panel).

**自行实现**（契约 §4.8/§5.5/§6.3）：结构与延迟设计复刻 app/web_stream.py 的范式
（`push()` 只拷贝原图；独立编码线程仅在 `_clients > 0` 时 `imencode` 最新帧；
`daemon_threads=True`；`allow_reuse_address=True`），但**绝不 import 该文件**
（它在改动前就是 ` M` 状态，属于 outOfScope）。

UI v2 口径：状态与开关**全部**由页面 DOM 呈现；**推流画面零像素文字**（只有检测框
与框上短标签），因此 960px 推流缩放不再影响任何文字可读性。

调试层纪律（captain D7 + t9 F4）：调试字段的**值只在调试开启时才写入 DOM**，
而不是"先写进隐藏块、再用 CSS/`hidden` 挡住"。因此：
  * `page_html(debug=False)` 的静态文本**完全不含**调试写入器（连 `<script>` 里的
    调试字段访问都没有），只保留一份 id 无调试 token 的 `#debug` 骨架（CamelCase id）；
  * `page_html(debug=True)` 才注入 `writeDebug()`，且 `tick()` 里对它的调用也以
    `if(debugOn)` 门控；
  * 页面内热键 `d` 只切换 `#debug` 的 `hidden`（§5.4），初值来自服务端 `debug`。

路由（冻结，未改动）:
  GET  /                  HTML（中文；两个 checkbox id 冻结为 fatigueEnabled / helmetEnabled）
  GET  /stream            multipart/x-mixed-replace MJPEG（无客户端时不编码）
  GET  /frame             单帧 image/jpeg（无帧时占位图）
  GET  /state             JSON（字段名冻结，见 docs/dms_helmet_demo.md）
  GET  /health            {"ok": true, "frames": N}
  GET  /favicon.ico       204
  POST /api/dms_config    runtime switches including fatigue_alarm_buzzer
                          -> 200 + 应用后的完整快照（附 "ok": true）
  其它                    404 text/plain

端口被占用时由调用方（app/dms_app.py）捕获 OSError 并给出单行错误 + 退出码 5。
"""
from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional

import cv2
import numpy as np

from app.dms.render import NOTICE_CN

_BOUNDARY = "frame"
WEB_MAX_W = 960
WEB_JPEG_QUALITY = 60
# The DMS capture loop is capped at 10 FPS.  Sending duplicate JPEGs at 30 FPS
# creates browser buffering and wastes remote-network bandwidth.
MAX_STREAM_FPS = 12

_HTML_PAGE = """<!doctype html>
<html lang="zh-CN"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>DMS demo — 疲劳 / 头盔佩戴检测</title>
<style>
  body{margin:0;background:#0c0f14;color:#e6e9ef;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
  img{display:block;width:100%;max-width:1280px;margin:0 auto;border-bottom:1px solid #222}
  #ctl{display:flex;flex-wrap:wrap;gap:1.6rem;align-items:center;padding:.7rem .8rem;font-size:16px;border-bottom:1px solid #222}
  #ctl label{display:flex;align-items:center;gap:.5rem;cursor:pointer}
  #ctl input[type=checkbox]{width:20px;height:20px}
  #ctl .st{font-size:16px;font-weight:700}
  #status{padding:.55rem .8rem;font-size:15px;border-bottom:1px solid #222}
  #notice{padding:.5rem .8rem;font-size:14px;color:#fbbf24;background:#1a1408;border-bottom:1px solid #222}
  #debug{padding:.5rem .8rem;font-size:13px;color:#9aa3b5;border-bottom:1px solid #222}
  #debug div{margin:.15rem 0}
  .k{color:#8b93a7} .ok{color:#34d399} .bad{color:#f87171} .warn{color:#fbbf24}
</style></head><body>
<canvas id="canvas" style="display:block;width:100%;max-width:1280px;margin:0 auto;border-bottom:1px solid #222"></canvas>
<div id="ctl">
  <label><input type="checkbox" id="fatigueEnabled"> 疲劳检测 <span id="fatigueState" class="st">-</span></label>
  <label><input type="checkbox" id="helmetEnabled"> 头盔检测 <span id="helmetState" class="st">-</span></label>
</div>
<div id="status"><span id="camState">-</span> · <span id="faceState">-</span></div>
<div id="debug"__DEBUG_HIDDEN__>
  <div><span class="k">模式</span> <span id="dbgMode">-</span> <span class="k">来源</span> <span id="dbgSource">-</span></div>
  <div><span class="k">帧序号</span> <span id="dbgFidx">-</span> <span class="k">帧率</span> <span id="dbgFps">-</span> <span class="k">相机</span> <span id="dbgCam">-</span></div>
  <div><span class="k">疲劳评分</span> <span id="dbgScore">-</span> <span class="k">眼部暗占比</span> <span id="dbgEye">-</span> <span class="k">嘴部张开占比</span> <span id="dbgMouth">-</span></div>
  <div><span class="k">疲劳推理</span> <span id="dbgFatigueInfer">-</span> <span class="k">最近帧号</span> <span id="dbgFatigueFidx">-</span> <span class="k">耗时</span> <span id="dbgFatigueMs">-</span></div>
  <div><span class="k">人体数</span> <span id="dbgPersons">-</span> <span class="k">佩戴/未佩戴/无法判定</span> <span id="dbgCounts">-</span></div>
  <div><span class="k">头盔推理</span> <span id="dbgHelmetInfer">-</span> <span class="k">最近帧号</span> <span id="dbgHelmetFidx">-</span> <span class="k">耗时</span> <span id="dbgHelmetMs">-</span></div>
  <div id="dbgPersonList"></div>
</div>
<div id="notice">__NOTICE__</div>
<script>
const STATE_CN={DISABLED:"已关闭",UNKNOWN:"未知",NORMAL:"正常",DROWSY_WARN:"疲劳预警",DROWSY_ALARM:"疲劳报警"};
const STATE_CLS={DISABLED:"k",UNKNOWN:"warn",NORMAL:"ok",DROWSY_WARN:"warn",DROWSY_ALARM:"bad"};
const HELM_CN={disabled:"已关闭",no_person:"无人体框",not_worn:"未佩戴",unknown:"无法判定",worn:"已佩戴"};
const VERDICT_CN={worn:"已佩戴",not_worn:"未佩戴",unknown:"无法判定"};
const fatigueEl=document.getElementById('fatigueEnabled');
const helmetEl=document.getElementById('helmetEnabled');
const dbgEl=document.getElementById('debug');
let debugForced=null;
let debugOn=__DEBUG_INIT__;
function helmetSummary(h){
  if(!h||!h.enabled) return 'disabled';
  const ps=h.persons||[];
  const seen={};
  for(let i=0;i<ps.length;i++){ seen[ps[i].verdict||'unknown']=1; }
  const keys=Object.keys(seen);
  if(!keys.length) return 'no_person';
  if(seen.not_worn) return 'not_worn';
  if(seen.unknown) return 'unknown';
  return 'worn';
}
function applyDebug(){
  if(debugForced===null) return;
  if(debugForced) dbgEl.removeAttribute('hidden'); else dbgEl.setAttribute('hidden','');
}
document.addEventListener('keydown',function(e){
  if(e.key!=='d'&&e.key!=='D') return;
  const t=e.target;
  if(t&&(t.tagName==='INPUT'||t.tagName==='TEXTAREA')) return;
  debugForced = dbgEl.hasAttribute('hidden');
  if(debugForced) debugOn=true;
  applyDebug();
});
async function postConfig(d){
  try{
    await fetch('/api/dms_config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)});
  }catch(e){}
  tick();
}
fatigueEl.addEventListener('change',function(){ postConfig({fatigue_enabled:fatigueEl.checked}); });
helmetEl.addEventListener('change',function(){ postConfig({helmet_enabled:helmetEl.checked}); });
function fmt(v,unit){ return (v===null||v===undefined)?'-':(Math.round(v*100)/100)+(unit||''); }
function txt(id,value){ document.getElementById(id).textContent=value; }
async function tick(){
  try{
    const r=await fetch('/state',{cache:'no-store'});
    if(!r.ok) throw 0;
    const d=await r.json();
    const f=d.dms.fatigue, h=d.dms.helmet;
    if(document.activeElement!==fatigueEl) fatigueEl.checked=!!f.enabled;
    if(document.activeElement!==helmetEl) helmetEl.checked=!!h.enabled;
    const fs=STATE_CN[f.state]||f.state, fc=STATE_CLS[f.state]||'k';
    const fe=document.getElementById('fatigueState');
    fe.textContent=fs; fe.className='st '+fc;
    document.getElementById('helmetState').textContent=HELM_CN[helmetSummary(h)]||'-';
    txt('camState','相机: '+(d.cam_ok?'正常':'中断'));
    txt('faceState','人脸: '+(!f.enabled?'不适用':(f.face_present?'正常':'丢失')));
    applyDebug();
__DEBUG_WRITER__
  }catch(e){ txt('camState','状态获取失败'); }
}
__DEBUG_SCRIPT__
var _frameImg = new Image();
_frameImg.onload = function() {
    var c = document.getElementById("canvas");
    if (!c) return;
    c.width = _frameImg.naturalWidth || 640;
    c.height = _frameImg.naturalHeight || 480;
    c.getContext("2d").drawImage(_frameImg, 0, 0);
};
_frameImg.onerror = function() { _frameImg.src = "/frame?_t=" + Date.now(); };
_frameImg.src = "/frame";
setInterval(function() { _frameImg.src = "/frame?_t=" + Date.now(); }, 100);

tick(); setInterval(tick,1000);
</script>
</body></html>
"""

# 调试写入器：**只有** debug=True 时才注入页面（captain D7 / t9 F4）。
# 刻意保持"一个函数、一处实现"，且 tick() 里对它的调用也以 `if(debugOn)` 门控——
# 调试值绝不无条件写进 DOM。
_DEBUG_SCRIPT = """function writeDebug(d){
  const f=d.dms.fatigue, h=d.dms.helmet;
  txt('dbgMode',d.mode||'-');
  txt('dbgSource',d.source||'-');
  txt('dbgFidx',d.fidx);
  txt('dbgFps',fmt(d.fps));
  txt('dbgCam',d.cam_ok?'正常':'中断');
  txt('dbgScore',fmt(f.score));
  txt('dbgEye',fmt(f.eye_dark_ratio));
  txt('dbgMouth',fmt(f.mouth_open_ratio));
  txt('dbgFatigueInfer',f.infer_count);
  txt('dbgFatigueFidx',f.last_infer_fidx);
  txt('dbgFatigueMs',fmt(f.last_infer_ms,'ms'));
  const counts=h.verdict_counts||{}; const ps=h.persons||[];
  txt('dbgPersons',ps.length);
  txt('dbgCounts',(counts.worn||0)+'/'+(counts.not_worn||0)+'/'+(counts.unknown||0));
  txt('dbgHelmetInfer',h.infer_count);
  txt('dbgHelmetFidx',h.last_infer_fidx);
  txt('dbgHelmetMs',fmt(h.last_infer_ms,'ms'));
  const rows=[];
  for(let i=0;i<Math.min(ps.length,3);i++){
    const p=ps[i];
    rows.push('id'+p.track_id+' '+(VERDICT_CN[p.verdict]||p.verdict)+'/'+(p.reason||'-')+
      ' 颜色/肤色/暗区 '+fmt(p.helmet_color_ratio)+'/'+fmt(p.skin_ratio)+'/'+fmt(p.dark_ratio)+
      ' 帧龄'+p.age_frames);
  }
  if(ps.length>3) rows.push('+'+(ps.length-3)+' 更多');
  document.getElementById('dbgPersonList').textContent=rows.join(' | ');
}
"""

_DEBUG_CALL = "    if(debugOn) writeDebug(d);\n"


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # noqa: BLE001 - quiet: 20fps would flood
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def do_GET(self):  # noqa: BLE001 - dispatch
        srv: "DmsWebServer" = self.server  # type: ignore[assignment]
        path = self.path.split("?", 1)[0]
        if path == "/":
            self._send(200, srv.page_html().encode("utf-8"),
                       "text/html; charset=utf-8")
        elif path == "/health":
            self._send(200, json.dumps({"ok": True, "frames": srv.frames})
                       .encode("utf-8"), "application/json")
        elif path == "/state":
            self._send(200, json.dumps(srv.state_payload(), default=str)
                       .encode("utf-8"), "application/json")
        elif path == "/stream":
            self._stream(srv)
        elif path == "/frame":
            self._frame(srv)
        elif path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
        else:
            self._send(404, b"not found\n", "text/plain")

    def do_POST(self):  # noqa: BLE001 - dispatch
        srv: "DmsWebServer" = self.server  # type: ignore[assignment]
        path = self.path.split("?", 1)[0]
        if path != "/api/dms_config":
            self._send(404, b"not found\n", "text/plain")
            return
        data: dict = {}
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length > 0 else b"{}"
            parsed = json.loads(raw.decode("utf-8")) if raw else {}
            if isinstance(parsed, dict):
                data = parsed
        except Exception:  # noqa: BLE001 - bad JSON -> no-op, keep old values
            data = {}
        self._send(200, json.dumps(srv.apply_config(data), default=str)
                   .encode("utf-8"), "application/json")

    def _stream(self, srv: "DmsWebServer") -> None:
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
            last_fid = -1
            head = (b"--" + _BOUNDARY.encode() + b"\r\n"
                    b"Content-Type: image/jpeg\r\n")
            while not srv.stopped:
                with srv._lock:
                    jpg = srv._jpg
                    fid = srv._jpg_fid
                if jpg is None:
                    jpg = _placeholder_jpg()
                elif fid == last_fid:
                    time.sleep(min(delay, 0.02))
                    continue
                try:
                    self.wfile.write(head + b"Content-Length: "
                                     + str(len(jpg)).encode() + b"\r\n\r\n"
                                     + jpg + b"\r\n")
                    self.wfile.flush()
                    last_fid = fid
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return
                time.sleep(delay)
        finally:
            srv._clients_dec()

    def _frame(self, srv: "DmsWebServer") -> None:
        with srv._lock:
            raw = srv._raw
        if raw is None:
            self._send(200, _placeholder_jpg(), "image/jpeg")
            return
        try:
            img = _web_sized(raw)
            ok, buf = cv2.imencode(".jpg", img,
                                   [cv2.IMWRITE_JPEG_QUALITY, WEB_JPEG_QUALITY])
            body = buf.tobytes() if ok else _placeholder_jpg()
        except Exception:  # noqa: BLE001
            body = _placeholder_jpg()
        self._send(200, body, "image/jpeg")


def _web_sized(raw: np.ndarray) -> np.ndarray:
    height, width = raw.shape[:2]
    if width <= WEB_MAX_W:
        return raw
    scale = WEB_MAX_W / float(width)
    return cv2.resize(raw, (WEB_MAX_W, int(round(height * scale))),
                      interpolation=cv2.INTER_AREA)


_placeholder_cache: Optional[bytes] = None


def _placeholder_jpg() -> bytes:
    global _placeholder_cache
    if _placeholder_cache is None:
        img = np.zeros((60, 360, 3), dtype=np.uint8)
        cv2.putText(img, "dms: warming up ...", (8, 36),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1,
                    cv2.LINE_AA)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 70])
        _placeholder_cache = buf.tobytes() if ok else b""
    return _placeholder_cache


class DmsWebServer(ThreadingHTTPServer):
    """MJPEG server for the DMS canvas (stdlib only; no app/web_stream import)."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, host: str = "0.0.0.0", port: int = 8010,
                 runtime: Any = None,
                 notices: Optional[List[str]] = None,
                 debug: bool = False) -> None:
        super().__init__((host, port), _Handler)
        self._runtime = runtime
        self._notices = list(notices if notices is not None else [NOTICE_CN])
        self._debug = bool(debug)
        self._jpg: Optional[bytes] = None
        self._raw: Optional[np.ndarray] = None
        self._raw_fid = 0
        self._enc_fid = -1
        self._jpg_fid = -1
        self._meta: Dict[str, Any] = {}
        self._frames = 0
        self._clients = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._enc_thread: Optional[threading.Thread] = None

    # -- public API ---------------------------------------------------------
    @property
    def frames(self) -> int:
        with self._lock:
            return self._frames

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def page_html(self) -> str:
        notice = ""
        if self._notices:
            notice = str(self._notices[0])
        # debug=False：**不注入**调试写入器（D7/F4：静态文本不含调试字段访问）
        return (_HTML_PAGE
                .replace("__DEBUG_HIDDEN__", "" if self._debug else " hidden")
                .replace("__DEBUG_INIT__", "true" if self._debug else "false")
                .replace("__DEBUG_WRITER__", _DEBUG_CALL if self._debug else "")
                .replace("__DEBUG_SCRIPT__",
                         _DEBUG_SCRIPT if self._debug else "")
                .replace("__NOTICE__", _escape_html(notice)))

    def state_payload(self) -> dict:
        with self._lock:
            meta = dict(self._meta)
        snapshot = (self._runtime.snapshot() if self._runtime is not None
                    else {"fatigue": {}, "helmet": {}})
        fps = meta.get("fps")
        return {
            "fidx": int(meta.get("fidx", 0) or 0),
            "ts": float(meta.get("ts", 0.0) or 0.0),
            "fps": float(fps) if isinstance(fps, (int, float)) else 0.0,
            "mode": str(meta.get("mode", "web")),
            "cam_ok": bool(meta.get("cam_ok", False)),
            "source": str(meta.get("source", "")),
            "dms": snapshot,
            "notices": list(self._notices),
        }

    def apply_config(self, data: dict) -> dict:
        """Apply runtime switches and return the complete resulting snapshot."""
        snapshot = (self._runtime.apply_config(data)
                    if self._runtime is not None else {})
        return {
            "ok": True,
            "fatigue": snapshot.get("fatigue", {}),
            "helmet": snapshot.get("helmet", {}),
            "dms": snapshot,
        }

    def push(self, canvas_bgr: np.ndarray, meta: Optional[dict] = None) -> None:
        """Stash a COPY of the latest canvas (no encoding on this thread)."""
        try:
            raw = np.ascontiguousarray(canvas_bgr).copy()
        except Exception:  # noqa: BLE001 - never break the loop over a frame
            return
        with self._lock:
            self._raw = raw
            self._raw_fid += 1
            self._meta = meta or {}
            self._frames += 1

    def start(self) -> None:
        self._thread = threading.Thread(target=self.serve_forever,
                                        name="dms-web", daemon=True)
        self._thread.start()
        self._enc_thread = threading.Thread(target=self._encoder_loop,
                                            name="dms-encoder", daemon=True)
        self._enc_thread.start()
        port = self.server_address[1]
        for ip in _lan_ips():
            print(f"[dms] web ui: http://{ip}:{port}/")

    def stop(self) -> None:
        try:
            self._stop.set()
            self.shutdown()
            self.server_close()
        except Exception:  # noqa: BLE001
            pass

    # -- internals ----------------------------------------------------------
    def _clients_inc(self) -> None:
        with self._lock:
            self._clients += 1

    def _clients_dec(self) -> None:
        with self._lock:
            self._clients = max(0, self._clients - 1)

    def _encoder_loop(self) -> None:
        """Encode the NEWEST canvas only while at least one client watches."""
        while not self._stop.is_set():
            if self._clients <= 0:
                self._stop.wait(0.05)
                continue
            with self._lock:
                fid = self._raw_fid
                raw = self._raw
            if raw is None or fid == self._enc_fid:
                self._stop.wait(0.01)
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
                self._jpg_fid = fid
                self._enc_fid = fid


def _escape_html(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def _lan_ips() -> List[str]:
    ips = ["127.0.0.1"]
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ":" not in ip and ip not in ips:
                ips.append(ip)
    except Exception:  # noqa: BLE001
        pass
    return ips
