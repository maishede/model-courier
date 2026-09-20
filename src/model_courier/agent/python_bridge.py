"""Bounded JSONL bridge for a model adapter in a user-owned Python environment."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

_SECRET = re.compile(r"(?i)(token|secret|password|authorization)(\s*[=:]\s*)[^,;\s]+")


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
            stdout, stderr = process.communicate(payload + b"\n", timeout=self.timeout)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.communicate()
            raise BridgeError("bridge_timeout", "adapter exceeded its execution deadline") from exc
        except OSError as exc:
            raise BridgeError(
                "bridge_start_failed", "adapter process could not be started"
            ) from exc

        if len(stdout) > self.max_message_bytes:
            raise BridgeError("message_too_large", "adapter response exceeded the size limit")
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


def _redact(value: str) -> str:
    return _SECRET.sub(r"\1\2[redacted]", value)
