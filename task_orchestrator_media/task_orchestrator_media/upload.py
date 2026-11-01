"""Asynchronous S3-compatible artifact upload and short-lived access URLs."""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from pathlib import Path
from queue import Empty, Full, Queue
from typing import Any, Callable, Protocol
from urllib.parse import quote, urlparse

from task_orchestrator_media.models import ArtifactRecord
from task_orchestrator_media.spool import SpoolError


class S3Client(Protocol):
    """Subset of the boto3 S3 client used by the uploader."""

    def upload_file(
        self,
        filename: str,
        bucket: str,
        key: str,
        ExtraArgs: dict[str, Any] | None = None,
    ) -> Any:
        """Upload one local file."""

    def generate_presigned_url(
        self,
        ClientMethod: str,
        Params: dict[str, Any] | None = None,
        ExpiresIn: int = 3600,
    ) -> str:
        """Create a temporary object access URL."""


@dataclass(frozen=True)
class S3UploadConfig:
    """Validated object-storage settings without embedded credentials."""

    enabled: bool = False
    bucket: str = ""
    prefix: str = "task-orchestrator"
    endpoint_url: str = ""
    region: str = ""
    public_base_url: str = ""
    addressing_style: str = "auto"
    allow_insecure_endpoint: bool = False
    max_attempts: int = 3
    retry_delay_sec: float = 1.0
    presign_default_sec: int = 900
    presign_max_sec: int = 3600
    server_side_encryption: str = ""
    kms_key_id: str = ""

    def validate(self) -> "S3UploadConfig":
        if not self.enabled:
            return self
        if not self.bucket or any(char.isspace() for char in self.bucket):
            raise SpoolError("upload_bucket must be a non-empty bucket name without whitespace")
        if self.prefix.startswith("/") or ".." in self.prefix.split("/"):
            raise SpoolError("upload_prefix must be a safe relative object prefix")
        self._validate_url(self.endpoint_url, "upload_endpoint_url", allow_empty=True)
        self._validate_url(self.public_base_url, "upload_public_base_url", allow_empty=True)
        if (
            self.endpoint_url.startswith("http://")
            or self.public_base_url.startswith("http://")
        ) and not self.allow_insecure_endpoint:
            raise SpoolError("HTTP storage URLs require upload_allow_insecure_endpoint=true")
        if self.max_attempts < 1 or self.max_attempts > 20:
            raise SpoolError("upload_max_attempts must be between 1 and 20")
        if self.retry_delay_sec < 0 or self.retry_delay_sec > 300:
            raise SpoolError("upload_retry_delay_sec must be between 0 and 300")
        if not 60 <= self.presign_default_sec <= self.presign_max_sec:
            raise SpoolError("upload_presign_default_sec must be between 60 and upload_presign_max_sec")
        if not 60 <= self.presign_max_sec <= 604_800:
            raise SpoolError("upload_presign_max_sec must be between 60 and 604800")
        if self.server_side_encryption not in {"", "AES256", "aws:kms"}:
            raise SpoolError("upload_server_side_encryption must be empty, AES256 or aws:kms")
        if self.kms_key_id and self.server_side_encryption != "aws:kms":
            raise SpoolError("upload_kms_key_id requires aws:kms server-side encryption")
        if self.addressing_style not in {"auto", "path", "virtual"}:
            raise SpoolError("upload_addressing_style must be auto, path or virtual")
        return self

    def _validate_url(self, value: str, name: str, *, allow_empty: bool) -> None:
        if not value and allow_empty:
            return
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise SpoolError(f"{name} must be an HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise SpoolError(f"{name} must not contain credentials, query parameters or fragments")


@dataclass(frozen=True)
class ArtifactAccess:
    """On-demand access result that is never persisted in task history."""

    url: str
    expires_sec: int


UploadCallback = Callable[[ArtifactRecord], None]


class S3ArtifactUploader:
    """Upload finalized artifacts on one bounded background worker."""

    def __init__(
        self,
        config: S3UploadConfig,
        callback: UploadCallback,
        *,
        client: S3Client | None = None,
    ) -> None:
        self.config = config.validate()
        self._callback = callback
        self._client = client
        self._queue: Queue[tuple[ArtifactRecord, Path] | None] = Queue(maxsize=1024)
        self._known_ids: set[str] = set()
        self._lock = threading.Lock()
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None
        if self.config.enabled:
            self._thread = threading.Thread(
                target=self._run,
                name="media-s3-uploader",
                daemon=True,
            )
            self._thread.start()

    def enqueue(self, artifact: ArtifactRecord, local_path: Path) -> ArtifactRecord:
        """Queue a local artifact and return its immediate UPLOADING state."""
        if not self.config.enabled:
            return artifact
        if not local_path.is_file():
            failed = replace(artifact, upload_state="FAILED", upload_error="local artifact file is missing")
            self._callback(failed)
            return failed
        with self._lock:
            if artifact.artifact_id in self._known_ids:
                return artifact
            self._known_ids.add(artifact.artifact_id)
        pending = replace(
            artifact,
            upload_state="UPLOADING",
            remote_object_key=self.object_key(artifact),
            public_url="",
            upload_error="",
        )
        self._callback(pending)
        try:
            self._queue.put_nowait((pending, local_path))
        except Full:
            with self._lock:
                self._known_ids.discard(artifact.artifact_id)
            failed = replace(pending, upload_state="FAILED", upload_error="upload queue is full")
            self._callback(failed)
            return failed
        return pending

    def object_key(self, artifact: ArtifactRecord) -> str:
        """Build a stable, path-safe object key from the local storage key."""
        relative = Path(artifact.storage_key)
        if relative.is_absolute() or ".." in relative.parts:
            raise SpoolError("artifact storage_key is not safe for object storage")
        parts = [part.strip("/") for part in (self.config.prefix, relative.as_posix()) if part.strip("/")]
        return "/".join(parts)

    def access(self, artifact: ArtifactRecord, expires_sec: int = 0) -> ArtifactAccess:
        """Return a public URL or generate a bounded-lifetime presigned URL."""
        if artifact.upload_state != "AVAILABLE" or not artifact.remote_object_key:
            raise SpoolError("artifact is not available in object storage")
        if artifact.public_url:
            return ArtifactAccess(artifact.public_url, 0)
        ttl = expires_sec or self.config.presign_default_sec
        ttl = max(60, min(ttl, self.config.presign_max_sec))
        client = self._get_client()
        url = client.generate_presigned_url(
            ClientMethod="get_object",
            Params={"Bucket": self.config.bucket, "Key": artifact.remote_object_key},
            ExpiresIn=ttl,
        )
        return ArtifactAccess(str(url), ttl)

    def close(self, timeout_sec: float = 5.0) -> None:
        """Stop accepting work and finish the current bounded wait."""
        if self._thread is None:
            return
        self._stopping.set()
        try:
            self._queue.put_nowait(None)
        except Full:
            pass
        self._thread.join(timeout=max(0.0, timeout_sec))

    def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                item = self._queue.get(timeout=0.2)
            except Empty:
                continue
            try:
                if item is None:
                    return
                artifact, local_path = item
                self._upload_with_retry(artifact, local_path)
            finally:
                self._queue.task_done()

    def _upload_with_retry(self, artifact: ArtifactRecord, local_path: Path) -> None:
        last_error = ""
        for attempt in range(1, self.config.max_attempts + 1):
            try:
                client = self._get_client()
                extra_args: dict[str, Any] = {
                    "ContentType": artifact.mime_type,
                    "Metadata": {
                        "artifact-id": artifact.artifact_id,
                        "sha256": artifact.sha256,
                    },
                }
                if self.config.server_side_encryption:
                    extra_args["ServerSideEncryption"] = self.config.server_side_encryption
                if self.config.kms_key_id:
                    extra_args["SSEKMSKeyId"] = self.config.kms_key_id
                client.upload_file(
                    str(local_path),
                    self.config.bucket,
                    artifact.remote_object_key,
                    ExtraArgs=extra_args,
                )
                public_url = ""
                if self.config.public_base_url:
                    public_url = (
                        self.config.public_base_url.rstrip("/")
                        + "/"
                        + quote(artifact.remote_object_key, safe="/")
                    )
                self._callback(
                    replace(
                        artifact,
                        upload_state="AVAILABLE",
                        public_url=public_url,
                        upload_error="",
                    )
                )
                return
            except Exception as exc:  # noqa: BLE001 - SDK/network failures become artifact state.
                last_error = f"attempt {attempt}/{self.config.max_attempts}: {exc}"
                if attempt < self.config.max_attempts:
                    self._stopping.wait(self.config.retry_delay_sec * attempt)
        self._callback(replace(artifact, upload_state="FAILED", upload_error=last_error[:1024]))

    def _get_client(self) -> S3Client:
        if self._client is not None:
            return self._client
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:
            raise SpoolError("automatic upload requires the python3-boto3 package") from exc
        kwargs: dict[str, Any] = {}
        if self.config.endpoint_url:
            kwargs["endpoint_url"] = self.config.endpoint_url
        if self.config.region:
            kwargs["region_name"] = self.config.region
        if self.config.addressing_style != "auto":
            kwargs["config"] = Config(s3={"addressing_style": self.config.addressing_style})
        self._client = boto3.client("s3", **kwargs)
        return self._client
