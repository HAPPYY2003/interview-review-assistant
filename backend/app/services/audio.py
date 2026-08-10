from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import filetype
import httpx
from mutagen import File as MutagenFile

from backend.app.config import Settings


SUPPORTED_AUDIO_SUFFIXES = {".mp3", ".m4a", ".wav", ".flac", ".ogg"}
SUPPORTED_AUDIO_MIMES = {
    "audio/mpeg",
    "audio/mp4",
    "video/mp4",
    "audio/x-m4a",
    "audio/wav",
    "audio/x-wav",
    "audio/flac",
    "audio/x-flac",
    "audio/ogg",
    "application/ogg",
}


class AudioInspectionError(ValueError):
    pass


class TranscriptionError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False, status_code: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


@dataclass(frozen=True)
class AudioInspection:
    suffix: str
    mime_type: str
    size_bytes: int
    sha256: str
    duration_seconds: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "suffix": self.suffix,
            "mimeType": self.mime_type,
            "sizeBytes": self.size_bytes,
            "sha256": self.sha256,
            "durationSeconds": self.duration_seconds,
        }


class TranscriptionProvider(Protocol):
    def transcribe(self, path: Path, mime_type: str) -> tuple[dict[str, Any], int]: ...


def inspect_audio(path: Path, filename: str, settings: Settings) -> AudioInspection:
    size = path.stat().st_size
    if size <= 0:
        raise AudioInspectionError("音频文件为空")
    if size > settings.max_audio_bytes:
        raise AudioInspectionError("音频超过 200MB 限制")

    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_AUDIO_SUFFIXES:
        raise AudioInspectionError("仅支持 MP3、M4A、WAV、FLAC 和 OGG 音频")

    kind = filetype.guess(str(path))
    mime_type = kind.mime if kind else ""
    if mime_type not in SUPPORTED_AUDIO_MIMES:
        raise AudioInspectionError("音频内容与扩展名不匹配或格式无法识别")

    parsed = MutagenFile(path)
    duration = float(getattr(getattr(parsed, "info", None), "length", 0) or 0)
    if duration <= 0:
        raise AudioInspectionError("无法读取音频时长")
    if duration > settings.max_audio_seconds:
        raise AudioInspectionError("音频超过 120 分钟限制")

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return AudioInspection(suffix, mime_type, size, digest.hexdigest(), round(duration, 3))


class DeepgramTranscriptionProvider:
    endpoint = "https://api.deepgram.com/v1/listen"

    def __init__(self, settings: Settings):
        self.settings = settings

    def transcribe(self, path: Path, mime_type: str) -> tuple[dict[str, Any], int]:
        if not self.settings.deepgram_api_key:
            raise TranscriptionError("未配置 DEEPGRAM_API_KEY")
        params = {
            "model": self.settings.deepgram_model,
            "language": "zh",
            "smart_format": "true",
            "punctuate": "true",
            "utterances": "true",
            "diarize_model": "latest",
        }
        headers = {"Authorization": f"Token {self.settings.deepgram_api_key}", "Content-Type": mime_type}
        last_error: TranscriptionError | None = None
        for attempt in range(2):
            try:
                with path.open("rb") as handle, httpx.Client(timeout=httpx.Timeout(600.0, connect=30.0)) as client:
                    response = client.post(self.endpoint, params=params, headers=headers, content=handle)
                if response.status_code in {401, 402, 403, 413, 415, 422}:
                    raise TranscriptionError(
                        f"Deepgram 拒绝请求（HTTP {response.status_code}）",
                        status_code=response.status_code,
                    )
                if response.status_code == 429 or response.status_code >= 500:
                    raise TranscriptionError(
                        f"Deepgram 暂时不可用（HTTP {response.status_code}）",
                        retryable=True,
                        status_code=response.status_code,
                    )
                response.raise_for_status()
                return response.json(), attempt
            except httpx.RequestError as exc:
                last_error = TranscriptionError(f"Deepgram 网络错误：{exc}", retryable=True)
            except TranscriptionError as exc:
                last_error = exc
                if not exc.retryable:
                    raise
            if attempt == 1:
                break
        raise last_error or TranscriptionError("Deepgram 转写失败")


def deepgram_segments(payload: dict[str, Any]) -> list[dict[str, Any]]:
    utterances = payload.get("results", {}).get("utterances", [])
    segments: list[dict[str, Any]] = []
    for index, utterance in enumerate(utterances, 1):
        text = str(utterance.get("transcript", "")).strip()
        words = utterance.get("words") or []
        word_confidences = [float(word.get("confidence", 0)) for word in words if word.get("confidence") is not None]
        speaker_confidences = [float(word.get("speaker_confidence", 0)) for word in words if word.get("speaker_confidence") is not None]
        confidence = sum(word_confidences) / len(word_confidences) if word_confidences else float(utterance.get("confidence", 0) or 0)
        speaker_confidence = (
            sum(speaker_confidences) / len(speaker_confidences)
            if speaker_confidences
            else utterance.get("speaker_confidence")
        )
        speaker = utterance.get("speaker")
        segments.append(
            {
                "id": f"S{index:04d}",
                "ordinal": index,
                "rawText": text,
                "normalizedText": " ".join(text.split()),
                "speakerLabel": f"speaker_{speaker}" if speaker is not None else "",
                "speakerRole": "unknown",
                "startTime": float(utterance.get("start", 0) or 0),
                "endTime": float(utterance.get("end", 0) or 0),
                "startChar": None,
                "endChar": None,
                "confidence": round(confidence, 3),
                "speakerConfidence": round(float(speaker_confidence), 3) if speaker_confidence is not None else None,
                "needsConfirmation": confidence < 0.75 or speaker is None or (speaker_confidence is not None and float(speaker_confidence) < 0.6) or not text,
                "excluded": not bool(text),
            }
        )
    return segments
