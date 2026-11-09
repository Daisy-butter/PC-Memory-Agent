from __future__ import annotations

import os
from pathlib import Path

DEFAULT_PYTHON = "/SSD_DISK/users/wuruihan/venvs/pcmemory/bin/python"
DEFAULT_DATASET = Path("/SSD_DISK/users/wuruihan/datasets/FileSystem_wrh")
DEFAULT_MODEL = "Qwen/Qwen3-8B"


def vllm_command(python: str = DEFAULT_PYTHON, model: str = DEFAULT_MODEL, port: int = 8001) -> list[str]:
    return [
        python, "-m", "vllm.entrypoints.openai.api_server",
        "--model", model,
        "--host", "127.0.0.1",
        "--port", str(port),
        "--max-model-len", os.getenv("PCM_MAX_MODEL_LEN", "8192"),
        "--gpu-memory-utilization", os.getenv("PCM_GPU_MEMORY_UTILIZATION", "0.85"),
        "--dtype", "auto",
        "--trust-remote-code",
    ]


def serve_env(gpu: str = "1") -> dict[str, str]:
    env = os.environ.copy()
    env["CUDA_VISIBLE_DEVICES"] = gpu
    env["HF_HOME"] = os.getenv("HF_HOME", "/SSD_DISK/users/wuruihan/huggingface")
    env["HUGGINGFACE_HUB_CACHE"] = str(Path(env["HF_HOME"]) / "hub")
    env.setdefault("PCM_VLLM_MODEL", DEFAULT_MODEL)
    return env
