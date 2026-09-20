"""Bounded JSONL bridge for a model adapter in a user-owned Python environment."""

from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

_AUTHORIZATION_HEADER = re.compile(
    r"(?i)(\bauthorization\b\s*:\s*(?:bearer|basic)\s+)[^\s,;]+"
)
_SECRET = re.compile(
    r"(?i)([\"']?(?:token|secret|password|authorization)[\"']?\s*[=:]\s*)"
    r"(?:\"(?:\\.|[^\"])*\"|'(?:\\.|[^'])*'|[^\s,;}\]]+)"
)


class BridgeError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class PythonBridge:
    def __init__(
        self,
        executable: Path,
        arguments: Sequence[str],
        *,
        timeout: float = 60.0,
        max_message_bytes: int = 1_048_576,
    ) -> None:
        self.executable = executable
        self.arguments = tuple(arguments)
        self.timeout = timeout
        self.max_message_bytes = max_message_bytes

    def run(self, request: dict[str, Any]) -> dict[str, Any]:
        request_id = request.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            raise BridgeError("invalid_request", "request_id is required")
        payload = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(payload) + 1 > self.max_message_bytes:
            raise BridgeError("message_too_large", "adapter request exceeded the size limit")
        try:
            process = subprocess.Popen(
                [str(self.executable), *self.arguments],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
            )
        except OSError as exc:
            raise BridgeError(
                "bridge_start_failed", "adapter process could not be started"
            ) from exc

        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []
        stream_sizes = {"stdout": 0, "stderr": 0}
        output_exceeded = threading.Event()

        stdout_thread = threading.Thread(
            target=_capture_stream,
            args=(
                process.stdout,
                "stdout",
                self.max_message_bytes,
                stdout_chunks,
                stream_sizes,
                output_exceeded,
            ),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=_capture_stream,
            args=(
                process.stderr,
                "stderr",
                self.max_message_bytes,
                stderr_chunks,
                stream_sizes,
                output_exceeded,
            ),
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()

        def write_request() -> None:
            try:
                assert process.stdin is not None
                process.stdin.write(payload + b"\n")
                process.stdin.close()
            except (BrokenPipeError, OSError):
                return

        writer_thread = threading.Thread(target=write_request, daemon=True)
        writer_thread.start()
        deadline = time.monotonic() + self.timeout
        timed_out = False
        while process.poll() is None:
            if output_exceeded.is_set():
                process.kill()
                break
            if time.monotonic() >= deadline:
                timed_out = True
                process.kill()
                break
            time.sleep(0.01)
        try:
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        writer_thread.join(timeout=1.0)
        stdout_thread.join(timeout=1.0)
        stderr_thread.join(timeout=1.0)

        if timed_out:
            raise BridgeError("bridge_timeout", "adapter exceeded its execution deadline")
        if output_exceeded.is_set():
            raise BridgeError("message_too_large", "adapter response exceeded the size limit")
        stdout = b"".join(stdout_chunks)
        stderr = b"".join(stderr_chunks)
        if process.returncode != 0:
            detail = _redact(stderr.decode("utf-8", errors="replace")[:512])
            suffix = f": {detail}" if detail else ""
            raise BridgeError(
                "bridge_exit", f"adapter exited with code {process.returncode}{suffix}"
            )

        lines = [line for line in stdout.splitlines() if line.strip()]
        if not lines:
            raise BridgeError("bridge_no_response", "adapter returned no JSON response")
        try:
            response = json.loads(lines[0].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BridgeError("invalid_response", "adapter returned invalid JSON") from exc
        if not isinstance(response, dict):
            raise BridgeError("invalid_response", "adapter response must be a JSON object")
        if response.get("request_id") != request_id:
            raise BridgeError("request_mismatch", "adapter response request_id did not match")
        return response


def _capture_stream(
    stream: Any,
    name: str,
    max_message_bytes: int,
    chunks: list[bytes],
    sizes: dict[str, int],
    exceeded: threading.Event,
) -> None:
    total = 0
    while True:
        chunk = stream.read(8192)
        if not chunk:
            break
        previous = total
        total += len(chunk)
        if previous < max_message_bytes:
            chunks.append(chunk[: max_message_bytes - previous])
        if total > max_message_bytes:
            exceeded.set()
    sizes[name] = total


def _redact(value: str) -> str:
    value = _AUTHORIZATION_HEADER.sub(r"\1[redacted]", value)
    return _SECRET.sub(r"\1[redacted]", value)
