"""Streaming artifact storage kept outside SQLite."""

from __future__ import annotations

import hashlib
import os
import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from model_courier.contracts import ArtifactRef

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_AUDIO_BYTES = 32 * 1024 * 1024
MAX_RESULT_BYTES = 16 * 1024 * 1024


class ArtifactError(ValueError):
    pass


@dataclass(frozen=True)
class UploadHandle:
    task_id: str
    artifact_id: str
    name: str
    mime: str
    declared_size: int
    path: Path
    temporary_path: Path


@dataclass(frozen=True)
class CleanupReport:
    deleted_files: int
    deleted_bytes: int
    missing_files: int


class ArtifactStore:
    def __init__(self, root: Path, max_total_bytes: int = 10 * 1024**3) -> None:
        self.root = Path(root)
        self.max_total_bytes = max_total_bytes
        self.incoming = self.root / "incoming"
        self.staging = self.root / "staging"
        self.results = self.root / "results"
        self.incoming.mkdir(parents=True, exist_ok=True)
        self.staging.mkdir(parents=True, exist_ok=True)
        self.results.mkdir(parents=True, exist_ok=True)

    def begin(
        self, task_id: str, kind: str, declared_size: int, mime: str, name: str
    ) -> UploadHandle:
        if kind not in {"input", "result"}:
            raise ArtifactError("unsupported artifact kind")
        if declared_size < 0:
            raise ArtifactError("declared size cannot be negative")
        limit = MAX_RESULT_BYTES if kind == "result" else self._input_limit(mime)
        if declared_size > limit:
            raise ArtifactError(f"artifact exceeds {limit} byte limit")
        safe_name = Path(name).name
        if not safe_name or safe_name in {".", ".."}:
            raise ArtifactError("invalid artifact name")
        artifact_id = secrets.token_hex(16)
        directory = (self.incoming if kind == "input" else self.staging) / task_id
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{artifact_id}-{safe_name}"
        temporary_path = path.with_suffix(path.suffix + ".part")
        return UploadHandle(
            task_id, artifact_id, safe_name, mime, declared_size, path, temporary_path
        )

    def write_stream(self, handle: UploadHandle, chunks: Iterable[bytes]) -> ArtifactRef:
        digest = hashlib.sha256()
        size = 0
        try:
            with handle.temporary_path.open("wb") as output:
                for chunk in chunks:
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > handle.declared_size:
                        raise ArtifactError("upload is larger than declared size")
                    digest.update(chunk)
                    output.write(chunk)
            if size != handle.declared_size:
                raise ArtifactError("upload size does not match declaration")
            os.replace(handle.temporary_path, handle.path)
        except Exception:
            handle.temporary_path.unlink(missing_ok=True)
            handle.path.unlink(missing_ok=True)
            raise
        return ArtifactRef(
            artifact_id=handle.artifact_id,
            name=handle.name,
            mime=handle.mime,
            size_bytes=size,
            sha256=digest.hexdigest(),
        )

    def path_for(self, handle: UploadHandle | ArtifactRef) -> Path:
        if isinstance(handle, UploadHandle):
            return handle.path
        matches = list(self.root.rglob(f"{handle.artifact_id}-*"))
        if len(matches) != 1:
            raise ArtifactError("artifact file is not uniquely present")
        return matches[0]

    def publish_result(
        self,
        task_id: str,
        lease_generation: int,
        files: Iterable[tuple[str, str, bytes]],
    ) -> list[ArtifactRef]:
        output: list[ArtifactRef] = []
        for name, mime, content in files:
            handle = self.begin(task_id, "result", len(content), mime, name)
            result = self.write_stream(handle, [content])
            final_directory = self.results / task_id / str(lease_generation)
            final_directory.mkdir(parents=True, exist_ok=True)
            final_path = final_directory / handle.path.name
            os.replace(handle.path, final_path)
            output.append(result)
        return output

    def delete_expired(self, paths: Iterable[Path]) -> CleanupReport:
        deleted_files = 0
        deleted_bytes = 0
        missing_files = 0
        for path in paths:
            path = Path(path)
            if not path.exists():
                missing_files += 1
                continue
            if not path.is_file() or self.root not in path.resolve().parents:
                continue
            size = path.stat().st_size
            path.unlink()
            deleted_files += 1
            deleted_bytes += size
        return CleanupReport(deleted_files, deleted_bytes, missing_files)

    @staticmethod
    def _input_limit(mime: str) -> int:
        if mime.startswith("image/"):
            return MAX_IMAGE_BYTES
        if mime.startswith("audio/"):
            return MAX_AUDIO_BYTES
        raise ArtifactError("unsupported input MIME type")
