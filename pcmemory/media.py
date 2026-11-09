from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

MAX_MEDIA_CHARS = 20_000
_WHISPER = None


def _tool(name: str) -> str | None:
    """Prefer binaries that actually run on this host. The snap wrapper needs a display and fails headless."""
    candidates = []
    if name in {"ffmpeg", "ffprobe"}:
        candidates.append(Path(f"/snap/ffmpeg/current/usr/bin/{name}"))
    if name == "tesseract":
        candidates.append(Path("/SSD_DISK/users/wuruihan/venvs/tesseract/bin/tesseract"))
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    found = shutil.which(name)
    if found and Path(found).name == name:
        return found
    snap = Path(f"/snap/ffmpeg/current/usr/bin/{name}")
    return str(snap) if snap.exists() else None


def _run(command: list[str], timeout: int = 180) -> bool:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def probe_summary(path: Path) -> str:
    ffprobe = _tool("ffprobe")
    if not ffprobe:
        return ""
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration,format_name,size:stream=codec_type,codec_name,width,height", "-of", "default=nw=1", str(path)],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip()[:2000] if result.returncode == 0 else ""


def _ocr_with_lang(tesseract: str, path: Path, languages: str) -> str | None:
    try:
        result = subprocess.run(
            [tesseract, str(path), "stdout", "-l", languages],
            capture_output=True, text=True, timeout=90, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = result.stdout.strip()
    return text[:MAX_MEDIA_CHARS] if result.returncode == 0 and text else None


def ocr_image(path: Path) -> str | None:
    flag = os.getenv("PCM_ENABLE_OCR", "auto")
    if flag == "0":
        return None
    tesseract = _tool("tesseract")
    if not tesseract:
        return None
    languages = os.getenv("PCM_OCR_LANG", "eng+chi_sim")
    text = _ocr_with_lang(tesseract, path, languages)
    if text:
        return text
    if languages != "eng":
        text = _ocr_with_lang(tesseract, path, "eng")
        if text:
            return text
    converted: Path | None = None
    try:
        from PIL import Image
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
            converted = Path(handle.name)
        with Image.open(path) as image:
            image.convert("RGB").save(converted, "PNG")
        return _ocr_with_lang(tesseract, converted, "eng")
    except Exception:
        return None
    finally:
        if converted is not None:
            converted.unlink(missing_ok=True)


def vision_caption(path: Path) -> str | None:
    """Optional second vLLM (a VL checkpoint). Qwen3-8B itself does not accept images."""
    base = os.getenv("PCM_VISION_BASE_URL", "").rstrip("/")
    if not base:
        return None
    model = os.getenv("PCM_VISION_MODEL", "Qwen/Qwen2.5-VL-3B-Instruct")
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    try:
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        payload = {
            "model": model,
            "temperature": 0.1,
            "max_tokens": 256,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "用中文概括这张图片的主题、可见文字和与文档或项目相关的信息，不超过120字。"},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}},
                ],
            }],
        }
        request = urllib.request.Request(
            base + "/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "Authorization": "Bearer EMPTY"},
        )
        with urllib.request.urlopen(request, timeout=90) as response:
            data = json.loads(response.read().decode())
        text = data["choices"][0]["message"]["content"].strip()
    except (OSError, urllib.error.URLError, KeyError, IndexError, json.JSONDecodeError, ValueError):
        return None
    return text[:1000] if text else None


def _pcm16_mono(path: Path):
    """Decode with ffmpeg. This venv's PyAV cannot open files the way faster-whisper 1.2 expects."""
    ffmpeg = _tool("ffmpeg")
    if not ffmpeg:
        return None
    with tempfile.TemporaryDirectory(prefix="pcm-wav-") as directory:
        wav = Path(directory) / "audio.wav"
        if not _run([ffmpeg, "-y", "-i", str(path), "-t", "600", "-vn", "-ac", "1", "-ar", "16000", str(wav)]):
            return None
        if not wav.exists() or wav.stat().st_size <= 44:
            return None
        import numpy as np
        import wave
        with wave.open(str(wav), "rb") as source:
            if source.getsampwidth() != 2 or source.getnchannels() != 1:
                return None
            frames = source.readframes(source.getnframes())
        if not frames:
            return None
        return np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0


def transcribe_media(path: Path) -> str | None:
    global _WHISPER
    try:
        if path.stat().st_size < 1024:
            return None
    except OSError:
        return None
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return None
    audio = _pcm16_mono(path)
    if audio is None or getattr(audio, "size", 0) < 1600:
        return None
    try:
        if _WHISPER is None:
            name = os.getenv("PCM_WHISPER_MODEL", "small")
            device = os.getenv("PCM_WHISPER_DEVICE", "cpu")
            compute = "int8" if device == "cpu" else "float16"
            _WHISPER = WhisperModel(name, device=device, compute_type=compute)
        language = os.getenv("PCM_WHISPER_LANGUAGE") or None
        segments, _info = _WHISPER.transcribe(audio, language=language, vad_filter=True)
        text = "".join(segment.text for segment in segments).strip()
    except Exception:
        return None
    return text[:MAX_MEDIA_CHARS] if text else None


def _image_header(path: Path) -> str:
    try:
        from PIL import Image
        with Image.open(path) as image:
            return f"[image] format={image.format} size={image.size[0]}x{image.size[1]} mode={image.mode} name={path.name}"
    except Exception:
        return f"[image] {path.name}"


def observe_image(path: Path) -> str:
    parts = [_image_header(path)]
    caption = vision_caption(path)
    if caption:
        parts.append("[画面描述]\n" + caption)
    ocr = ocr_image(path)
    if ocr:
        parts.append("[OCR]\n" + ocr)
    elif not caption:
        parts.append("图片不能被 Qwen3-8B 直接读取。未得到 OCR 或视觉描述，当前只保存图像属性。")
    return "\n".join(parts)[:MAX_MEDIA_CHARS]


def observe_audio(path: Path) -> str:
    parts = [f"[audio] {path.name}"]
    meta = probe_summary(path)
    if meta:
        parts.append(meta)
    transcript = transcribe_media(path)
    if transcript:
        parts.append("[转写]\n" + transcript)
    else:
        try:
            import faster_whisper  # noqa: F401
            parts.append("语音转写没有得到文本。")
        except ImportError:
            parts.append("音频不能被文本模型直接读取。未安装 faster-whisper，当前只保存文件名和容器信息。")
    return "\n".join(parts)[:MAX_MEDIA_CHARS]


def _sample_frames(ffmpeg: str, path: Path, folder: Path) -> list[Path]:
    duration = 0.0
    meta = probe_summary(path)
    for line in meta.splitlines():
        if line.startswith("duration="):
            try:
                duration = float(line.split("=", 1)[1])
            except ValueError:
                duration = 0.0
    times = [max(0.0, duration * ratio) for ratio in (0.1, 0.5, 0.9)] if duration > 1 else [0.5, 2.0, 5.0]
    frames: list[Path] = []
    for index, stamp in enumerate(times):
        target = folder / f"frame_{index}.jpg"
        if _run([ffmpeg, "-y", "-ss", f"{stamp:.3f}", "-i", str(path), "-frames:v", "1", str(target)], timeout=60) and target.exists():
            frames.append(target)
    return frames


def observe_video(path: Path) -> str:
    parts = [f"[video] {path.name}"]
    meta = probe_summary(path)
    if meta:
        parts.append(meta)
    ffmpeg = _tool("ffmpeg")
    if not ffmpeg:
        parts.append("未找到 ffmpeg，无法抽帧或抽取音轨。")
        return "\n".join(parts)[:MAX_MEDIA_CHARS]
    with tempfile.TemporaryDirectory(prefix="pcm-video-") as directory:
        folder = Path(directory)
        wav = folder / "audio.wav"
        if _run([ffmpeg, "-y", "-i", str(path), "-t", "600", "-vn", "-ac", "1", "-ar", "16000", str(wav)]) and wav.exists() and wav.stat().st_size > 44:
            transcript = transcribe_media(wav)
            if transcript:
                parts.append("[音轨转写，最多前10分钟]\n" + transcript)
            else:
                parts.append("音轨已抽出，转写没有得到语音文本。")
        notes = []
        for frame in _sample_frames(ffmpeg, path, folder):
            piece = " ".join(item for item in (vision_caption(frame), ocr_image(frame)) if item)
            notes.append(_image_header(frame) + (f"\n{piece}" if piece else "\n关键帧没有识别出文字。"))
        if notes:
            parts.append("[关键帧]\n" + "\n".join(notes))
    if len(parts) == 1 or (len(parts) == 2 and meta):
        parts.append("视频已记录容器信息。音轨转写或关键帧文字需要 ffmpeg、faster-whisper 或 OCR。")
    return "\n".join(parts)[:MAX_MEDIA_CHARS]
