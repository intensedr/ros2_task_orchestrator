"""Dependency-free value objects used by the media manager."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class DeviceConfig:
    """Configured compressed-image source."""

    device_id: str
    source_topic: str
    display_name: str = ""
    media_kind: str = "image"
    mime_type: str = "image/jpeg"
    extension: str = ".jpg"
    frame_id: str = ""
    capabilities: tuple[str, ...] = ("snapshot", "recording")
    metadata_json: str = "{}"


@dataclass(frozen=True)
class AudioDeviceConfig:
    """Configured raw PCM source."""

    device_id: str
    source_topic: str
    display_name: str = ""
    message_type: str = "task_orchestrator_media_msgs/msg/AudioChunkV1"
    sample_format: str = "s16le"
    sample_rate_hz: int = 48_000
    channels: int = 1
    frame_id: str = ""
    capabilities: tuple[str, ...] = ("recording",)
    metadata_json: str = "{}"


@dataclass(frozen=True)
class FramePacket:
    """One immutable encoded frame received from ROS."""

    sequence: int
    data: bytes
    captured_ns: int
    frame_id: str
    mime_type: str
    extension: str
    source_format: str = ""


@dataclass(frozen=True)
class AudioPacket:
    """One immutable interleaved PCM chunk received from ROS."""

    sequence: int
    data: bytes
    captured_ns: int
    sample_format: str
    sample_rate_hz: int
    channels: int
    frame_id: str = ""


@dataclass(frozen=True)
class ArtifactRecord:
    """Stable metadata for a file or logical media bundle."""

    artifact_id: str
    artifact_kind: str
    mime_type: str
    device_id: str
    storage_key: str
    size_bytes: int
    sha256: str
    captured_ns: int
    finished_ns: int
    duration_sec: float
    frame_id: str
    task_id: str
    mission_id: str
    upload_state: str = "LOCAL"
    remote_object_key: str = ""
    public_url: str = ""
    upload_error: str = ""
    metadata_json: str = "{}"
    api_version: str = "v1"

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable manifest representation."""
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ArtifactRecord":
        """Restore an artifact from a manifest."""
        return cls(**value)


@dataclass
class SessionRecord:
    """Mutable recording-session state owned by the manager."""

    session_id: str
    device_id: str
    media_kind: str
    state: str
    task_id: str
    mission_id: str
    idempotency_key: str
    started_ns: int
    max_duration_sec: float
    metadata_json: str
    recording_backend: str = "frame_sequence"
    audio_device_id: str = ""
    audio_mode: str = "none"
    audio_sample_format: str = ""
    audio_sample_rate_hz: int = 0
    audio_channels: int = 0
    stopped_ns: int = 0
    frame_count: int = 0
    dropped_frames: int = 0
    audio_chunk_count: int = 0
    dropped_audio_chunks: int = 0
    size_bytes: int = 0
    artifacts: list[ArtifactRecord] = field(default_factory=list)
    error_code: str = ""
    error_message: str = ""
    api_version: str = "v1"

    @property
    def duration_sec(self) -> float:
        end_ns = self.stopped_ns or self.started_ns
        return max(0.0, (end_ns - self.started_ns) / 1_000_000_000.0)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable persistent representation."""
        payload = asdict(self)
        payload["artifacts"] = [artifact.to_dict() for artifact in self.artifacts]
        return payload

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SessionRecord":
        """Restore a session from its persistent representation."""
        payload = dict(value)
        payload["artifacts"] = [ArtifactRecord.from_dict(item) for item in payload.get("artifacts", [])]
        return cls(**payload)
