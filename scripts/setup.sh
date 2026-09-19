#!/bin/bash
# Bootstrap the Visual Hub environment: .venv, python dependencies, the
# EfficientTAM editable install (the venv egg-link that backend/app/segment
# imports at runtime) and the tracker checkpoint.
#
# Idempotent. Never uses sudo, never touches system CUDA, and never replaces the
# system OpenCV build.
#
# Device: reComputer Rugged J401 / Jetson Orin NX 16GB / JetPack 5.1.3
# (L4T R35.5.0), Python 3.8. torch/torchvision must come from the NVIDIA jp5
# redist wheels, and the venv reuses the system python3-opencv (GStreamer
# enabled). See docs/archive/MIGRATION.md for the full adaptation log.
set -e
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
VENV="$ROOT/.venv"
SP="$("$VENV/bin/python" -c 'import site; print(site.getsitepackages()[0])' 2>/dev/null || true)"

echo "============================================================"
echo " Visual Hub setup"
echo " root: $ROOT"
echo "============================================================"

# 0. platform sanity
if [ ! -f /etc/nv_tegra_release ]; then
  echo "FAIL: not a Jetson device (no /etc/nv_tegra_release). Aborting."
  exit 1
fi
echo "[0] Jetson OK: $(head -1 /etc/nv_tegra_release)"
command -v python3 >/dev/null || { echo "FAIL: python3 not found"; exit 1; }

# 1. venv + packaging tools
if [ ! -x "$VENV/bin/python" ]; then
  echo "[1] create venv (.venv)"
  python3 -m venv "$VENV"
fi
echo "[1] upgrade pip/setuptools/wheel"
"$VENV/bin/python" -m pip install --upgrade pip setuptools wheel >/dev/null

# 2. torch/torchvision gate. JetPack 5 needs the NVIDIA redist torch wheel plus a
#    source-built cp38 torchvision; a generic PyPI/JetPack-6 wheel breaks the
#    working CUDA runtime, so this script refuses to guess.
if ! "$VENV/bin/python" -c 'import torch, torchvision' >/dev/null 2>&1; then
  echo "[2] FAIL: torch/torchvision missing from $VENV" >&2
  echo "    Install NVIDIA's jp5 redist wheel (torch 2.1.0a0+nv23.6) and a cp38" >&2
  echo "    source build of torchvision first, then re-run this script." >&2
  echo "    Steps: docs/archive/MIGRATION.md" >&2
  exit 1
fi
echo "[2] torch $("$VENV/bin/python" -c 'import torch; print(torch.__version__)')"

# 3. python dependencies (OpenCV is deliberately not a pip dependency here)
echo "[3] install backend requirements (fastapi/uvicorn/httpx/pyyaml)"
"$VENV/bin/pip" install --progress-bar off -r backend/requirements.txt
echo "[3] install Jetson runtime requirements (hydra/iopath/transformers/mediapipe)"
"$VENV/bin/pip" install --progress-bar off -r requirements-jetson.txt

# 4. EfficientTAM. The repo-level third_party/ tree is git-ignored, but the
#    editable install it creates (egg-link inside the venv) is what
#    backend/app/segment/model.py resolves `efficient_track_anything` from.
if [ ! -d "$ROOT/third_party/EfficientTAM/efficient_track_anything" ]; then
  echo "[4] clone EfficientTAM -> third_party/EfficientTAM"
  mkdir -p "$ROOT/third_party"
  git clone --depth 1 https://github.com/yformer/EfficientTAM.git "$ROOT/third_party/EfficientTAM"
else
  echo "[4] EfficientTAM sources present"
fi
echo "[4] editable install (BUILD_CUDA=0, --no-build-isolation, --no-deps)"
Efficient_Track_Anything_BUILD_CUDA=0 \
  "$VENV/bin/pip" install -e "$ROOT/third_party/EfficientTAM" --no-build-isolation --no-deps

# 5. cv2 Qt fonts: the venv copy of system OpenCV ships no fonts, and every
#    cv2 window run floods stderr with QFontDatabase warnings without them.
if [ -n "$SP" ] && [ -d /usr/share/fonts/truetype/dejavu ]; then
  mkdir -p "$SP/cv2/qt/fonts"
  cp -n /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf \
    /usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf \
    /usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf \
    "$SP/cv2/qt/fonts/" 2>/dev/null || true
  echo "[5] cv2 Qt fonts -> $SP/cv2/qt/fonts"
fi

# 6. tracker checkpoint
if [ ! -s "$ROOT/checkpoints/efficienttam_ti_512x512.pt" ]; then
  echo "[6] download checkpoint (efficienttam_ti_512x512.pt, ~69MB)"
  mkdir -p "$ROOT/checkpoints"
  wget -nv -O "$ROOT/checkpoints/efficienttam_ti_512x512.pt" \
    "https://huggingface.co/yunyangx/efficient-track-anything/resolve/main/efficienttam_ti_512x512.pt"
else
  echo "[6] checkpoint present"
fi

# 7. CUDA gate
echo "[7] verify torch CUDA gate"
"$VENV/bin/python" - <<'PY'
import torch
print("   torch", torch.__version__, "| cuda", torch.version.cuda,
      "| avail", torch.cuda.is_available(),
      "| dev", torch.cuda.get_device_name(0) if torch.cuda.is_available() else "N/A")
assert torch.cuda.is_available(), "CUDA not available — setup failed"
x = torch.randn((2048, 2048), device="cuda")
y = x @ x
torch.cuda.synchronize()
print("   matmul ok", tuple(y.shape), "-> GATE: PASS")
PY

echo
echo "============================================================"
echo " setup DONE. Next:"
echo "   models:  ./scripts/install_dms_models.sh    # DMS/PPE + person detector"
echo "            ./scripts/build_engine.sh 518      # rear-warning depth engine"
echo "   verify:  ./scripts/verify_env.sh"
echo "   start:   ./scripts/run_visual_hub.sh        (systemd: systemctl start visual-hub)"
echo "   stop:    ./scripts/run_visual_hub.sh stop"
echo "============================================================"