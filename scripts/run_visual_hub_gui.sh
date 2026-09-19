#!/bin/bash
# Visual Hub desktop entry: open the hub full screen on the Jetson display.
#
# Started by deploy/visual-hub.desktop (installed on ~/Desktop). Behaviour:
#   - single instance (flock): a second double-click only raises a notification
#   - starts visual-hub.service when it is down, without any password prompt
#     (requires deploy/seeed-nopasswd.sudoers; pkexec remains a fallback)
#   - starts the unit with --no-block and then polls /api/health, so a slow
#     poe-cam-net oneshot can never freeze the desktop entry
#   - picks a visible X display via scripts/gui_display.sh, then opens Firefox
#     kiosk with a dedicated profile
set -euo pipefail

ROOT="/home/seeed/workspace/seg_demo"
URL="http://127.0.0.1:8000"
SERVICE="visual-hub.service"
WAIT_SECONDS="${VISUAL_HUB_GUI_WAIT:-300}"
RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/visual-hub"
PROFILE_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/visual-hub/firefox-profile"
LOCK_FILE="$RUNTIME_DIR/visual-hub-gui.lock"

notify() {  # notify <urgency> <message>
    command -v notify-send >/dev/null 2>&1 || return 0
    if [ "$1" = "critical" ]; then
        notify-send -u critical "Visual Hub" "$2" || true
    else
        notify-send -u low -t 5000 "Visual Hub" "$2" || true
    fi
}

mkdir -p "$STATE_DIR" "$PROFILE_DIR"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    notify normal "总控 GUI 已经打开"
    exit 0
fi

if ! curl -fsS --max-time 2 "$URL" >/dev/null 2>&1; then
    notify normal "正在启动总控服务…"
    started=0
    # Passwordless path: /etc/sudoers.d/seeed-nopasswd is installed on this device.
    # --no-block keeps the desktop entry responsive even when poe-cam-net is slow.
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

for _ in $(seq 1 "$WAIT_SECONDS"); do
    if curl -fsS --max-time 2 "$URL/api/health" >/dev/null 2>&1; then
        break
    fi
    if [ "$(systemctl is-active "$SERVICE" 2>/dev/null || true)" = "failed" ]; then
        break
    fi
    sleep 1
done
if ! curl -fsS --max-time 2 "$URL/api/health" >/dev/null 2>&1; then
    notify critical "总控服务启动超时（${WAIT_SECONDS}s），请查看 journalctl -u ${SERVICE}"
    exit 1
fi

# Reuse the project's physical/RDP/Xpra display selection rules.
. "$ROOT/scripts/gui_display.sh"

if ! command -v firefox >/dev/null 2>&1; then
    notify critical "未找到 Firefox"
    exit 1
fi

{
    echo "[$(date --iso-8601=seconds)] opening $URL on DISPLAY=${DISPLAY:-unset}"
    firefox --no-remote --new-instance --profile "$PROFILE_DIR" --kiosk "$URL/"
} >>"$STATE_DIR/gui.log" 2>&1