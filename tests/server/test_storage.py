from __future__ import annotations

from pathlib import Path

import pytest

from model_courier.server.storage import ArtifactError, ArtifactStore


def test_streamed_input_is_hashed_and_atomically_published(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    handle = store.begin("task-1", "input", 5, "image/jpeg", "frame.jpg")
    ref = store.write_stream(handle, [b"he", b"llo"])

    assert ref.size_bytes == 5
    assert ref.sha256 == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    assert handle.path.exists()
    assert not handle.temporary_path.exists()


def test_stream_rejects_size_mismatch_and_cleans_partial_file(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    handle = store.begin("task-1", "input", 5, "audio/wav", "sample.wav")

    with pytest.raises(ArtifactError, match="size"):
        store.write_stream(handle, [b"short"][:-1])

    assert not handle.path.exists()
    assert not handle.temporary_path.exists()


def test_limits_and_path_traversal_are_enforced(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)

    with pytest.raises(ArtifactError, match="MIME"):
        store.begin("task-1", "input", 1, "application/octet-stream", "data.bin")
    with pytest.raises(ArtifactError, match="limit"):
        store.begin("task-1", "input", 8 * 1024 * 1024 + 1, "image/jpeg", "frame.jpg")
    handle = store.begin("task-1", "input", 1, "image/jpeg", "..\\outside.jpg")
    assert handle.name == "outside.jpg"
