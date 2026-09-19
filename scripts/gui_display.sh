#!/bin/bash
# gui_display.sh — pick an X display the user can actually SEE.
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
# Policy (exports DISPLAY + XAUTHORITY + GUI_DISPLAY_KIND):
#   1. GUI_DISPLAY=:N      — explicit override, used when reachable.
#   2. SSH session (SSH_CONNECTION set) with xpra installed and
#      XPRA!=0            — use the xpra session :100 (auto-start if needed);
#                           the user watches the app on their own computer.
#   3. current $DISPLAY when reachable:
#        - host-prefixed (ssh -X style, e.g. localhost:10.0) -> KEEP: it is
#          an active SSH X11 tunnel to the user's machine.
#        - xrdp display   -> KEEP IT.  The user launches from inside the
#          RDP session and views there; the window stays for the RDP client
#          (re)connect.  (Only a NOTE when no client is connected now.)
#        - console display -> keep ONLY while a monitor is actually
#          attached (xrandr shows a connected output); the monitor is also
#          woken from DPMS sleep.
#   4. otherwise: console display WITH a connected monitor, else the newest
#      reachable xrdp display.
#   5. fail with a clear message (caller decides: --headless or abort).
#
# Usage (must be SOURCED so the exports stick):
#     . scripts/gui_display.sh
#
# Override for special cases:
#     GUI_DISPLAY=:10 ./scripts/run_warning.sh   # force a display
#     XPRA=0 ./scripts/run_warning.sh            # never use the xpra session
__gd_old_setu="${-}"
set -u

__gd_xauth_gdm="/run/user/1000/gdm/Xauthority"

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
            *[!0-9]*) continue ;;        # stray files like X0.lock
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
            *[!0-9]*) continue ;;        # stray files like X0.lock
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

# ------------------------------------------------------------- xpra (SSH)
__gd_xpra_display=":100"
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

# ------------------------------------------------------------ resolution
GUI_DISPLAY_KIND=""
__gd_chosen=""
if [ -n "${GUI_DISPLAY:-}" ]; then
    if __gd_reachable "$GUI_DISPLAY"; then
        __gd_chosen="$GUI_DISPLAY"
        echo "[gui-display] GUI_DISPLAY=$GUI_DISPLAY (explicit override)"
    else
        echo "[gui-display] WARNING: GUI_DISPLAY=$GUI_DISPLAY unreachable - auto-resolving" >&2
    fi
fi

# ssh -X native tunnel (DISPLAY=host:N.N): the window goes straight to the
# SSH client's X server — keep it, it beats xpra (no attach step needed).
if [ -z "$__gd_chosen" ] && [ -n "${DISPLAY:-}" ] \
   && __gd_has_host "$DISPLAY" && __gd_reachable "$DISPLAY"; then
    __gd_chosen="$DISPLAY"
    echo "[gui-display] SSH X11-forwarded DISPLAY=$DISPLAY (windows go to the SSH client's screen)" 2>&1
fi

# SSH + xpra: auto-forward the window to the user's own computer.
if [ -z "$__gd_chosen" ] && __gd_in_ssh \
   && [ "${XPRA:-1}" != "0" ]; then
    if command -v xpra >/dev/null 2>&1; then
        if __gd_start_xpra; then
            __gd_chosen="$__gd_xpra_display"
            echo "[gui-display] SSH session detected: using xpra display" \
                 "$__gd_xpra_display (window appears on the SSH client's" \
                 " screen; attach: xpra attach ssh:seeed@<jetson-ip>:100)" 2>&1
        else
            echo "[gui-display] WARNING: could not start xpra session - falling back" >&2
        fi
    fi
fi

if [ -z "$__gd_chosen" ] && [ -n "${DISPLAY:-}" ] && __gd_reachable "$DISPLAY"; then
    if __gd_is_xrdp "$DISPLAY"; then
        # Launched from inside the RDP session (or with its DISPLAY): that is
        # where the user looks.  Keep it even if the RDP client is away for a
        # moment — the window is there when the client (re)connects.
        __gd_chosen="$DISPLAY"
        __gd_rdp_connected || \
            echo "[gui-display] NOTE: DISPLAY=$DISPLAY is an xrdp session with no RDP client connected right now — the window becomes visible when the client reconnects" >&2
    elif __gd_has_monitor "$DISPLAY"; then
        __gd_chosen="$DISPLAY"
        __gd_wake_monitor "$__gd_chosen"
    else
        echo "[gui-display] DISPLAY=$DISPLAY is the console but NO monitor is attached — switching to a visible display" >&2
    fi
fi

if [ -z "$__gd_chosen" ]; then
    __gd_cand=$(__gd_console_display)
    if [ -n "$__gd_cand" ] && __gd_has_monitor "$__gd_cand"; then
        __gd_chosen="$__gd_cand"
        __gd_wake_monitor "$__gd_chosen"
    else
        __gd_chosen=$(__gd_newest_xrdp)
        [ -n "$__gd_chosen" ] && \
            echo "[gui-display] no viewable console monitor; using xrdp display $__gd_chosen" >&2
    fi
fi

if [ -z "$__gd_chosen" ]; then
    echo "[gui-display] ERROR: no reachable X display found." >&2
    echo "[gui-display]   - run on the device with a desktop session," >&2
    echo "[gui-display]   - connect via RDP and set GUI_DISPLAY=:10," >&2
    echo "[gui-display]   - connect via SSH with xpra installed (auto-used), " >&2
    echo "[gui-display]   - or use --headless (gate/CI mode)." >&2
    return 1 2>/dev/null
    exit 1
fi

export DISPLAY="$__gd_chosen"
if __gd_is_xrdp "$__gd_chosen"; then
    unset XAUTHORITY  # xrdp sessions keep their cookie in ~/.Xauthority
elif [ "$__gd_chosen" = "$__gd_xpra_display" ]; then
    unset XAUTHORITY  # xpra's own X server needs no cookie
else
    [ -f "$__gd_xauth_gdm" ] && export XAUTHORITY="$__gd_xauth_gdm"
fi
if [ "$__gd_chosen" = "$__gd_xpra_display" ]; then
    GUI_DISPLAY_KIND="xpra"
elif __gd_is_xrdp "$__gd_chosen"; then
    GUI_DISPLAY_KIND="xrdp"
elif __gd_has_host "$__gd_chosen"; then
    GUI_DISPLAY_KIND="ssh-x11"
else
    GUI_DISPLAY_KIND="console"
fi
export GUI_DISPLAY_KIND
echo "[gui-display] GUI display=$DISPLAY (kind=$GUI_DISPLAY_KIND)"

# restore the caller's set -u state (we enabled it defensively)
case "$__gd_old_setu" in
    *u*) : ;;                       # caller already had it
    *)   set +u ;;                  # caller did not: give it back
esac
unset __gd_old_setu __gd_chosen __gd_cand __gd_xauth_gdm 2>/dev/null || true
unset -f __gd_norm __gd_has_host __gd_reachable __gd_is_xrdp __gd_rdp_connected \
             __gd_has_monitor __gd_console_display __gd_newest_xrdp \
             __gd_wake_monitor __gd_is_xpra_session __gd_start_xpra \
             __gd_in_ssh __gd_xpra_display 2>/dev/null || true