"""Submit one camera frame from a Raspberry Pi.

Set MODEL_COURIER_URL and MODEL_COURIER_DEVICE_TOKEN in the environment before
running this example. The camera capture step is deliberately left as a file
read so it works with picamera2, libcamera, or a saved test frame.
"""

from __future__ import annotations

import os
from pathlib import Path

from model_courier_sdk import ModelCourierClient

from model_courier.contracts import ArtifactRef, TaskEnvelope

image_path = Path(os.environ.get("MODEL_COURIER_IMAGE", "frame.jpg"))
envelope = TaskEnvelope(
    task_type="vision.detect.v1",
    input_artifacts=[ArtifactRef(name=image_path.name, mime="image/jpeg")],
    idempotency_key=f"pi-camera:{image_path.stat().st_mtime_ns}",
)

with ModelCourierClient(
    os.environ["MODEL_COURIER_URL"], os.environ["MODEL_COURIER_DEVICE_TOKEN"]
) as client:
    view = client.create_task(envelope, image_path)
    print(client.wait_for_result(view.task_id, timeout=120))
