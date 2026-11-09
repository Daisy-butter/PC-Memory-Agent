#!/usr/bin/env bash
set -euo pipefail
export HF_HOME="${HF_HOME:-/SSD_DISK/users/wuruihan/huggingface}"
export PATH="/SSD_DISK/users/wuruihan/venvs/tesseract/bin:${PATH}"
export PCM_ENABLE_OCR="${PCM_ENABLE_OCR:-auto}"
export PCM_WHISPER_DEVICE="${PCM_WHISPER_DEVICE:-cpu}"
export PCM_WHISPER_MODEL="${PCM_WHISPER_MODEL:-small}"
PYTHON="${PCM_PYTHON:-/SSD_DISK/users/wuruihan/venvs/pcmemory/bin/python}"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="$(command -v python3)"
fi
DATA="${PCM_DATASET_ROOT:-/SSD_DISK/users/wuruihan/datasets/FileSystem_wrh}"
cd "$(dirname "$0")/.."
exec "$PYTHON" -m pcmemory index "$DATA" --max-size-mib "${PCM_MAX_SIZE_MIB:-1024}"
