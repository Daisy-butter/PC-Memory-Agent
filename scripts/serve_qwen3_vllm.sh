#!/usr/bin/env bash
# Local text agent. Qwen3-8B does not read pixels or waveforms; media tools run at index time.
# Pin vLLM 0.19 (torch 2.10 / CUDA 12.8). This machine's driver is 570.86 and cannot run CUDA 13 wheels.
set -euo pipefail
export HF_HOME="${HF_HOME:-/SSD_DISK/users/wuruihan/huggingface}"
export HUGGINGFACE_HUB_CACHE="${HF_HOME}/hub"
export HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"
export TRANSFORMERS_OFFLINE="${TRANSFORMERS_OFFLINE:-1}"
export CUDA_VISIBLE_DEVICES="${PCM_CUDA_DEVICE:-1}"
if [[ -z "${PCM_VLLM_MODEL:-}" && -f /SSD_DISK/users/wuruihan/models/Qwen3-8B/config.json ]]; then
  export PCM_VLLM_MODEL="/SSD_DISK/users/wuruihan/models/Qwen3-8B"
else
  export PCM_VLLM_MODEL="${PCM_VLLM_MODEL:-Qwen/Qwen3-8B}"
fi
PYTHON="${PCM_VLLM_PYTHON:-/SSD_DISK/users/wuruihan/venvs/pcmemory/bin/python}"
PORT="${PCM_VLLM_PORT:-8001}"
exec "$PYTHON" -m vllm.entrypoints.openai.api_server \
  --model "$PCM_VLLM_MODEL" \
  --host 127.0.0.1 \
  --port "$PORT" \
  --max-model-len "${PCM_MAX_MODEL_LEN:-8192}" \
  --gpu-memory-utilization "${PCM_GPU_MEMORY_UTILIZATION:-0.85}" \
  --dtype auto \
  --trust-remote-code
