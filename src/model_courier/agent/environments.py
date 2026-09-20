"""Inspection of user-owned Python and Conda environments."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

from .models import EnvironmentProfile


@dataclass(frozen=True)
class EnvironmentCheck:
    available: bool
    python_version: tuple[int, int, int] | None = None
    error_code: str | None = None
    message: str | None = None


class EnvironmentInspector:
    """Run a fixed version probe without invoking a shell or user code."""

    def inspect(self, profile: EnvironmentProfile) -> EnvironmentCheck:
        executable = profile.executable
        if not executable.is_file():
            return EnvironmentCheck(
                False,
                error_code="executable_missing",
                message="Python executable was not found",
            )

        try:
            completed = subprocess.run(
                [str(executable), "-c", "import sys; print(sys.version_info[:3])"],
                capture_output=True,
                check=False,
                cwd=str(profile.conda_prefix) if profile.conda_prefix else None,
                env={**profile.environment, "PYTHONNOUSERSITE": "1"},
                text=True,
                timeout=10,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return EnvironmentCheck(
                False,
                error_code="probe_failed",
                message="Python version probe failed",
            )

        if completed.returncode != 0:
            return EnvironmentCheck(
                False,
                error_code="probe_failed",
                message="Python version probe failed",
            )

        version = _parse_version(completed.stdout)
        if version is None:
            return EnvironmentCheck(
                False,
                error_code="invalid_probe",
                message="Python returned an invalid version",
            )
        return EnvironmentCheck(True, python_version=version)


def _parse_version(output: str) -> tuple[int, int, int] | None:
    text = output.strip().strip("()")
    pieces = [piece.strip() for piece in text.split(",")]
    if len(pieces) != 3:
        return None
    try:
        version = tuple(int(piece) for piece in pieces)
    except ValueError:
        return None
    return version if len(version) == 3 else None
