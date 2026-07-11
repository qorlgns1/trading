import hashlib
import json
import os
import shutil
from contextlib import suppress
from datetime import date
from pathlib import Path
from typing import Any, cast

import polars as pl
from quant_core.enums import PeerGroup


class ResearchSnapshotMissing(RuntimeError):
    pass


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


class ResearchSnapshotStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.snapshots_root = root / "snapshots"
        self.checkpoints_root = root / "checkpoints"
        self.leases_root = root / "leases"
        self.pointer_path = root / "current.json"

    def ensure(self) -> None:
        self.snapshots_root.mkdir(parents=True, exist_ok=True)
        self.checkpoints_root.mkdir(parents=True, exist_ok=True)
        self.leases_root.mkdir(parents=True, exist_ok=True)

    def current_manifest(self) -> dict[str, Any] | None:
        if not self.pointer_path.is_file():
            return None
        pointer = json.loads(self.pointer_path.read_text(encoding="utf-8"))
        manifest_path = self.root / pointer["manifest_path"]
        if not manifest_path.is_file():
            return None
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        return cast(dict[str, Any], payload)

    def current_snapshot_path(self) -> Path | None:
        manifest = self.current_manifest()
        if manifest is None:
            return None
        return self.root / str(manifest["snapshot_path"])

    def snapshot_path(self, data_version: str) -> Path:
        path = self.snapshots_root / data_version
        if not (path / "manifest.json").is_file():
            raise ResearchSnapshotMissing(f"데이터 스냅샷을 찾을 수 없습니다: {data_version}")
        return path

    def acquire_lease(self, data_version: str, owner_id: str) -> Path:
        self.ensure()
        self.snapshot_path(data_version)
        path = self.leases_root / data_version / owner_id
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("leased\n", encoding="utf-8")
        return path

    def release_lease(self, data_version: str, owner_id: str) -> None:
        path = self.leases_root / data_version / owner_id
        path.unlink(missing_ok=True)
        with suppress(OSError):
            path.parent.rmdir()

    def create_staging(self, run_id: str) -> Path:
        self.ensure()
        path = self.snapshots_root / f".staging-{run_id}"
        shutil.rmtree(path, ignore_errors=True)
        (path / "bars").mkdir(parents=True)
        (path / "scores").mkdir(parents=True)
        return path

    def clone_current_bars(self, target: Path) -> None:
        current = self.current_snapshot_path()
        if current is None:
            return
        source_root = current / "bars"
        if not source_root.is_dir():
            return
        for source in source_root.rglob("*.parquet"):
            relative = source.relative_to(source_root)
            destination = target / "bars" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(source, destination)
            except OSError:
                shutil.copy2(source, destination)

    def activate(self, staging: Path, manifest: dict[str, Any]) -> Path:
        version = str(manifest["data_version"])
        final_path = self.snapshots_root / version
        manifest["snapshot_path"] = str(final_path.relative_to(self.root))
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if final_path.exists():
            shutil.rmtree(staging, ignore_errors=True)
        else:
            staging.rename(final_path)
        pointer_temp = self.pointer_path.with_suffix(".tmp")
        pointer_temp.write_text(
            json.dumps(
                {
                    "data_version": version,
                    "manifest_path": str((final_path / "manifest.json").relative_to(self.root)),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(pointer_temp, self.pointer_path)
        self._retain_latest(3)
        return final_path

    def _retain_latest(self, count: int) -> None:
        directories = sorted(
            [
                path
                for path in self.snapshots_root.iterdir()
                if path.is_dir() and not path.name.startswith(".staging-")
            ],
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        leased = (
            {
                path.name
                for path in self.leases_root.iterdir()
                if path.is_dir() and any(path.iterdir())
            }
            if self.leases_root.is_dir()
            else set()
        )
        retained = 0
        for path in directories:
            if path.name in leased or retained < count:
                retained += path.name not in leased
                continue
            shutil.rmtree(path, ignore_errors=True)

    def scan_bars(
        self,
        *,
        snapshot_path: Path | None = None,
        peer_group: PeerGroup | None = None,
        start: date | None = None,
    ) -> pl.LazyFrame:
        base = snapshot_path or self.current_snapshot_path()
        if base is None:
            raise ResearchSnapshotMissing("정상 실데이터 스냅샷이 없습니다.")
        pattern = (
            base / "bars" / f"peer_group={peer_group.value}" / "year=*" / "bars.parquet"
            if peer_group is not None
            else base / "bars" / "peer_group=*" / "year=*" / "bars.parquet"
        )
        frame = pl.scan_parquet(str(pattern), hive_partitioning=True)
        if start is not None:
            frame = frame.filter(pl.col("date") >= start)
        return frame

    def universe(self, snapshot_path: Path | None = None) -> pl.DataFrame:
        base = snapshot_path or self.current_snapshot_path()
        if base is None:
            raise ResearchSnapshotMissing("정상 실데이터 스냅샷이 없습니다.")
        return pl.read_csv(base / "universe.csv")

    def latest_scores(self, snapshot_path: Path | None = None) -> pl.DataFrame:
        base = snapshot_path or self.current_snapshot_path()
        if base is None:
            raise ResearchSnapshotMissing("정상 실데이터 스냅샷이 없습니다.")
        return pl.read_parquet(base / "scores" / "latest.parquet")

    def score_history(self, snapshot_path: Path | None = None) -> pl.LazyFrame:
        base = snapshot_path or self.current_snapshot_path()
        if base is None:
            raise ResearchSnapshotMissing("정상 실데이터 스냅샷이 없습니다.")
        return pl.scan_parquet(base / "scores" / "history.parquet")

    def quality_report(self, sync_id: str | None = None) -> dict[str, Any]:
        if sync_id is not None:
            path = self.root / "quality-runs" / sync_id / "summary.json"
        else:
            base = self.current_snapshot_path()
            if base is None:
                raise ResearchSnapshotMissing("정상 실데이터 스냅샷이 없습니다.")
            path = base / "quality" / "summary.json"
        if not path.is_file():
            raise ResearchSnapshotMissing("데이터 품질 보고서가 없습니다.")
        return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))

    def quality_issues(self, sync_id: str | None = None) -> pl.DataFrame:
        if sync_id is not None:
            path = self.root / "quality-runs" / sync_id / "issues.parquet"
        else:
            base = self.current_snapshot_path()
            if base is None:
                raise ResearchSnapshotMissing("정상 실데이터 스냅샷이 없습니다.")
            path = base / "quality" / "issues.parquet"
        if not path.is_file():
            raise ResearchSnapshotMissing("데이터 품질 문제 목록이 없습니다.")
        return pl.read_parquet(path)
