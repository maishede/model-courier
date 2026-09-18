"""Faster-Whisper adapter with lazy optional import."""

from __future__ import annotations

from typing import Any

from model_courier.contracts import (
    CapabilityManifest,
    ProviderResult,
    TaskEnvelope,
    canonical_json_digest,
)
from model_courier.worker.factory import ProviderRegistration
from model_courier.worker.provider import ExecutionContext, ProviderError


class FasterWhisperProvider:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self._model = None

    def describe(self) -> CapabilityManifest:
        return CapabilityManifest(
            capability_id="faster-whisper:default",
            task_type="audio.transcribe.v1",
            provider="faster-whisper",
            models=["tiny", "base", "small", "medium", "large-v3"],
            formats=["audio/wav", "audio/mpeg", "audio/mp4", "audio/ogg"],
        )

    def validate(self, task: TaskEnvelope) -> None:
        if task.task_type != "audio.transcribe.v1":
            raise ProviderError(
                "unsupported_task",
                "Faster-Whisper only handles audio transcription",
            )

    def execute(self, task: TaskEnvelope, context: ExecutionContext) -> ProviderResult:
        if not context.input_paths:
            raise ProviderError("missing_input", "audio input is missing")
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise ProviderError(
                "provider_dependency_missing",
                "install faster-whisper to use this Provider",
            ) from exc
        if self._model is None:
            self._model = WhisperModel(
                self.config.get("model", "small"),
                device=self.config.get("device", "auto"),
                compute_type=self.config.get("compute_type", "default"),
            )
        segments, info = self._model.transcribe(
            str(context.input_paths[0]),
            language=task.options.get("language"),
            vad_filter=task.options.get("vad_filter", True),
        )
        normalized = []
        text_parts = []
        for segment in segments:
            text = segment.text.strip()
            text_parts.append(text)
            normalized.append(
                {"start": float(segment.start), "end": float(segment.end), "text": text}
            )
        payload = {
            "text": " ".join(part for part in text_parts if part),
            "language": getattr(info, "language", None),
            "segments": normalized,
        }
        context.report_progress(1.0, "transcription complete")
        return ProviderResult(
            schema_version="audio.transcribe.v1",
            json=payload,
            result_digest=canonical_json_digest(payload),
        )

    def close(self) -> None:
        self._model = None


def registration() -> ProviderRegistration:
    provider = FasterWhisperProvider({})
    return ProviderRegistration(provider.describe(), FasterWhisperProvider)
