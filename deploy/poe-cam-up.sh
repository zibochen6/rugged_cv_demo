#!/bin/bash
# Discover directly connected PoE cameras and stop once the expected pair is online.
set -u

IFACES="${POE_IFACES:-eth0 eth1 eth2 eth3 eth4}"
SUBNETS="${POE_SUBNETS:-192.168.137 192.168.138 192.168.1 192.168.0 192.168.100}"
HOSTS="${POE_HOSTS:-20 64 10 108}"
EXPECTED_COUNT="${POE_EXPECTED_COUNT:-2}"
DEADLINE=$(( $(date +%s) + ${POE_DISCOVERY_TIMEOUT_S:-150} ))

for iface in $IFACES; do
  ip link set "$iface" up 2>/dev/null || true
done

declare -A FOUND
log() { logger -t poe-cam-up -- "$*"; echo "$*"; }

found_count() {
  local count=0 subnet
  for subnet in $SUBNETS; do
    [ -n "${FOUND[$subnet]:-}" ] && count=$((count + 1))
  done
  echo "$count"
}

while [ "$(found_count)" -lt "$EXPECTED_COUNT" ] && [ "$(date +%s)" -lt "$DEADLINE" ]; do
  missing=""
  for subnet in $SUBNETS; do
    [ -n "${FOUND[$subnet]:-}" ] || missing="$missing $subnet"
  done

  for iface in $IFACES; do
    [ "$(cat "/sys/class/net/$iface/carrier" 2>/dev/null)" = "1" ] || continue
    for subnet in $missing; do
      [ -n "${FOUND[$subnet]:-}" ] && continue
      ip addr add "$subnet.100/24" dev "$iface" 2>/dev/null || true
      for host in $HOSTS; do
        if ping -c1 -W1 -I "$iface" "$subnet.$host" >/dev/null 2>&1; then
          FOUND[$subnet]="$iface"
          ip addr add "$subnet.1/24" dev "$iface" 2>/dev/null || true
          log "camera $subnet.$host answered on $iface"
          break
        fi
      done
      if [ -z "${FOUND[$subnet]:-}" ]; then
        ip addr del "$subnet.100/24" dev "$iface" 2>/dev/null || true
      fi
      [ "$(found_count)" -ge "$EXPECTED_COUNT" ] && break 2
    done
  done
  sleep 5
done

for subnet in $SUBNETS; do
  if [ -n "${FOUND[$subnet]:-}" ]; then
    log "subnet $subnet.0/24 -> ${FOUND[$subnet]} (kept)"
  fi
done

count="$(found_count)"
if [ "$count" -lt "$EXPECTED_COUNT" ]; then
  log "warning: found $count/$EXPECTED_COUNT expected PoE cameras before timeout"
fi
