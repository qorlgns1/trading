import io
from abc import ABC, abstractmethod
from datetime import UTC, datetime, timedelta
from pathlib import Path

from quant_api.settings import Settings


class ArtifactStore(ABC):
    @abstractmethod
    def put(self, object_key: str, payload: bytes, content_type: str) -> int:
        raise NotImplementedError

    @abstractmethod
    def download_url(self, object_key: str, expires_in: timedelta) -> str:
        raise NotImplementedError

    def local_path(self, object_key: str) -> Path | None:
        return None


class LocalArtifactStore(ArtifactStore):
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, object_key: str, payload: bytes, content_type: str) -> int:
        del content_type
        target = (self.root / object_key).resolve()
        if self.root not in target.parents:
            raise ValueError("허용되지 않은 산출물 경로입니다.")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        return len(payload)

    def download_url(self, object_key: str, expires_in: timedelta) -> str:
        del expires_in
        return f"/api/v1/artifacts/local/{object_key}"

    def local_path(self, object_key: str) -> Path | None:
        target = (self.root / object_key).resolve()
        if self.root not in target.parents:
            return None
        return target


class OCIArtifactStore(ArtifactStore):
    def __init__(self, settings: Settings) -> None:
        import oci

        signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
        self.client = oci.object_storage.ObjectStorageClient(
            {"region": settings.oci_region}, signer=signer
        )
        self.namespace = settings.oci_namespace or ""
        self.bucket = settings.oci_bucket_name or ""
        self.region = settings.oci_region
        self.oci = oci

    def put(self, object_key: str, payload: bytes, content_type: str) -> int:
        self.client.put_object(
            self.namespace,
            self.bucket,
            object_key,
            io.BytesIO(payload),
            content_type=content_type,
        )
        return len(payload)

    def download_url(self, object_key: str, expires_in: timedelta) -> str:
        details = self.oci.object_storage.models.CreatePreauthenticatedRequestDetails(
            name=f"download-{datetime.now(UTC).timestamp():.0f}",
            access_type="ObjectRead",
            time_expires=datetime.now(UTC) + expires_in,
            object_name=object_key,
        )
        response = self.client.create_preauthenticated_request(
            self.namespace, self.bucket, details
        )
        return f"https://objectstorage.{self.region}.oraclecloud.com{response.data.access_uri}"


def create_artifact_store(settings: Settings) -> ArtifactStore:
    if settings.artifact_backend == "oci":
        return OCIArtifactStore(settings)
    return LocalArtifactStore(settings.artifact_root)
