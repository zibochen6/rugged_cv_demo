#!/bin/bash
# One-click DMS demo (疲劳检测 + 头盔佩戴检测, 演示级).
#
#   ./scripts/run_dms_demo.sh                                  # web 模式 + configs/dms.yaml 的相机源
#   ./scripts/run_dms_demo.sh --mode display                   # 本地窗口（source scripts/gui_display.sh 选屏）
#   DMS_CAMERA=synthetic ./scripts/run_dms_demo.sh --max-frames 120
#   DMS_PORT=8011 ./scripts/run_dms_demo.sh --headless
#   DMS_MODE=display GUI_DISPLAY=:0 ./scripts/run_dms_demo.sh
#
# 环境覆盖: DMS_MODE(web|display) DMS_CAMERA(源) DMS_PORT(默认 8010) DMS_HEADLESS(0|1)
#           GUI_DISPLAY(:N，display 模式强制选屏)
# 退出码: 2 参数错误 | 3 相机源不可用 | 4 display 无可用 X | 5 web 端口被占用
set -u
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
PY="$ROOT/.venv/bin/python"
MODE="${DMS_MODE:-web}"
CAMERA="${DMS_CAMERA:-}"
PORT="${DMS_PORT:-8010}"

ARGS=(--mode "$MODE")
if [ -n "$CAMERA" ]; then
  ARGS+=(--camera "$CAMERA")
fi
if [ "${DMS_HEADLESS:-0}" = "1" ]; then
  ARGS+=(--headless)
fi

if [ "$MODE" = "display" ]; then
  # source 选屏：失败必须单行报错 + 退出码 4，而不是把窗口丢到看不见的屏
  if ! . "$ROOT/scripts/gui_display.sh"; then
    echo "[dms-run] ERROR: no usable X display for display mode." >&2
    echo "[dms-run]   connect via RDP (GUI_DISPLAY=:10) or use --mode web." >&2
    exit 4
  fi
else
  ARGS+=(--port "$PORT")
  # 端口先在 shell 里探一次：给出可读结论而不是 Python traceback
  if ! "$PY" -c "import socket;s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);s.bind(('0.0.0.0',$PORT));s.close()" 2>/dev/null; then
    echo "[dms-run] ERROR: port $PORT is already in use." >&2
    echo "[dms-run]   stop that process (see: ss -ltnp | grep $PORT) or set DMS_PORT=<other>." >&2
    exit 5
  fi
  echo "[dms-run] open in a browser:"
  for ip in $(hostname -I 2>/dev/null) 127.0.0.1; do
    echo "    http://${ip}:${PORT}/"
  done
fi

echo "[dms-run] mode=$MODE camera=${CAMERA:-<configs/dms.yaml>} extras: $*"
# Jetson static-TLS 规避（同 run_web.sh / run_warning.sh）：torch/TRT 与 GTK 同时在场时，
# gst/nv 之类插件可能 dlopen 失败（"cannot allocate memory in static TLS block"）。
GLD=/lib/aarch64-linux-gnu/libGLdispatch.so.0
[ -f "$GLD" ] && export LD_PRELOAD="$GLD${LD_PRELOAD:+:$LD_PRELOAD}"

exec "$PY" -u app/dms_app.py "${ARGS[@]}" "$@"
