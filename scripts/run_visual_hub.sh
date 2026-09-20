#!/usr/bin/env bash
# Visual Hub — the only entry point for the three-camera hub.
#
#   ./scripts/run_visual_hub.sh                start (systemd ExecStart uses this same script)
#   ./scripts/run_visual_hub.sh stop           stop recording + front/rear/dms, release cameras/GPU/port
#   ./scripts/run_visual_hub.sh stop --poe     the same, then cut PoE power (poe-cam-net + poe-pse)
#   ./scripts/run_visual_hub.sh status         one-shot read-only status
#   ./scripts/run_visual_hub.sh gui            desktop entry: start if needed, wait, open Firefox kiosk
#
# `stop` runs before any environment/virtualenv validation on purpose: stopping
# must never depend on the protected env file or on a healthy venv.
#
# `start`/`gui` refuse to start a second hub on a busy port (exit 3) instead of
# letting uvicorn report a bare "[Errno 98] address already in use": the usual
# cause is that visual-hub.service is already running.
#
# A *manual* foreground `start` cannot see FRONT_CAMERA_URL / REAR_CAMERA_URL:
# /etc/seg-demo/visual-hub.env is root:0600 and only systemd can inject it. Such
# a run therefore uses configs/_camera_bindings.yaml (written by the web UI) or
# starts with no cameras at all; it says so instead of leaving you guessing.
#
# The display-picking logic used by `gui` was folded in from the former
# scripts/gui_display.sh (which the former scripts/run_visual_hub_gui.sh
# sourced), so this single script is self-contained.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
PORT="${HUB_PORT:-8000}"
URL="http://127.0.0.1:${PORT}"
SERVICE="visual-hub.service"
STOP_WAIT="${HUB_STOP_WAIT:-45}"

# --- gui subcommand state ---
GUI_WAIT="${VISUAL_HUB_GUI_WAIT:-300}"
RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/visual-hub"
PROFILE_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/visual-hub/firefox-profile"
LOCK_FILE="$RUNTIME_DIR/visual-hub-gui.lock"
GUI_LOG="$STATE_DIR/gui.log"

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

notify() {  # notify <urgency> <message>
  command -v notify-send >/dev/null 2>&1 || return 0
  if [ "$1" = "critical" ]; then
    notify-send -u critical "Visual Hub" "$2" || true
  else
    notify-send -u low -t 5000 "Visual Hub" "$2" || true
  fi
}

# ===========================================================================
# Display selection (folded in from the former scripts/gui_display.sh)
#
# This device has several kinds of X displays:
#   :0      physical console (gdm autologin)  — MAY HAVE NO MONITOR ATTACHED
#   :N      xrdp virtual session (viewed remotely over RDP from a laptop)
#   :100    xpra session (viewed over SSH from the user's own computer)
#
# Symptom: app windows "don't open".  A stale DISPLAY (a disconnected xrdp
# session, an sshd X11-forwarding leftover, or a systemd --user environment
# poisoned by dbus-update-activation-environment from an RDP login) silently
# sends GTK/Qt windows to a screen nobody is watching.  Worse: the console
# X server can be reachable while its monitor is off/disconnected, so a
# "reachable" display is NOT a "visible" display.
#
# select_gui_display() exports DISPLAY + XAUTHORITY + GUI_DISPLAY_KIND with:
#   1. GUI_DISPLAY=:N      — explicit override, used when reachable.
#   2. SSH session (SSH_CONNECTION set) with xpra installed and XPRA!=0
#                          — use the xpra session :100 (auto-start if needed).
#   3. current $DISPLAY when reachable:
#        - host-prefixed (ssh -X style) -> KEEP: an active SSH X11 tunnel.
#        - xrdp display   -> KEEP IT (the user launches from inside the RDP
#          session; only a NOTE when no client is connected right now).
#        - console display -> keep ONLY while a monitor is actually attached.
#   4. otherwise: console display WITH a connected monitor, else the newest
#      reachable xrdp display.
#   5. fail with a clear message (return 1); the caller decides what to do.
#
# Override for special cases:
#     GUI_DISPLAY=:10 ./scripts/run_visual_hub.sh gui   # force a display
#     XPRA=0 ./scripts/run_visual_hub.sh gui            # never use xpra
# ===========================================================================
__gd_xauth_gdm="/run/user/1000/gdm/Xauthority"
__gd_xpra_display=":100"

__gd_norm() {  # normalize a DISPLAY value:  :10.0 / localhost:10.0 -> :10
  local d="$1"
  d="${d##*:}"            # strip any host part
  echo ":${d%%.*}"        # strip the .screen (e.g. :10.0 -> :10)
}

__gd_has_host() {  # 1 if DISPLAY has a host prefix (ssh -X style)
  case "$1" in
    *:*) case "${1%%:*}" in ""|/) return 1 ;; *) return 0 ;; esac ;;
    *)   return 1 ;;
  esac
}

__gd_reachable() {  # $1 = display (:N or host:N.N) — X server answers xset
  local d="$1"
  if __gd_has_host "$d"; then
    # ssh -X tunnel: ask xset to open the display itself (auth comes
    # from ~/.Xauthority where sshd added the forwarding cookie).
    XAUTHORITY="$__gd_xauth_gdm" timeout 2 xset -display "$d" q \
      >/dev/null 2>&1 && return 0
    timeout 2 xset -display "$d" q >/dev/null 2>&1
    return $?
  fi
  local dn; dn=$(__gd_norm "$d")
  [ -S "/tmp/.X11-unix/X${dn#:}" ] || return 1
  if command -v xset >/dev/null 2>&1; then
    XAUTHORITY="$__gd_xauth_gdm" timeout 2 xset -display "$dn" q \
      >/dev/null 2>&1 && return 0
    timeout 2 xset -display "$dn" q >/dev/null 2>&1
  else
    return 0
  fi
}

__gd_is_xrdp() {  # $1 = display (:N or host:N.N) ; the Xorg serving it runs an xrdp config
  local d="$1"
  local n="${d##*:}"
  n="${n%%.*}"
  case "$n" in
    ""|*[!0-9]*) return 1 ;;
  esac
  command -v pgrep >/dev/null 2>&1 || return 1
  pgrep -f "Xorg :${n}( |\$).*xrdp" >/dev/null 2>&1
}

__gd_rdp_connected() {
  command -v ss >/dev/null 2>&1 || return 1
  ss -tn state established '( sport = :3389 )' 2>/dev/null | grep -q ':3389'
}

__gd_has_monitor() {  # $1 = display (:N) — a real output is connected
  local d out
  d=$(__gd_norm "$1")
  command -v xrandr >/dev/null 2>&1 || return 0   # unknown -> assume yes
  out=$(XAUTHORITY="$__gd_xauth_gdm" timeout 2 xrandr --display "$d" \
        2>/dev/null)
  [ -n "$out" ] || out=$(timeout 2 xrandr --display "$d" 2>/dev/null)
  echo "$out" | grep -qE '(^|[^s]) connected' && return 0
  return 1
}

__gd_console_display() {
  # console = reachable X socket whose Xorg has NO xrdp config
  local s n
  for s in /tmp/.X11-unix/X[0-9]*; do
    [ -e "$s" ] || continue
    n="${s##*X}"                    # digits only (strip the X prefix)
    case "$n" in
      *[!0-9]*) continue ;;         # stray files like X0.lock
    esac
    if __gd_reachable ":$n" && ! __gd_is_xrdp ":$n"; then
      echo ":$n"
      return 0
    fi
  done
  return 1
}

__gd_newest_xrdp() {
  local s n
  for s in /tmp/.X11-unix/X[0-9]*; do
    [ -e "$s" ] || continue
    n="${s##*X}"                    # digits only (strip the X prefix)
    case "$n" in
      *[!0-9]*) continue ;;         # stale files like X0.lock
    esac
    if __gd_reachable ":$n" && __gd_is_xrdp ":$n"; then
      echo ":$n"
      return 0
    fi
  done
  return 1
}

__gd_wake_monitor() {  # $1 = display (:N) — nudge a sleeping console display
  command -v xset >/dev/null 2>&1 || return 0
  XAUTHORITY="$__gd_xauth_gdm" timeout 2 xset -display "$1" dpms force on \
    >/dev/null 2>&1
}

__gd_is_xpra_session() {  # is the xpra session :100 running?
  command -v xpra >/dev/null 2>&1 || return 1
  local sock
  sock="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/xpra/$(id -un)-${__gd_xpra_display#:}"
  [ -S "$sock" ]
}

__gd_in_ssh() {  # is this shell inside a REAL sshd session?
  [ -n "${SSH_CONNECTION:-}" ] || return 1
  # 1) logind session from our cgroup (survives reparenting: nohup,
  #    backgrounding, setsid — the cgroup stays in session-N.scope)
  if command -v loginctl >/dev/null 2>&1; then
    local sess
    sess=$(grep -oE 'session-[0-9]+\.scope' /proc/self/cgroup 2>/dev/null \
           | head -1 | grep -oE '[0-9]+')
    if [ -n "$sess" ] && \
       [ "$(loginctl show-session "$sess" -p Service --value 2>/dev/null)" = "sshd" ]; then
      return 0
    fi
  fi
  # 2) ancestry walk (fast path; fails after reparenting)
  local p=$$ comm="" pp=""
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    [ -r "/proc/$p/stat" ] || break
    comm=$(awk '{print $2}' "/proc/$p/stat" 2>/dev/null | tr -d '()')
    [ "$comm" = "sshd" ] && return 0
    pp=$(awk '{print $4}' "/proc/$p/stat" 2>/dev/null)
    case "$pp" in
      ""|0|1) break ;;
    esac
    p="$pp"
  done
  return 1
}

__gd_start_xpra() {
  # Idempotent: start the reusable xpra app session (daemon).  The virtual
  # display follows the app window size (resize-display) so the 1280x720
  # canvas maps 1:1; the client connects with:
  #     xpra attach ssh:seeed@<this-host>:100
  command -v xpra >/dev/null 2>&1 || return 1
  if __gd_is_xpra_session; then
    return 0
  fi
  echo "[gui-display] starting xpra session :100 ..." >&2
  xpra start "${__gd_xpra_display}" \
      --resize-display=yes \
      --min-size=1280x720 \
      --max-size=2560x1440 \
      --daemon=yes 2>&1 | tail -1 >&2
  # wait for the session socket
  for _ in 1 2 3 4 5; do
    __gd_is_xpra_session && return 0
    sleep 1
  done
  return 1
}

select_gui_display() {  # export DISPLAY/XAUTHORITY/GUI_DISPLAY_KIND; 1 = none visible
  local chosen="" cand=""
  GUI_DISPLAY_KIND=""

  if [ -n "${GUI_DISPLAY:-}" ]; then
    if __gd_reachable "$GUI_DISPLAY"; then
      chosen="$GUI_DISPLAY"
      echo "[gui-display] GUI_DISPLAY=$GUI_DISPLAY (explicit override)"
    else
      echo "[gui-display] WARNING: GUI_DISPLAY=$GUI_DISPLAY unreachable - auto-resolving" >&2
    fi
  fi

  # ssh -X native tunnel (DISPLAY=host:N.N): the window goes straight to the
  # SSH client's X server — keep it, it beats xpra (no attach step needed).
  if [ -z "$chosen" ] && [ -n "${DISPLAY:-}" ] \
     && __gd_has_host "$DISPLAY" && __gd_reachable "$DISPLAY"; then
    chosen="$DISPLAY"
    echo "[gui-display] SSH X11-forwarded DISPLAY=$DISPLAY (windows go to the SSH client's screen)" 2>&1
  fi

  # SSH + xpra: auto-forward the window to the user's own computer.
  if [ -z "$chosen" ] && __gd_in_ssh \
     && [ "${XPRA:-1}" != "0" ]; then
    if command -v xpra >/dev/null 2>&1; then
      if __gd_start_xpra; then
        chosen="$__gd_xpra_display"
        echo "[gui-display] SSH session detected: using xpra display" \
             "$__gd_xpra_display (window appears on the SSH client's" \
             " screen; attach: xpra attach ssh:seeed@<jetson-ip>:100)" 2>&1
      else
        echo "[gui-display] WARNING: could not start xpra session - falling back" >&2
      fi
    fi
  fi

  if [ -z "$chosen" ] && [ -n "${DISPLAY:-}" ] && __gd_reachable "$DISPLAY"; then
    if __gd_is_xrdp "$DISPLAY"; then
      # Launched from inside the RDP session (or with its DISPLAY): that is
      # where the user looks.  Keep it even if the RDP client is away for a
      # moment — the window is there when the client (re)connects.
      chosen="$DISPLAY"
      __gd_rdp_connected || \
          echo "[gui-display] NOTE: DISPLAY=$DISPLAY is an xrdp session with no RDP client connected right now — the window becomes visible when the client reconnects" >&2
    elif __gd_has_monitor "$DISPLAY"; then
      chosen="$DISPLAY"
      __gd_wake_monitor "$chosen"
    else
      echo "[gui-display] DISPLAY=$DISPLAY is the console but NO monitor is attached — switching to a visible display" >&2
    fi
  fi

  if [ -z "$chosen" ]; then
    cand=$(__gd_console_display)
    if [ -n "$cand" ] && __gd_has_monitor "$cand"; then
      chosen="$cand"
      __gd_wake_monitor "$chosen"
    else
      chosen=$(__gd_newest_xrdp)
      [ -n "$chosen" ] && \
          echo "[gui-display] no viewable console monitor; using xrdp display $chosen" >&2
    fi
  fi

  if [ -z "$chosen" ]; then
    echo "[gui-display] ERROR: no reachable X display found." >&2
    echo "[gui-display]   - run on the device with a desktop session," >&2
    echo "[gui-display]   - connect via RDP and set GUI_DISPLAY=:10," >&2
    echo "[gui-display]   - connect via SSH with xpra installed (auto-used), " >&2
    echo "[gui-display]   - or use the headless API instead." >&2
    return 1
  fi

  export DISPLAY="$chosen"
  if __gd_is_xrdp "$chosen"; then
    unset XAUTHORITY  # xrdp sessions keep their cookie in ~/.Xauthority
  elif [ "$chosen" = "$__gd_xpra_display" ]; then
    unset XAUTHORITY  # xpra's own X server needs no cookie
  else
    [ -f "$__gd_xauth_gdm" ] && export XAUTHORITY="$__gd_xauth_gdm"
  fi
  if [ "$chosen" = "$__gd_xpra_display" ]; then
    GUI_DISPLAY_KIND="xpra"
  elif __gd_is_xrdp "$chosen"; then
    GUI_DISPLAY_KIND="xrdp"
  elif __gd_has_host "$chosen"; then
    GUI_DISPLAY_KIND="ssh-x11"
  else
    GUI_DISPLAY_KIND="console"
  fi
  export GUI_DISPLAY_KIND
  echo "[gui-display] GUI display=$DISPLAY (kind=$GUI_DISPLAY_KIND)"
  return 0
}

# ===========================================================================
# Hub lifecycle: start / stop / status
# ===========================================================================
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
  while [ "$i" -lt "$STOP_WAIT" ]; do
    port_busy || break
    sleep 1
    i=$((i + 1))
  done

  if port_busy; then
    echo "[hub] ERROR: port ${PORT} is still in use after ${STOP_WAIT}s" >&2
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

  # Refuse before uvicorn gets a chance to, with something actionable. A second
  # hub on the same port is always a mistake and "[Errno 98] address already in
  # use" does not tell the operator what to do about it.
  if port_busy; then
    echo "[hub] port $PORT is already in use — visual-hub.service is probably running." >&2
    echo "[hub]   ./scripts/run_visual_hub.sh status   # read-only status" >&2
    echo "[hub]   sudo systemctl restart visual-hub   # (re)start it, e.g. after a config change" >&2
    echo "[hub]   ./scripts/run_visual_hub.sh stop     # stop it first to run in the foreground" >&2
    exit 3
  fi

  # A missing camera is a note, not a boot failure: a role that is still
  # unconfigured refuses to start, with a per-module error, instead of taking
  # the hub down with it.
  if [[ -z "${FRONT_CAMERA_URL:-}" && -z "${REAR_CAMERA_URL:-}" ]]; then
    if [[ -f "$ROOT/configs/_camera_bindings.yaml" ]]; then
      echo "[hub] note: no camera URL in this shell; using configs/_camera_bindings.yaml" >&2
    else
      echo "[hub] note: no camera URL in this shell, and no binding file yet." >&2
      echo "[hub]       /etc/seg-demo/visual-hub.env is root:0600, so systemd is the only" >&2
      echo "[hub]       thing that can inject it — a manual run starts with no cameras." >&2
      echo "[hub]       Either use the service:" >&2
      echo "[hub]         sudo systemctl start visual-hub    (or ./scripts/run_visual_hub.sh gui)" >&2
      echo "[hub]       ...or pick a camera per module in the UI, which writes that binding file." >&2
    fi
  fi

  GL_DISPATCH=/lib/aarch64-linux-gnu/libGLdispatch.so.0
  if [[ -f "$GL_DISPATCH" ]]; then
    export LD_PRELOAD="$GL_DISPATCH${LD_PRELOAD:+:$LD_PRELOAD}"
  fi
  export PYTHONUNBUFFERED=1
  # HUB_CAMERA_BINDINGS / HUB_EXTRA_CAMERAS / DMS_CAMERA are read straight from
  # the environment by backend/app/hub/config.py and never forced here, so an
  # operator's own choice always wins over this script.

  cd "$ROOT"
  exec "$PY" -u -m backend.app.main --host 0.0.0.0 --port "$PORT"
}

# ===========================================================================
# gui: the desktop entry (former scripts/run_visual_hub_gui.sh)
#
# Behaviour:
#   - single instance (flock): a second double-click only raises a notification
#   - starts visual-hub.service when it is down, without any password prompt
#     (requires deploy/seeed-nopasswd.sudoers; pkexec remains a fallback)
#   - starts the unit with --no-block and then polls /api/health, so a slow
#     service start (model load, camera open) can never freeze the desktop entry
#   - picks a visible X display via select_gui_display(), then opens Firefox
#     kiosk with a dedicated profile
# ===========================================================================
open_gui() {
  local started=0 i

  mkdir -p "$STATE_DIR" "$PROFILE_DIR"
  exec 9>"$LOCK_FILE"
  if ! flock -n 9; then
    notify normal "总控 GUI 已经打开"
    exit 0
  fi

  if ! curl -fsS --max-time 2 "$URL" >/dev/null 2>&1; then
    notify normal "正在启动总控服务…"
    # Passwordless path: /etc/sudoers.d/seeed-nopasswd is installed on this device.
    # --no-block keeps the desktop entry responsive even when the hub starts slowly.
    if sudo -n systemctl start --no-block "$SERVICE" >/dev/null 2>&1; then
      started=1
    elif command -v pkexec >/dev/null 2>&1; then
      # Fallback for a machine without the sudoers drop-in (opens an auth dialog).
      if pkexec systemctl start --no-block "$SERVICE"; then
        started=1
      fi
    fi
    if [ "$started" = 0 ]; then
      notify critical "总控服务未运行，且无法自动启动"
      exit 1
    fi
  fi

  for i in $(seq 1 "$GUI_WAIT"); do
    if curl -fsS --max-time 2 "$URL/api/health" >/dev/null 2>&1; then
      break
    fi
    if [ "$(systemctl is-active "$SERVICE" 2>/dev/null || true)" = "failed" ]; then
      break
    fi
    sleep 1
  done
  if ! curl -fsS --max-time 2 "$URL/api/health" >/dev/null 2>&1; then
    notify critical "总控服务启动超时（${GUI_WAIT}s），请查看 journalctl -u ${SERVICE}"
    exit 1
  fi

  # Reuse the project's physical/RDP/Xpra display selection rules.
  if ! select_gui_display; then
    notify critical "未找到可见的显示，无法打开总控界面"
    exit 1
  fi

  if ! command -v firefox >/dev/null 2>&1; then
    notify critical "未找到 Firefox"
    exit 1
  fi

  {
    echo "[$(date --iso-8601=seconds)] opening $URL on DISPLAY=${DISPLAY:-unset}"
    firefox --no-remote --new-instance --profile "$PROFILE_DIR" --kiosk "$URL/"
  } >>"$GUI_LOG" 2>&1
}

case "${1:-start}" in
  start | "") start_hub ;;
  stop) stop_hub "${2:-}" ;;
  status) show_status ;;
  gui) open_gui ;;
  -h | --help | help)
    sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'
    ;;
  *)
    echo "usage: $0 [start|stop [--poe]|status|gui]" >&2
    exit 2
    ;;
esac