import threading
from pathlib import Path

import pytest
from task_orchestrator_media.models import ArtifactRecord
from task_orchestrator_media.spool import SpoolError
from task_orchestrator_media.upload import S3ArtifactUploader, S3UploadConfig


class _FakeS3Client:
    def __init__(self):
        self.uploads = []
        self.presigns = []

    def upload_file(self, filename, bucket, key, ExtraArgs=None):
        self.uploads.append((filename, bucket, key, ExtraArgs))

    def generate_presigned_url(self, ClientMethod, Params=None, ExpiresIn=3600):
        self.presigns.append((ClientMethod, Params, ExpiresIn))
        return f"https://signed.example/{Params['Key']}?temporary=true"


class _FailingS3Client(_FakeS3Client):
    def upload_file(self, filename, bucket, key, ExtraArgs=None):
        super().upload_file(filename, bucket, key, ExtraArgs)
        raise RuntimeError("network unavailable")


def _artifact(path: Path) -> ArtifactRecord:
    return ArtifactRecord(
        artifact_id="artifact-1",
        artifact_kind="video",
        mime_type="video/mp4",
        device_id="front",
        storage_key="recordings/front/recording.mp4",
        size_bytes=path.stat().st_size,
        sha256="a" * 64,
        captured_ns=1,
        finished_ns=2,
        duration_sec=1.0,
        frame_id="front",
        task_id="task",
        mission_id="mission",
    )


def test_s3_uploader_sets_available_and_generates_temporary_access(tmp_path):
    media_path = tmp_path / "recording.mp4"
    media_path.write_bytes(b"video")
    client = _FakeS3Client()
    updates = []
    available = threading.Event()

    def callback(artifact):
        updates.append(artifact)
        if artifact.upload_state == "AVAILABLE":
            available.set()

    uploader = S3ArtifactUploader(
        S3UploadConfig(enabled=True, bucket="robot-media", prefix="fleet-a"),
        callback,
        client=client,
    )
    try:
        pending = uploader.enqueue(_artifact(media_path), media_path)
        assert pending.upload_state == "UPLOADING"
        assert available.wait(timeout=2.0)
        uploaded = updates[-1]
        assert uploaded.upload_state == "AVAILABLE"
        assert uploaded.remote_object_key == "fleet-a/recordings/front/recording.mp4"
        assert client.uploads[0][3]["ContentType"] == "video/mp4"

        access = uploader.access(uploaded, expires_sec=120)
        assert access.expires_sec == 120
        assert "temporary=true" in access.url
        assert client.presigns[0][2] == 120
    finally:
        uploader.close()


def test_public_base_url_is_returned_without_presigning(tmp_path):
    media_path = tmp_path / "recording.mp4"
    media_path.write_bytes(b"video")
    client = _FakeS3Client()
    updates = []
    available = threading.Event()

    def callback(artifact):
        updates.append(artifact)
        if artifact.upload_state == "AVAILABLE":
            available.set()

    uploader = S3ArtifactUploader(
        S3UploadConfig(
            enabled=True,
            bucket="robot-media",
            public_base_url="https://media.example/files",
        ),
        callback,
        client=client,
    )
    try:
        uploader.enqueue(_artifact(media_path), media_path)
        assert available.wait(timeout=2.0)
        access = uploader.access(updates[-1])
        assert access.expires_sec == 0
        assert access.url.startswith("https://media.example/files/")
        assert client.presigns == []
    finally:
        uploader.close()


def test_upload_config_rejects_plain_http_by_default():
    with pytest.raises(SpoolError, match="HTTP"):
        S3UploadConfig(
            enabled=True,
            bucket="robot-media",
            endpoint_url="http://minio:9000",
        ).validate()


def test_s3_uploader_retries_and_publishes_failed_state(tmp_path):
    media_path = tmp_path / "recording.mp4"
    media_path.write_bytes(b"video")
    client = _FailingS3Client()
    failed = threading.Event()
    updates = []

    def callback(artifact):
        updates.append(artifact)
        if artifact.upload_state == "FAILED":
            failed.set()

    uploader = S3ArtifactUploader(
        S3UploadConfig(
            enabled=True,
            bucket="robot-media",
            max_attempts=2,
            retry_delay_sec=0,
        ),
        callback,
        client=client,
    )
    try:
        uploader.enqueue(_artifact(media_path), media_path)
        assert failed.wait(timeout=2.0)
        assert len(client.uploads) == 2
        assert updates[-1].upload_state == "FAILED"
        assert "network unavailable" in updates[-1].upload_error
    finally:
        uploader.close()
