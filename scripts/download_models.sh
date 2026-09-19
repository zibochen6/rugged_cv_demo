#!/bin/bash
# Download EfficientTAM checkpoints from HuggingFace.
cd "$(dirname "$0")/.."
mkdir -p checkpoints
BASE="https://huggingface.co/yunyangx/efficient-track-anything/resolve/main"
MODELS=${@:-efficienttam_ti_512x512 efficienttam_s_512x512}
for m in $MODELS; do
  if [ -s "checkpoints/$m.pt" ]; then
    echo "present: checkpoints/$m.pt"
    continue
  fi
  echo "downloading $m.pt ..."
  wget -nv -O "checkpoints/$m.pt" "$BASE/$m.pt" || { echo "FAILED $m"; exit 1; }
done
echo "--- checkpoints ---"
ls -lh checkpoints/*.pt

# Depth Anything V2 metric depth (Phase 10A+). HF-transformers format.
# NOTE: the original (non-hf) metric repos were removed upstream; only the
# "-hf" converted repos are public as of 2025-09.
DEPTH_REPO="depth-anything/Depth-Anything-V2-Metric-Indoor-Small-hf"
if [ -s checkpoints/depth_anything_v2_metric_indoor_small/model.safetensors ]; then
  echo "present: checkpoints/depth_anything_v2_metric_indoor_small"
else
  echo "downloading $DEPTH_REPO ..."
  .venv/bin/python - "$DEPTH_REPO" <<'EOF'
import sys
from huggingface_hub import snapshot_download
print(snapshot_download(sys.argv[1], local_dir="checkpoints/depth_anything_v2_metric_indoor_small"))
EOF
fi
