"""Ultralytics object detection adapter with lazy optional import."""

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


class UltralyticsProvider:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self._model = None

    def describe(self) -> CapabilityManifest:
        return CapabilityManifest(
            capability_id="ultralytics:default",
            task_type="vision.detect.v1",
            provider="ultralytics",
            models=["yolo11n", "yolo11s", "custom"],
            formats=["image/jpeg", "image/png", "image/webp"],
        )

    def validate(self, task: TaskEnvelope) -> None:
        if task.task_type != "vision.detect.v1":
            raise ProviderError("unsupported_task", "Ultralytics only handles object detection")

    def execute(self, task: TaskEnvelope, context: ExecutionContext) -> ProviderResult:
        if not context.input_paths:
            raise ProviderError("missing_input", "image input is missing")
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise ProviderError(
                "provider_dependency_missing",
                "install ultralytics to use this Provider",
            ) from exc
        if self._model is None:
            self._model = YOLO(self.config.get("model", "yolo11n"))
        predictions = self._model.predict(
            source=str(context.input_paths[0]),
            device=self.config.get("device", 0),
            verbose=False,
        )
        if not predictions:
            raise ProviderError(
                "invalid_provider_output",
                "Ultralytics returned no prediction object",
            )
        prediction = predictions[0]
        boxes = getattr(prediction, "boxes", None)
        detections = []
        if boxes is not None:
            xyxy = boxes.xyxy.tolist()
            confidence = boxes.conf.tolist()
            classes = boxes.cls.tolist()
            names = getattr(prediction, "names", {})
            for coordinates, score, class_id in zip(
                xyxy, confidence, classes, strict=True
            ):
                class_index = int(class_id)
                detections.append(
                    {
                        "label": names.get(class_index, str(class_index)),
                        "confidence": float(score),
                        "xyxy": [float(value) for value in coordinates],
                    }
                )
        payload = {
            "width": int(getattr(prediction, "orig_shape", (0, 0))[1]),
            "height": int(getattr(prediction, "orig_shape", (0, 0))[0]),
            "detections": detections,
        }
        context.report_progress(1.0, "detection complete")
        return ProviderResult(
            schema_version="vision.detect.v1",
            json=payload,
            result_digest=canonical_json_digest(payload),
        )

    def close(self) -> None:
        self._model = None


def registration() -> ProviderRegistration:
    provider = UltralyticsProvider({})
    return ProviderRegistration(provider.describe(), UltralyticsProvider)
