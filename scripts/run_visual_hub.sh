#!/usr/bin/env bash
# Visual Hub — the only manual entry point for the three-camera hub.
#
#   ./scripts/run_visual_hub.sh                start (systemd ExecStart uses this same script)
#   ./scripts/run_visual_hub.sh stop           stop recording + front/rear/dms, release cameras/GPU/port
#   ./scripts/run_visual_hub.sh stop --poe     the same, then cut PoE power (poe-cam-net + poe-pse)
#   ./scripts/run_visual_hub.sh status         one-shot read-only status
#
# `stop` runs before any environment/virtualenv validation on purpose: stopping
# must never depend on the protected env file or on a healthy venv.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
PORT="${HUB_PORT:-8000}"
URL="http://127.0.0.1:${PORT}"
SERVICE="visual-hub.service"
WAIT_SECONDS="${HUB_STOP_WAIT:-45}"

port_busy() { ss -ltn 2>/dev/null | grep -q ":${PORT} "; }

main_pid() {
  local pid
  pid="$(systemctl show "$SERVICE" -p MainPID --value 2>/dev/null || echo 0)"
  if [ -n "$pid" ] && [ "$pid" != "0" ]; then
    echo "$pid"
    return 0
  fi
  # Fallback for a foreground run that systemd does not know about.
  pgrep -f "^${PY} -u -m backend\.app\.main" 2>/dev/null | head -1 || true
}

stop_hub() {
  local reachable=0 pid leftover i=0 with_poe=0
  [ "${1:-}" = "--poe" ] && with_poe=1

  if curl -fsS --max-time 2 "$URL/api/health" >/dev/null 2>&1; then
    reachable=1
    echo "[hub] stopping recordings"
    curl -fsS --max-time 8 -X POST "$URL/api/recording/actions/stop-all" >/dev/null 2>&1 \
      || echo "[hub]   recording stop-all unavailable; relying on shutdown"
    echo "[hub] stopping front/rear/dms inference"
    curl -fsS --max-time 20 -X POST "$URL/api/hub/actions/stop-all" >/dev/null 2>&1 \
      || echo "[hub]   module stop-all unavailable; relying on shutdown"
  fi

  pid="$(main_pid)"
  if [ "$reachable" = 0 ] && ! port_busy && [ -z "${pid:-}" ]; then
    echo "[hub] already stopped (no listener on ${PORT}, no hub process)"
    return 0
  fi

  if sudo -n systemctl stop "$SERVICE" >/dev/null 2>&1; then
    echo "[hub] systemd stop requested for ${SERVICE}"
  fi

  # Give a systemd-managed instance a moment; otherwise escalate to SIGTERM
  # (the service user owns the process, so no password is needed).
  while [ "$i" -lt 5 ]; do
    port_busy || break
    sleep 1
    i=$((i + 1))
  done
  if port_busy; then
    pid="$(main_pid)"
    if [ -n "${pid:-}" ]; then
      echo "[hub] port ${PORT} still held by pid ${pid}; sending SIGTERM"
      kill -TERM "$pid" 2>/dev/null || true
    else
      echo "[hub] WARNING: port ${PORT} is held by an unknown process" >&2
    fi
  fi

  i=0
  while [ "$i" -lt "$WAIT_SECONDS" ]; do
    port_busy || break
    sleep 1
    i=$((i + 1))
  done

  if port_busy; then
    echo "[hub] ERROR: port ${PORT} is still in use after ${WAIT_SECONDS}s" >&2
    return 1
  fi

  leftover="$(pgrep -f 'app/(warn_app|dms_app)\.py' 2>/dev/null || true)"
  if [ -n "$leftover" ]; then
    echo "[hub] WARNING: leftover module processes: $(echo "$leftover" | tr '\n' ' ')" >&2
  fi

  echo "[hub] released: port ${PORT} closed, camera/GPU holders gone"

  if [ "$with_poe" = 1 ]; then
    # Optional hard release: drop the camera-subnet provisioning and the PoE PSE
    # power hold (cutting camera power). The next boot/start re-provisions them.
    echo "[hub] stopping PoE provisioning and power hold"
    sudo -n systemctl stop poe-cam-net.service >/dev/null 2>&1 \
      || echo "[hub]   poe-cam-net not stopped (needs the passwordless sudoers drop-in)"
    sudo -n systemctl stop poe-pse.service >/dev/null 2>&1 \
      || echo "[hub]   poe-pse not stopped"
    echo "[hub] poe-pse: $(systemctl is-active poe-pse.service 2>/dev/null || true)  poe-cam-net: $(systemctl is-active poe-cam-net.service 2>/dev/null || true)"
  fi

  echo "[hub] service state: $(systemctl is-active "$SERVICE" 2>/dev/null || true)"
  return 0
}

show_status() {
  printf 'service: %s\n' "$(systemctl is-active "$SERVICE" 2>/dev/null || true)"
  curl -fsS --max-time 3 "$URL/api/hub/status" || echo "hub API not reachable on ${URL}"
}

start_hub() {
  if [[ ! -x "$PY" ]]; then
    echo "[hub] missing virtualenv interpreter: $PY" >&2
    exit 1
  fi
  if [[ -z "${FRONT_CAMERA_URL:-}" || -z "${REAR_CAMERA_URL:-}" ]]; then
    echo "[hub] FRONT_CAMERA_URL and REAR_CAMERA_URL must be provided by the protected environment file" >&2
    exit 2
  fi
  if [[ "${DMS_CAMERA:-usb:0}" != "usb:0" ]]; then
    echo "[hub] DMS_CAMERA must remain usb:0" >&2
    exit 2
  fi

  GL_DISPATCH=/lib/aarch64-linux-gnu/libGLdispatch.so.0
  if [[ -f "$GL_DISPATCH" ]]; then
    export LD_PRELOAD="$GL_DISPATCH${LD_PRELOAD:+:$LD_PRELOAD}"
  fi
  export DMS_CAMERA=usb:0
  export DMS_FIXED_USB_ONLY=1
  export PYTHONUNBUFFERED=1

  cd "$ROOT"
  exec "$PY" -u -m backend.app.main --host 0.0.0.0 --port "$PORT"
}

case "${1:-start}" in
  start | "") start_hub ;;
  stop) stop_hub "${2:-}" ;;
  status) show_status ;;
  -h | --help | help)
    sed -n '2,7p' "$0" | sed 's/^# \{0,1\}//'
    ;;
  *)
    echo "usage: $0 [start|stop [--poe]|status]" >&2
    exit 2
    ;;
esac