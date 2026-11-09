from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

LOCAL_QWEN3 = Path("/SSD_DISK/users/wuruihan/models/Qwen3-8B")


def vllm_base_url() -> str:
    return os.getenv("PCM_VLLM_BASE_URL", "http://127.0.0.1:8001/v1").rstrip("/")


def vllm_model() -> str:
    override = os.getenv("PCM_VLLM_MODEL")
    if override:
        return override
    if (LOCAL_QWEN3 / "config.json").is_file():
        return str(LOCAL_QWEN3)
    return "Qwen/Qwen3-8B"


def chat(messages: list[dict[str, str]], timeout: int = 180) -> str:
    """Call a local OpenAI-compatible vLLM server. Qwen3 thinking is disabled for stable answers."""
    payload = {
        "model": vllm_model(),
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": 1024,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    request = urllib.request.Request(
        vllm_base_url() + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {os.getenv('PCM_VLLM_API_KEY', 'EMPTY')}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode())
    except urllib.error.URLError as error:
        raise ConnectionError(f"无法连接本地 vLLM {vllm_base_url()}：{error}") from error
    text = data["choices"][0]["message"]["content"].strip()
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
