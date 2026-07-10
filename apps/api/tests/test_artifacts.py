from datetime import timedelta
from pathlib import Path

import pytest
from quant_api.artifacts import LocalArtifactStore, create_artifact_store
from quant_api.settings import Settings


def test_local_artifact_store_keeps_objects_inside_root(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    assert store.put("runs/id/report.html", b"report", "text/html") == 6
    target = store.local_path("runs/id/report.html")
    assert target is not None and target.read_bytes() == b"report"
    assert store.download_url("runs/id/report.html", timedelta(minutes=10)).endswith(
        "/runs/id/report.html"
    )
    with pytest.raises(ValueError, match="허용되지 않은"):
        store.put("../secret", b"x", "text/plain")
    assert store.local_path("../secret") is None


def test_store_factory_uses_local_backend(tmp_path: Path) -> None:
    settings = Settings(artifact_backend="local", artifact_root=tmp_path)
    assert isinstance(create_artifact_store(settings), LocalArtifactStore)
