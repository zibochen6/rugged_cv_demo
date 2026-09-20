#!/usr/bin/env bash
# Install / refresh the Visual Hub deployment artifacts on the Jetson.
#
# Idempotent and safe to re-run. Validates the sudoers drop-in BEFORE installing
# it, never starts or stops a service, and never installs packages (it only
# reports a missing logrotate).
#
#   sudo ./deploy/install.sh                 # install/refresh everything
#   sudo ./deploy/install.sh --dry-run       # print actions, change nothing
#   sudo ./deploy/install.sh --no-desktop    # skip the ~/Desktop entry
#   sudo ./deploy/install.sh --no-sudoers    # skip the passwordless sudo drop-in
#
# Reference paths (/home/seeed/workspace/seg_demo, user seeed) are rewritten to this
# checkout, so a clone at any location installs correctly.
#
# Installs:
#   /etc/systemd/system/visual-hub.service
#   /etc/systemd/system/poe-pse.service       (ordering-cycle note lives here)
#   /etc/systemd/system/poe-cam-net.service
#   /usr/local/bin/poe-cam-up.sh
#   /etc/logrotate.d/visual-hub
#   /etc/sudoers.d/seeed-nopasswd             (validated with visudo -c -f)
#   /etc/seg-demo/visual-hub.env              (only when absent, from the example)
#   ~seeed/Desktop/visual-hub.desktop         (trusted, Exec re-rooted)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEPLOY="$ROOT/deploy"
TARGET_USER="${HUB_USER:-seeed}"
TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"

DRY_RUN=0
DO_DESKTOP=1
DO_SUDOERS=1
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --no-desktop) DO_DESKTOP=0 ;;
    --no-sudoers) DO_SUDOERS=0 ;;
    -h | --help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

if [ "$(id -u)" != "0" ] && [ "$DRY_RUN" = 0 ]; then
  echo "must run as root (sudo $0)" >&2
  exit 1
fi

say() { printf '%s\n' "$*"; }
act() {
  say "  $*"
  [ "$DRY_RUN" = 1 ] && return 0
  "$@"
}
install_file() {  # install_file <mode> <src> <dst>
  act install -m "$1" -o root -g root "$2" "$3"
}

# install_templated <mode> <src> <dst>: rewrite the reference deployment path
# (and the target user) so a clone at any location installs correctly.
install_templated() {
  if [ "$DRY_RUN" = 1 ]; then
    say "  sed s#/home/seeed/workspace/seg_demo#$ROOT#g; su seeed -> su $TARGET_USER  ($2 -> $3)"
    return 0
  fi
  sed -e "s#/home/seeed/workspace/seg_demo#$ROOT#g" \
      -e "s#\bsu seeed seeed\b#su $TARGET_USER $TARGET_USER#g" "$2" >/tmp/install-templated.$$
  install -m "$1" -o root -g root /tmp/install-templated.$$ "$3"
  rm -f /tmp/install-templated.$$
}

say "Visual Hub deploy @ $ROOT  (user=$TARGET_USER home=$TARGET_HOME dry-run=$DRY_RUN)"

say "[1/7] systemd units"
install_templated 0644 "$DEPLOY/visual-hub.service" /etc/systemd/system/visual-hub.service
install_file 0644 "$DEPLOY/poe-pse.service" /etc/systemd/system/poe-pse.service
install_file 0644 "$DEPLOY/poe-cam-net.service" /etc/systemd/system/poe-cam-net.service
install_file 0755 "$DEPLOY/poe-cam-up.sh" /usr/local/bin/poe-cam-up.sh
act systemctl daemon-reload
for unit in poe-pse.service poe-cam-net.service visual-hub.service; do
  act systemctl enable "$unit"
done

say "[2/7] logrotate"
if ! command -v logrotate >/dev/null 2>&1; then
  say "  WARNING: logrotate is not installed; logs/*.log and logs/*.jsonl will grow unbounded."
  say "           fix: sudo apt-get install -y logrotate"
else
  install_templated 0644 "$DEPLOY/visual-hub.logrotate" /etc/logrotate.d/visual-hub
  if [ "$DRY_RUN" = 0 ]; then
    /usr/sbin/logrotate -d /etc/logrotate.d/visual-hub >/dev/null 2>&1 \
      && say "  logrotate config parses OK" \
      || say "  WARNING: logrotate config failed its self-test"
  fi
fi

say "[3/7] protected environment file"
if [ -f /etc/seg-demo/visual-hub.env ]; then
  say "  /etc/seg-demo/visual-hub.env exists; left untouched (RTSP credentials are not managed here)"
else
  act install -d -m 0755 -o root -g root /etc/seg-demo
  say "  installing the TEMPLATE — fill in FRONT_CAMERA_URL / REAR_CAMERA_URL afterwards"
  install_file 0600 "$DEPLOY/visual-hub.env.example" /etc/seg-demo/visual-hub.env
fi

say "[4/7] desktop entry"
if [ "$DO_DESKTOP" = 1 ]; then
  if [ ! -d "$TARGET_HOME/Desktop" ]; then
    say "  no $TARGET_HOME/Desktop; skipped"
  else
    if [ "$DRY_RUN" = 1 ]; then
      say "  sed s#/home/seeed/workspace/seg_demo#$ROOT#g visual-hub.desktop > $TARGET_HOME/Desktop/visual-hub.desktop"
    else
      sed "s#/home/seeed/workspace/seg_demo#$ROOT#g" "$DEPLOY/visual-hub.desktop" >/tmp/visual-hub.desktop.$$
      install -m 0755 -o "$TARGET_USER" -g "$TARGET_USER" \
        /tmp/visual-hub.desktop.$$ "$TARGET_HOME/Desktop/visual-hub.desktop"
      rm -f /tmp/visual-hub.desktop.$$
      if command -v gio >/dev/null 2>&1; then
        tuid="$(id -u "$TARGET_USER")"
        if su - "$TARGET_USER" -c "XDG_RUNTIME_DIR=/run/user/$tuid DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$tuid/bus gio set '$TARGET_HOME/Desktop/visual-hub.desktop' metadata::trusted true" >/dev/null 2>&1; then
          say "  desktop icon marked trusted"
        elif gio info "$TARGET_HOME/Desktop/visual-hub.desktop" 2>/dev/null | grep -q "metadata::trusted: true"; then
          say "  desktop icon already trusted"
        else
          say "  note: could not mark the icon trusted (right-click the icon > Allow Launching once)"
        fi
      fi
    fi
  fi
fi

say "[5/7] passwordless operator sudo"
if [ "$DO_SUDOERS" = 1 ]; then
  if visudo -c -f "$DEPLOY/seeed-nopasswd.sudoers" >/dev/null 2>&1; then
    say "  sudoers drop-in parsed OK"
    install_file 0440 "$DEPLOY/seeed-nopasswd.sudoers" /etc/sudoers.d/seeed-nopasswd
    if [ "$DRY_RUN" = 0 ]; then
      visudo -c >/dev/null 2>&1 && say "  whole sudoers config parses OK" \
        || { say "  FATAL: sudoers now fails to parse; removing the drop-in" >&2
             rm -f /etc/sudoers.d/seeed-nopasswd; exit 1; }
    fi
  else
    say "  FATAL: deploy/seeed-nopasswd.sudoers does not parse; nothing installed" >&2
    exit 1
  fi
fi

say "[6/7] sanity: the units that must come up at boot"
if [ "$DRY_RUN" = 0 ]; then
  if systemctl show poe-pse.service -p After | grep -q "multi-user.target"; then
    say "  WARNING: poe-pse.service is ordered After=multi-user.target again — that is the"
    say "           boot ordering cycle which deletes the visual-hub start job every boot"
  else
    say "  poe-pse.service has no edge back to multi-user.target (cycle-free)"
  fi
fi

say "[7/7] done. Service state is unchanged; start with:"
say "  sudo systemctl start visual-hub      # or ./scripts/run_visual_hub.sh"
say "  ./scripts/run_visual_hub.sh stop     # stop and release cameras/GPU"
say "  ./scripts/run_visual_hub.sh gui      # desktop entry: kiosk UI on a visible display"
say "  reboot                               # verify boot auto-start (hub + poe-cam-net)"