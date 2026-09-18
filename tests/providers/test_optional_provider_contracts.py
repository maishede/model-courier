from __future__ import annotations

from model_courier_provider_faster_whisper.provider import FasterWhisperProvider
from model_courier_provider_faster_whisper.provider import registration as whisper_registration
from model_courier_provider_funasr.provider import FunASRProvider
from model_courier_provider_funasr.provider import registration as funasr_registration
from model_courier_provider_ultralytics.provider import UltralyticsProvider
from model_courier_provider_ultralytics.provider import registration as yolo_registration

from model_courier.contracts import ArtifactRef, TaskEnvelope


def test_optional_provider_registration_is_lazy() -> None:
    assert whisper_registration().capability.provider == "faster-whisper"
    assert funasr_registration().capability.provider == "funasr"
    assert yolo_registration().capability.provider == "ultralytics"


def test_optional_providers_validate_semantic_task_types_without_model_imports() -> None:
    audio = TaskEnvelope(
        task_type="audio.transcribe.v1",
        input_artifacts=[ArtifactRef(name="voice.wav", mime="audio/wav")],
        idempotency_key="voice-1",
    )
    image = TaskEnvelope(
        task_type="vision.detect.v1",
        input_artifacts=[ArtifactRef(name="frame.jpg", mime="image/jpeg")],
        idempotency_key="frame-1",
    )

    FasterWhisperProvider({}).validate(audio)
    FunASRProvider({}).validate(audio)
    UltralyticsProvider({}).validate(image)
