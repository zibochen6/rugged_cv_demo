#!/bin/bash
# Locate (or download) the EfficientTAM-Ti 512x512 checkpoint.
#
# The checkpoint ALREADY ships with the project at <repo>/checkpoints/
# (efficienttam_ti_512x512.pt, ~72 MB) — this script only falls back to
# downloading it from HuggingFace when that copy is missing.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NAME="efficienttam_ti_512x512.pt"
DIR="$ROOT/backend/app/segment/checkpoints"
EXISTING="$ROOT/checkpoints/$NAME"
URL="https://huggingface.co/yunyangx/efficient-track-anything/resolve/main/${NAME}"
mkdir -p "$DIR"

if [ -s "$DIR/$NAME" ]; then
  echo "checkpoint already present: $DIR/$NAME"
  exit 0
fi
if [ -s "$EXISTING" ]; then
  cp "$EXISTING" "$DIR/$NAME"
  echo "copied from existing: $EXISTING -> $DIR/$NAME"
  exit 0
fi
echo "downloading $URL -> $DIR/$NAME ..."
curl -sL --retry 5 --retry-delay 5 -o "$DIR/$NAME" "$URL"
echo "done: $(du -h "$DIR/$NAME" | cut -f1)"