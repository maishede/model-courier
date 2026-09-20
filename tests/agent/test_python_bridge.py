from __future__ import annotations

import sys
from pathlib import Path

import pytest

from model_courier.agent.python_bridge import BridgeError, PythonBridge, _redact


def write_bridge_script(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "bridge.py"
    script.write_text(body, encoding="utf-8")
    return script


def test_bridge_round_trips_one_jsonl_request(tmp_path: Path) -> None:
    script = write_bridge_script(
        tmp_path,
        """
import json
import sys
for line in sys.stdin:
    request = json.loads(line)
    print(json.dumps({'request_id': request['request_id'], 'ok': True}), flush=True)
    break
""",
    )

    result = PythonBridge(Path(sys.executable), [str(script)]).run(
        {"request_id": "req-1", "operation": "check"}
    )

    assert result == {"request_id": "req-1", "ok": True}


def test_bridge_rejects_oversized_response(tmp_path: Path) -> None:
    script = write_bridge_script(
        tmp_path,
        """
import sys
for line in sys.stdin:
    print('x' * 128, flush=True)
    break
""",
    )

    with pytest.raises(BridgeError, match="message_too_large"):
        PythonBridge(Path(sys.executable), [str(script)], max_message_bytes=64).run(
            {"request_id": "req-1"}
        )


def test_bridge_rejects_oversized_diagnostics(tmp_path: Path) -> None:
    script = write_bridge_script(
        tmp_path,
        """
import sys
sys.stderr.write('x' * 128)
sys.stderr.flush()
""",
    )

    with pytest.raises(BridgeError, match="message_too_large"):
        PythonBridge(Path(sys.executable), [str(script)], max_message_bytes=64).run(
            {"request_id": "req-1"}
        )


def test_bridge_rejects_oversized_request_before_starting_child(tmp_path: Path) -> None:
    script = write_bridge_script(tmp_path, "raise SystemExit(0)\n")

    with pytest.raises(BridgeError, match="message_too_large"):
        PythonBridge(Path(sys.executable), [str(script)], max_message_bytes=32).run(
            {"request_id": "req-1", "payload": "x" * 128}
        )


def test_bridge_reports_child_failure_without_leaking_secret(tmp_path: Path) -> None:
    script = write_bridge_script(
        tmp_path,
        """
import sys
print('authorization=secret-token', file=sys.stderr)
raise SystemExit(3)
""",
    )

    with pytest.raises(BridgeError) as caught:
        PythonBridge(Path(sys.executable), [str(script)]).run(
            {"request_id": "req-1", "authorization": "secret-token"}
        )

    assert caught.value.code == "bridge_exit"
    assert "secret-token" not in str(caught.value)


@pytest.mark.parametrize(
    "diagnostic",
    [
        '{"token": "secret-token"}',
        'Authorization: Bearer secret-token',
        "password=secret-token",
    ],
)
def test_redaction_covers_json_and_header_secret_formats(diagnostic: str) -> None:
    redacted = _redact(diagnostic)

    assert "secret-token" not in redacted
    assert "[redacted]" in redacted
