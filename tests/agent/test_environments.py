from __future__ import annotations

import sys
from pathlib import Path

from model_courier.agent.environments import EnvironmentInspector
from model_courier.agent.models import EnvironmentProfile


def test_inspector_reports_current_python_version() -> None:
    profile = EnvironmentProfile(
        profile_id="system-python",
        display_name="System Python",
        executable=Path(sys.executable),
    )

    result = EnvironmentInspector().inspect(profile)

    assert result.available is True
    assert result.python_version is not None
    assert result.error_code is None


def test_inspector_rejects_missing_executable_without_running_shell(tmp_path: Path) -> None:
    profile = EnvironmentProfile(
        profile_id="missing",
        display_name="Missing",
        executable=tmp_path / "missing-python.exe",
    )

    result = EnvironmentInspector().inspect(profile)

    assert result.available is False
    assert result.error_code == "executable_missing"
