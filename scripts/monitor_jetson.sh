#!/bin/bash
# Sample Jetson stats -> logs/tegrastats.log. Prefers `sudo tegrastats`
# (full GPU util / power / temp); falls back to no-sudo /sys reads if sudo is
# unavailable (Group A default). Run in background; kill (SIGTERM/SIGKILL) to stop.
# Usage: scripts/monitor_jetson.sh [logfile]
# The default log path resolves against the repo root, so running this from any
# cwd cannot create a stray <cwd>/logs/tegrastats.log.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG="${1:-$ROOT/logs/tegrastats.log}"
mkdir -p "$(dirname "$LOG")"
: > "$LOG"

use_tegrastats=0
if sudo -n true 2>/dev/null; then use_tegrastats=1; fi

while true; do
    ts=$(date +%H:%M:%S)
    if [ "$use_tegrastats" = "1" ]; then
        line=$(sudo -n tegrastats 2>/dev/null | head -1)
        echo "[$ts] $line" >> "$LOG"
    else
        temps=""
        for z in /sys/class/thermal/thermal_zone*/temp; do
            [ -r "$z" ] || continue
            zn=$(basename "$(dirname "$z")")
            temps="$temps ${zn}=$(cat "$z" 2>/dev/null)"
        done
        ram=$(awk '/MemAvailable/{print "MemAvail="$2"MB"}' /proc/meminfo 2>/dev/null)
        gpu_load=""
        [ -r /sys/devices/platform/gpu.0/load ] && gpu_load=" gpu_load=$(cat /sys/devices/platform/gpu.0/load 2>/dev/null)"
        echo "[$ts] no-sudo temps:$temps $ram$gpu_load" >> "$LOG"
    fi
    sleep 1
done
