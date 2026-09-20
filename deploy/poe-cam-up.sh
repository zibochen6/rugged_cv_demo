#!/bin/bash
# PoE camera subnet provisioning for the Visual Hub.
#
#   poe-cam-up.sh            one provisioning pass, then exit
#   poe-cam-up.sh --loop     stay resident and re-provision whenever a PoE port's
#                            link changes (this is what poe-cam-net.service runs)
#
# Why this is resident instead of a boot-time oneshot
# ---------------------------------------------------
# Both PoE camera ports are powered by this board, so a camera's 100M link can
# appear seconds *or minutes* after multi-user.target -- the camera is still
# booting, or its cable is (re)plugged later. The earlier implementation ran
# exactly once, inside a fixed 150 s boot window, and retracted every candidate
# address whose camera had not answered inside that window. A camera that
# brought its link up after the window therefore left its port with no IPv4 at
# all, and nothing ever re-provisioned it: the RTSP URL in
# /etc/seg-demo/visual-hub.env had no route, so the hub sat on
# "Connecting to camera..." with CAPTURE 0.0 FPS.
#
# Measured 2026-09-19 on the J401: one boot brought eth2 up at uptime 2176 s
# while the discovery window had closed at uptime ~200 s ("found 0/2"), so eth2
# was left with only a link-local IPv6 address and the front camera stayed
# unreachable until someone ran `ip addr add` by hand. Keeping the camera
# subnets provisioned is a continuous responsibility, so it runs as a watcher.
#
# Address model, per camera subnet S on the PoE port that reaches the camera:
#     S.100/24   local address used to dial the camera (RTSP)
#     S.1/24     companion alias, the legacy FRONT_HOST_IP / REAR_HOST_IP
#
# Subnets the hub is configured to dial are derived from FRONT_CAMERA_URL /
# REAR_CAMERA_URL, so provisioning cannot drift from what the hub actually
# opens. A configured subnet is assigned as soon as a PoE port has carrier --
# even before the camera has answered -- so a camera that boots later already
# finds a route waiting for it. Probing then pins each subnet to the port that
# really answered, and retracts it from every other PoE port, so two ports can
# never hold the same connected route. Historical extra subnets are only ever
# kept when a camera actually answers on them.
set -u

IFACES="${POE_IFACES:-eth0 eth1 eth2 eth3 eth4}"
SUBNETS="${POE_SUBNETS:-192.168.137 192.168.138 192.168.1 192.168.0 192.168.100}"
HOSTS="${POE_HOSTS:-20 64 10 108}"
LOCAL_HOST="${POE_LOCAL_HOST:-100}"
PEER_HOST="${POE_PEER_HOST:-1}"
PING_WAIT="${POE_PING_WAIT:-1}"
# How often the resident watcher re-reads link state (cheap), and how often it
# re-probes while a configured camera is still missing (not cheap).
POLL_SECONDS="${POE_POLL_SECONDS:-3}"
RESCAN_SECONDS="${POE_RESCAN_SECONDS:-30}"

MODE=pass
[ "${1:-}" = "--loop" ] && MODE=loop

log() { logger -t poe-cam-up -- "$*" 2>/dev/null || true; echo "$*"; }

# rtsp://user:pass@192.168.137.20:554/  ->  192.168.137.20
host_of_url() {
  local h="${1:-}"
  h="${h#*://}"; h="${h##*@}"; h="${h%%/*}"; h="${h%%:*}"
  case "$h" in
    [0-9]*.[0-9]*.[0-9]*.[0-9]*) printf '%s\n' "$h" ;;
  esac
}

CONFIGURED_HOSTS=""
for _u in "${FRONT_CAMERA_URL:-}" "${REAR_CAMERA_URL:-}"; do
  _h="$(host_of_url "$_u")"
  [ -n "$_h" ] && CONFIGURED_HOSTS="$CONFIGURED_HOSTS $_h"
done
CONFIGURED_HOSTS="${CONFIGURED_HOSTS# }"

CONFIGURED_SUBNETS=""
for _h in $CONFIGURED_HOSTS; do
  _s="${_h%.*}"
  case " $CONFIGURED_SUBNETS " in
    *" $_s "*) ;;
    *) CONFIGURED_SUBNETS="$CONFIGURED_SUBNETS $_s" ;;
  esac
done
CONFIGURED_SUBNETS="${CONFIGURED_SUBNETS# }"

CARRIERS=""
LAST_SIGNATURE="__init__"
LAST_RETRACT="__init__"
LAST_SWEEP=0
SETTLED=0
declare -A PIN=()

carrier_ifaces() {
  local i
  for i in $IFACES; do
    [ "$(cat "/sys/class/net/$i/carrier" 2>/dev/null)" = "1" ] && printf '%s\n' "$i"
  done
  return 0
}

have_addr() { ip -o -4 addr show dev "$1" 2>/dev/null | awk '{print $4}' | grep -qx "$2"; }
add_addr() { have_addr "$1" "$2" || ip addr add "$2" dev "$1" 2>/dev/null || true; }
del_addr() { have_addr "$1" "$2" && ip addr del "$2" dev "$1" 2>/dev/null; return 0; }

# Candidate host octets for a subnet: the configured camera first, then the
# historical guesses.
hosts_for() {
  local s="$1" h out=""
  for h in $CONFIGURED_HOSTS; do
    [ "${h%.*}" = "$s" ] && out="$out ${h##*.}"
  done
  for h in $HOSTS; do
    case " $out " in *" $h "*) ;; *) out="$out $h" ;; esac
  done
  printf '%s\n' "${out# }"
}

# Probe one subnet over one port. Pings run concurrently so a subnet full of
# silent addresses still costs only PING_WAIT, not len(hosts)*PING_WAIT.
REPLIED=""
probe_subnet() {
  local i="$1" s="$2" d h
  d="$(mktemp -d)" || return 1
  for h in $(hosts_for "$s"); do
    ( ping -c1 -W"$PING_WAIT" -I "$i" "$s.$h" >/dev/null 2>&1 && printf '%s\n' "$s.$h" >"$d/$h" ) &
  done
  wait
  REPLIED="$(cat "$d"/* 2>/dev/null | head -n1)"
  rm -rf "$d"
  [ -n "$REPLIED" ]
}

# Drop every address this script owns from a port.
retract_all() {
  local i="$1" s
  for s in $SUBNETS $CONFIGURED_SUBNETS; do
    del_addr "$i" "$s.$LOCAL_HOST/24"
    del_addr "$i" "$s.$PEER_HOST/24"
  done
}

# Every configured subnet already carries a local address on some live port.
settled_on() {
  local carriers="$1" s i found
  for s in $CONFIGURED_SUBNETS; do
    found=0
    for i in $carriers; do
      have_addr "$i" "$s.$LOCAL_HOST/24" && { found=1; break; }
    done
    [ "$found" = 1 ] || return 1
  done
  return 0
}

full_sweep() {
  local i j s cidr owner first keep seen=""
  for s in $SUBNETS $CONFIGURED_SUBNETS; do
    case " $seen " in *" $s "*) continue ;; esac
    seen="$seen $s"
    cidr="$s.$LOCAL_HOST/24"
    owner=""

    # Hold the address on exactly one port while probing it, so a probe can
    # never be answered over the wrong interface.
    for i in $CARRIERS; do
      for j in $CARRIERS; do [ "$j" = "$i" ] || del_addr "$j" "$cidr"; done
      add_addr "$i" "$cidr"
      if probe_subnet "$i" "$s"; then
        owner="$i"
        break
      fi
    done

    if [ -n "$owner" ]; then
      add_addr "$owner" "$s.$PEER_HOST/24"
      for j in $CARRIERS; do [ "$j" = "$owner" ] || del_addr "$j" "$s.$PEER_HOST/24"; done
      if [ "${PIN[$s]:-}" != "$owner" ]; then
        log "camera $REPLIED answered on $owner ($s.0/24)"
        PIN[$s]="$owner"
      fi
    else
      # No camera answered yet. A configured subnet still gets its address on
      # one carrier port so a camera that is still booting finds a route
      # waiting; historical extras are retracted so they cannot shadow a real
      # network elsewhere on this host.
      first="$(printf '%s\n' "$CARRIERS" | head -n1)"
      keep=""
      case " $CONFIGURED_SUBNETS " in *" $s "*) keep="$first" ;; esac
      for j in $CARRIERS; do [ "$j" = "$keep" ] || del_addr "$j" "$cidr"; done
      for j in $CARRIERS; do del_addr "$j" "$s.$PEER_HOST/24"; done
      if [ "${PIN[$s]:-}" != "-" ]; then
        if [ -n "$keep" ]; then
          log "no camera on $s.0/24 yet; holding $cidr on $keep for a late boot"
        else
          log "no camera on $s.0/24 on any PoE port"
        fi
        PIN[$s]="-"
      fi
    fi
  done

  # Settle only when every configured camera actually answered: otherwise the
  # next passes must keep re-probing so a camera that boots later still gets
  # pinned to the right port.
  SETTLED=1
  for s in $CONFIGURED_SUBNETS; do
    [ "${PIN[$s]:-}" != "-" ] || { SETTLED=0; break; }
  done
}

provision_pass() {
  local i now signature sweep=0
  for i in $IFACES; do ip link set "$i" up 2>/dev/null || true; done

  now="$(date +%s)"
  CARRIERS="$(carrier_ifaces)"
  signature="$(printf '%s' "$CARRIERS" | tr '\n' ',')"

  if [ "$signature" != "$LAST_SIGNATURE" ]; then
    # A port appeared or disappeared: always re-derive.
    sweep=1
  elif [ "$SETTLED" = 1 ]; then
    # Was healthy; only re-derive if the provisioned addresses went away.
    settled_on "$CARRIERS" || sweep=1
  elif [ $((now - LAST_SWEEP)) -ge "$RESCAN_SECONDS" ]; then
    # A configured camera has not answered yet: keep looking, slowly.
    sweep=1
  fi
  [ "$sweep" = 1 ] || return 0

  LAST_SWEEP="$now"

  # A port without carrier cannot reach a camera: drop our addresses there so a
  # pulled cable never leaves a stale connected route behind. Only needed when
  # the link picture actually changed.
  if [ "$signature" != "$LAST_RETRACT" ]; then
    for i in $IFACES; do
      case ",$signature" in *",$i,"*) continue ;; esac
      retract_all "$i"
    done
    LAST_RETRACT="$signature"
  fi

  LAST_SIGNATURE="$signature"
  if [ -z "$CARRIERS" ]; then
    SETTLED=0
    return 0
  fi

  full_sweep
  return 0
}

if [ "$MODE" = pass ]; then
  if [ -n "$CONFIGURED_SUBNETS" ]; then
    log "configured camera subnets: $CONFIGURED_SUBNETS (hosts: $CONFIGURED_HOSTS)"
  else
    log "warning: no FRONT_CAMERA_URL / REAR_CAMERA_URL in the environment; discovery only"
  fi
  provision_pass
  if [ -z "$CARRIERS" ]; then
    log "warning: no PoE port has link; check the PSE power hold and the camera cabling"
  fi
  exit 0
fi

log "watching PoE ports (${IFACES// /, }); re-provisioning whenever a port's link changes"
while true; do
  provision_pass
  sleep "$POLL_SECONDS"
done