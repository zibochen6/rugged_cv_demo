#!/bin/bash
# Source this to activate the project venv + Jetson CUDA environment.
#   source scripts/env.sh
_HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$_HERE/.."
export VENV_DIR="$(pwd)/.venv"
export CUDA_HOME="${CUDA_HOME:-/usr/local/cuda}"
export PATH="$CUDA_HOME/bin:$PATH"
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
echo "[env] venv @ $VENV_DIR  (CUDA_HOME=$CUDA_HOME)"