"""FunASR adapter with lazy optional import."""

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


class FunASRProvider:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self._model = None

    def describe(self) -> CapabilityManifest:
        return CapabilityManifest(
            capability_id="funasr:default",
            task_type="audio.transcribe.v1",
            provider="funasr",
            models=["paraformer", "paraformer-zh", "SenseVoiceSmall"],
            formats=["audio/wav", "audio/mpeg", "audio/mp4", "audio/ogg"],
        )

    def validate(self, task: TaskEnvelope) -> None:
        if task.task_type != "audio.transcribe.v1":
            raise ProviderError("unsupported_task", "FunASR only handles audio transcription")

    def execute(self, task: TaskEnvelope, context: ExecutionContext) -> ProviderResult:
        if not context.input_paths:
            raise ProviderError("missing_input", "audio input is missing")
        try:
            from funasr import AutoModel
        except ImportError as exc:
            raise ProviderError(
                "provider_dependency_missing",
                "install funasr to use this Provider",
            ) from exc
        if self._model is None:
            self._model = AutoModel(
                model=self.config.get("model", "paraformer-zh"),
                device=self.config.get("device", "cuda"),
                disable_update=True,
            )
        generated = self._model.generate(input=str(context.input_paths[0]))
        first = generated[0] if isinstance(generated, list) and generated else generated
        if not isinstance(first, dict):
            raise ProviderError("invalid_provider_output", "FunASR returned an unsupported result")
        payload = {
            "text": first.get("text", ""),
            "language": task.options.get("language", "zh"),
            "segments": first.get("segments", []),
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
    provider = FunASRProvider({})
    return ProviderRegistration(provider.describe(), FunASRProvider)
