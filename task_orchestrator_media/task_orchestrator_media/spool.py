"""Crash-recoverable local media spool with bounded disk use."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from task_orchestrator_media.config import validate_extension, validate_identifier
from task_orchestrator_media.models import ArtifactRecord, AudioPacket, DeviceConfig, FramePacket, SessionRecord


class SpoolError(RuntimeError):
    """Base class for local media spool failures."""


class SpoolQuotaExceeded(SpoolError):
    """Raised before a write would exceed the configured disk budget."""


_MIN_FINALIZATION_RESERVE_BYTES = 256 * 1024
_MIN_EXTERNAL_RECORDING_BYTES = 8 * 1024 * 1024


class RecordingBundle:
    """Append-only image-sequence bundle finalized into a durable manifest."""

    def __init__(
        self,
        store: "SpoolStore",
        session_dir: Path,
        device: DeviceConfig,
        session: SessionRecord,
    ) -> None:
        self._store = store
        self._session_dir = session_dir
        self._device = device
        self._session = session
        self._entries: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._closed = False
        self._frames_dir = session_dir / "frames"
        self._frames_dir.mkdir(parents=True, exist_ok=True)
        self._journal_path = session_dir / "frames.jsonl"

    @property
    def frame_count(self) -> int:
        return len(self._entries)

    @property
    def size_bytes(self) -> int:
        return sum(int(entry["size_bytes"]) for entry in self._entries)

    def append(self, frame: FramePacket) -> None:
        """Persist one encoded frame and its recovery journal entry."""
        with self._lock:
            if self._closed:
                raise SpoolError(f"recording session is already closed: {self._session.session_id}")
            validate_extension(frame.extension, "frame.extension")
            frame_index = len(self._entries) + 1
            filename = f"{frame_index:08d}{frame.extension}"
            frame_path = self._frames_dir / filename
            digest = hashlib.sha256(frame.data).hexdigest()
            entry = {
                "index": frame_index,
                "filename": f"frames/{filename}",
                "captured_ns": frame.captured_ns,
                "frame_id": frame.frame_id,
                "mime_type": frame.mime_type,
                "size_bytes": len(frame.data),
                "sha256": digest,
                "source_format": frame.source_format,
            }
            journal_line = (json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
            self._store._reserve(len(frame.data) + len(journal_line))
            try:
                self._store._atomic_write_untracked(frame_path, frame.data)
                with self._journal_path.open("ab") as stream:
                    stream.write(journal_line)
                    stream.flush()
                    os.fsync(stream.fileno())
                self._entries.append(entry)
            except Exception:
                self._store._release(len(frame.data) + len(journal_line))
                try:
                    frame_path.unlink(missing_ok=True)
                except OSError:
                    pass
                raise

    def append_audio(self, _chunk: AudioPacket) -> None:
        """Reject audio explicitly for the low-dependency frame-sequence backend."""
        raise SpoolError("audio recording requires the ffmpeg_mp4 backend")

    def finalize(self, session: SessionRecord) -> ArtifactRecord | None:
        """Close the bundle and materialize its immutable manifest."""
        with self._lock:
            if self._closed:
                return session.artifacts[0] if session.artifacts else None
            artifact = self._store._finalize_recording(self._session_dir, session, self._entries)
            self._closed = True
            return artifact


class SpoolStore:
    """Own local artifact paths, manifests, checksums and disk accounting."""

    def __init__(self, root_path: str | Path, max_bytes: int = 0, min_free_bytes: int = 0) -> None:
        self.root = Path(root_path).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max(0, int(max_bytes))
        self.min_free_bytes = max(0, int(min_free_bytes))
        self._lock = threading.Lock()
        self._used_bytes = self._scan_used_bytes()

    @property
    def used_bytes(self) -> int:
        with self._lock:
            return self._used_bytes

    def write_snapshot(
        self,
        device: DeviceConfig,
        frame: FramePacket,
        task_id: str,
        mission_id: str,
        metadata_json: str,
    ) -> ArtifactRecord:
        """Write one encoded image and a sidecar artifact manifest."""
        validate_identifier(device.device_id, "device_id")
        validate_extension(frame.extension, "frame.extension")
        artifact_id = uuid4().hex
        date_path = self._date_path(frame.captured_ns)
        relative_path = Path("snapshots") / date_path / device.device_id / f"{artifact_id}{frame.extension}"
        media_path = self.root / relative_path
        digest = hashlib.sha256(frame.data).hexdigest()
        artifact_metadata = self._artifact_metadata(
            metadata_json,
            {
                "source_format": frame.source_format,
                "sidecar": f"{relative_path.as_posix()}.json",
            },
        )
        artifact = ArtifactRecord(
            artifact_id=artifact_id,
            artifact_kind=device.media_kind,
            mime_type=frame.mime_type or device.mime_type,
            device_id=device.device_id,
            storage_key=relative_path.as_posix(),
            size_bytes=len(frame.data),
            sha256=digest,
            captured_ns=frame.captured_ns,
            finished_ns=frame.captured_ns,
            duration_sec=0.0,
            frame_id=frame.frame_id or device.frame_id,
            task_id=task_id,
            mission_id=mission_id,
            metadata_json=artifact_metadata,
        )
        sidecar_path = media_path.with_name(f"{media_path.name}.json")
        sidecar_data = self._json_bytes({"schema": "media.artifact.v1", **artifact.to_dict()})
        self._reserve(len(frame.data) + len(sidecar_data))
        try:
            self._atomic_write_untracked(media_path, frame.data)
            self._atomic_write_untracked(sidecar_path, sidecar_data)
        except Exception:
            self._release(len(frame.data) + len(sidecar_data))
            for path in (media_path, sidecar_path):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise
        return artifact

    def begin_recording(self, device: DeviceConfig, session: SessionRecord) -> RecordingBundle:
        """Create the durable marker for a new recording session."""
        session_dir = self._prepare_recording(device, session)
        try:
            return RecordingBundle(self, session_dir, device, session)
        except Exception:
            self._cleanup_prepared_recording(session_dir)
            raise

    def prepare_external_recording(
        self,
        device: DeviceConfig,
        session: SessionRecord,
        requested_bytes: int,
    ) -> tuple[Path, int]:
        """Create a session directory and reserve bounded capacity for an external encoder."""
        if requested_bytes < _MIN_EXTERNAL_RECORDING_BYTES:
            raise SpoolQuotaExceeded(
                f"external recording requires at least {_MIN_EXTERNAL_RECORDING_BYTES} reserved bytes"
            )
        session_dir = self._prepare_recording(device, session)
        try:
            allocation_bytes = self._reserve_external_capacity(requested_bytes)
            return session_dir, allocation_bytes
        except Exception:
            self._cleanup_prepared_recording(session_dir)
            raise

    def commit_external_recording(self, reserved_bytes: int, actual_bytes: int) -> None:
        """Replace a logical encoder reservation with its actual on-disk byte count."""
        with self._lock:
            next_used_bytes = max(0, self._used_bytes - max(0, reserved_bytes)) + max(0, actual_bytes)
            self._used_bytes = next_used_bytes
            if self.max_bytes and next_used_bytes > self.max_bytes:
                raise SpoolQuotaExceeded(
                    f"external recording exceeded media spool quota: {next_used_bytes} > {self.max_bytes}"
                )

    def release_external_recording(self, reserved_bytes: int) -> None:
        """Release an unused logical encoder reservation."""
        self._release(reserved_bytes)

    def finalize_video_recording(
        self,
        session_dir: Path,
        session: SessionRecord,
        entries: list[dict[str, Any]],
        *,
        codec: str,
        recording_fps: float,
        fragmented: bool,
        audio_entries: list[dict[str, Any]],
        audio_mode: str,
        audio_codec: str,
    ) -> ArtifactRecord | None:
        """Finalize an encoded MP4 and persist its artifact sidecar and session state."""
        partial_path = session_dir / "recording.mp4.part"
        video_path = session_dir / "recording.mp4"
        if partial_path.is_file() and not video_path.exists():
            os.replace(partial_path, video_path)
        if video_path.is_file():
            self._fsync_file(video_path)
        audio_partial_path = session_dir / "recording-audio.m4a.part"
        audio_path = session_dir / "recording-audio.m4a"
        if audio_mode == "separate" and audio_partial_path.is_file() and not audio_path.exists():
            os.replace(audio_partial_path, audio_path)
        if audio_path.is_file():
            self._fsync_file(audio_path)

        self._unlink_counted(session_dir / ".finalize-reserve")
        session.frame_count = len(entries)
        session.audio_chunk_count = len(audio_entries)
        video_size = video_path.stat().st_size if video_path.is_file() else 0
        audio_size = audio_path.stat().st_size if audio_path.is_file() else 0
        session.size_bytes = video_size + audio_size
        if session.stopped_ns <= 0:
            session.stopped_ns = time.time_ns()

        artifact: ArtifactRecord | None = None
        artifacts: list[ArtifactRecord] = []
        if entries and video_size > 0:
            first_ns = int(entries[0].get("captured_ns", session.started_ns))
            last_ns = int(entries[-1].get("captured_ns", first_ns))
            playback_duration_sec = len(entries) / recording_fps if recording_fps > 0 else 0.0
            relative_path = video_path.relative_to(self.root)
            artifact = ArtifactRecord(
                artifact_id=uuid4().hex,
                artifact_kind="video",
                mime_type="video/mp4",
                device_id=session.device_id,
                storage_key=relative_path.as_posix(),
                size_bytes=video_size,
                sha256=self._sha256_file(video_path),
                captured_ns=first_ns,
                finished_ns=last_ns,
                duration_sec=playback_duration_sec,
                frame_id=str(entries[0].get("frame_id", "")),
                task_id=session.task_id,
                mission_id=session.mission_id,
                metadata_json=self._artifact_metadata(
                    session.metadata_json,
                    {
                        "backend": "ffmpeg_mp4",
                        "codec": codec,
                        "container": "mp4",
                        "fragmented": fragmented,
                        "frame_count": len(entries),
                        "frame_journal": "video_frames.jsonl",
                        "recording_fps": recording_fps,
                        "session_id": session.session_id,
                        "source_duration_sec": max(0.0, (last_ns - first_ns) / 1_000_000_000.0),
                        "audio": {
                            "chunk_count": len(audio_entries),
                            "codec": audio_codec if audio_mode == "mux" else "",
                            "device_id": session.audio_device_id,
                            "mode": audio_mode,
                        },
                    },
                ),
            )
            sidecar = {
                "schema": "media.video.v1",
                "artifact": artifact.to_dict(),
                "encoding": {
                    "backend": "ffmpeg_mp4",
                    "codec": codec,
                    "container": "mp4",
                    "fragmented": fragmented,
                    "recording_fps": recording_fps,
                    "audio_codec": audio_codec if audio_mode == "mux" else "",
                    "audio_mode": audio_mode,
                },
                "audio_chunk_count": len(audio_entries),
                "frame_count": len(entries),
                "first_frame": entries[0],
                "last_frame": entries[-1],
            }
            self._atomic_write_counted(
                video_path.with_name(f"{video_path.name}.json"),
                self._json_bytes(sidecar),
            )
            artifacts.append(artifact)
        elif not session.error_code:
            session.state = "ERROR"
            session.error_code = "NO_FRAMES"
            session.error_message = "Recording finished before an MP4 could be produced."

        if audio_mode == "separate" and audio_entries and audio_size > 0:
            first_audio_ns = int(audio_entries[0].get("captured_ns", session.started_ns))
            last_audio_ns = int(audio_entries[-1].get("captured_ns", first_audio_ns))
            audio_duration_sec = self._audio_duration_sec(audio_entries)
            audio_artifact = ArtifactRecord(
                artifact_id=uuid4().hex,
                artifact_kind="audio",
                mime_type="audio/mp4",
                device_id=session.audio_device_id,
                storage_key=audio_path.relative_to(self.root).as_posix(),
                size_bytes=audio_size,
                sha256=self._sha256_file(audio_path),
                captured_ns=first_audio_ns,
                finished_ns=last_audio_ns,
                duration_sec=audio_duration_sec,
                frame_id=str(audio_entries[0].get("frame_id", "")),
                task_id=session.task_id,
                mission_id=session.mission_id,
                metadata_json=self._artifact_metadata(
                    session.metadata_json,
                    {
                        "audio_chunk_count": len(audio_entries),
                        "backend": "ffmpeg_mp4",
                        "codec": audio_codec,
                        "container": "mp4",
                        "device_id": session.audio_device_id,
                        "sample_format": session.audio_sample_format,
                        "sample_rate_hz": session.audio_sample_rate_hz,
                        "channels": session.audio_channels,
                        "session_id": session.session_id,
                    },
                ),
            )
            self._atomic_write_counted(
                audio_path.with_name(f"{audio_path.name}.json"),
                self._json_bytes(
                    {
                        "schema": "media.audio.v1",
                        "artifact": audio_artifact.to_dict(),
                        "audio_chunk_count": len(audio_entries),
                        "first_chunk": audio_entries[0],
                        "last_chunk": audio_entries[-1],
                    }
                ),
            )
            artifacts.append(audio_artifact)
        elif audio_mode == "separate":
            self._unlink_counted(audio_path)

        if artifacts:
            session.artifacts = artifacts

        if artifact is None:
            self._unlink_counted(video_path)
            self._unlink_counted(session_dir / "video_frames.jsonl")
            session.size_bytes = audio_size

        self._atomic_write_counted(session_dir / "session.json", self._session_bytes(session))
        return artifact

    def _prepare_recording(self, device: DeviceConfig, session: SessionRecord) -> Path:
        validate_identifier(device.device_id, "device_id")
        validate_identifier(session.session_id, "session_id")
        session_dir = (
            self.root
            / "recordings"
            / self._date_path(session.started_ns)
            / device.device_id
            / session.session_id
        )
        if session_dir.exists():
            raise SpoolError(f"recording session path already exists: {session.session_id}")
        session_dir.mkdir(parents=True)
        try:
            session_data = self._session_bytes(session)
            self._atomic_write_counted(session_dir / "session.json", session_data)
            reserve_bytes = max(_MIN_FINALIZATION_RESERVE_BYTES, len(session_data) * 8)
            self._atomic_write_counted(session_dir / ".finalize-reserve", bytes(reserve_bytes))
            return session_dir
        except Exception:
            self._cleanup_prepared_recording(session_dir)
            raise

    def _cleanup_prepared_recording(self, session_dir: Path) -> None:
        self._unlink_counted(session_dir / ".finalize-reserve")
        self._unlink_counted(session_dir / "session.json")
        for directory in (session_dir / "frames", session_dir):
            try:
                directory.rmdir()
            except OSError:
                pass

    def recover_sessions(self) -> list[SessionRecord]:
        """Load known sessions and finalize journals left open by a process crash."""
        sessions: list[SessionRecord] = []
        recordings_root = self.root / "recordings"
        if not recordings_root.exists():
            return sessions
        for session_path in sorted(recordings_root.glob("**/session.json")):
            try:
                payload = json.loads(session_path.read_text(encoding="utf-8"))
                session = SessionRecord.from_dict(payload)
                if session.state == "RECORDING":
                    session.state = "RECOVERED"
                    session.stopped_ns = time.time_ns()
                    if session.recording_backend == "ffmpeg_mp4":
                        entries = self._read_video_journal(session_path.parent / "video_frames.jsonl")
                        audio_entries = self._read_video_journal(session_path.parent / "audio_chunks.jsonl")
                        codec = str(entries[0].get("video_codec", "unknown")) if entries else "unknown"
                        recording_fps = float(entries[0].get("recording_fps", 0.0)) if entries else 0.0
                        fragmented = bool(entries[0].get("fragmented", True)) if entries else True
                        audio_codec = (
                            str(audio_entries[0].get("audio_codec", "unknown"))
                            if audio_entries
                            else "unknown"
                        )
                        self.finalize_video_recording(
                            session_path.parent,
                            session,
                            entries,
                            codec=codec,
                            recording_fps=recording_fps,
                            fragmented=fragmented,
                            audio_entries=audio_entries,
                            audio_mode=session.audio_mode,
                            audio_codec=audio_codec,
                        )
                    else:
                        entries = self._read_journal(session_path.parent / "frames.jsonl")
                        self._finalize_recording(session_path.parent, session, entries)
                sessions.append(session)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
        return sessions

    def load_artifacts(self) -> list[ArtifactRecord]:
        """Load finalized artifact descriptors and overlay durable upload state."""
        artifacts: dict[str, ArtifactRecord] = {}
        upload_artifacts: dict[str, ArtifactRecord] = {}
        for sidecar_path in self.root.rglob("*.json"):
            try:
                payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
                schema = payload.get("schema") if isinstance(payload, dict) else None
                artifact_payload: Any = None
                if schema == "media.artifact.v1":
                    artifact_payload = {key: value for key, value in payload.items() if key != "schema"}
                elif schema in {"media.video.v1", "media.audio.v1", "media.frame_manifest.v1"}:
                    artifact_payload = payload.get("artifact")
                elif schema == "media.upload.v1":
                    artifact_payload = payload.get("artifact")
                if isinstance(artifact_payload, dict):
                    artifact = ArtifactRecord.from_dict(artifact_payload)
                    target = upload_artifacts if schema == "media.upload.v1" else artifacts
                    target[artifact.artifact_id] = artifact
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                continue
        artifacts.update(upload_artifacts)
        return list(artifacts.values())

    def artifact_path(self, artifact: ArtifactRecord) -> Path:
        """Resolve a trusted relative storage key below the spool root."""
        relative = Path(artifact.storage_key)
        if relative.is_absolute() or ".." in relative.parts:
            raise SpoolError("artifact storage_key must be relative to the media spool")
        path = (self.root / relative).resolve()
        if path != self.root and self.root not in path.parents:
            raise SpoolError("artifact storage_key escapes the media spool")
        return path

    def persist_artifact_upload(self, artifact: ArtifactRecord) -> None:
        """Persist transfer state without storing temporary signed access URLs."""
        media_path = self.artifact_path(artifact)
        upload_path = media_path.with_name(f"{media_path.name}.upload.json")
        payload = artifact.to_dict()
        payload["public_url"] = artifact.public_url
        self._atomic_write_counted(
            upload_path,
            self._json_bytes({"schema": "media.upload.v1", "artifact": payload}),
        )

    def persist_session(self, session: SessionRecord) -> None:
        """Update a finalized recording session after artifact state changes."""
        validate_identifier(session.session_id, "session_id")
        matches = list(self.root.glob(f"recordings/**/{session.session_id}/session.json"))
        if len(matches) != 1:
            return
        self._atomic_write_counted(matches[0], self._session_bytes(session))

    def _finalize_recording(
        self,
        session_dir: Path,
        session: SessionRecord,
        entries: list[dict[str, Any]],
    ) -> ArtifactRecord | None:
        # This physical reserve makes terminal metadata writable even when the
        # payload has consumed the rest of the configured spool quota.
        self._unlink_counted(session_dir / ".finalize-reserve")
        session.frame_count = len(entries)
        session.size_bytes = sum(int(entry.get("size_bytes", 0)) for entry in entries)
        if session.stopped_ns <= 0:
            session.stopped_ns = time.time_ns()

        artifact: ArtifactRecord | None = None
        if entries:
            first_ns = int(entries[0].get("captured_ns", session.started_ns))
            last_ns = int(entries[-1].get("captured_ns", first_ns))
            artifact_id = uuid4().hex
            aggregate_digest = self._bundle_digest(entries)
            manifest_relative = (session_dir / "manifest.json").relative_to(self.root)
            artifact = ArtifactRecord(
                artifact_id=artifact_id,
                artifact_kind=f"{session.media_kind}_sequence",
                mime_type="application/vnd.task-orchestrator.frame-manifest+json",
                device_id=session.device_id,
                storage_key=manifest_relative.as_posix(),
                size_bytes=session.size_bytes,
                sha256=aggregate_digest,
                captured_ns=first_ns,
                finished_ns=last_ns,
                duration_sec=max(0.0, (last_ns - first_ns) / 1_000_000_000.0),
                frame_id=str(entries[0].get("frame_id", "")),
                task_id=session.task_id,
                mission_id=session.mission_id,
                metadata_json=self._artifact_metadata(
                    session.metadata_json,
                    {
                        "bundle_format": "frame-manifest-v1",
                        "frame_count": len(entries),
                        "session_id": session.session_id,
                    },
                ),
            )
            manifest = {
                "schema": "media.frame_manifest.v1",
                "artifact": artifact.to_dict(),
                "frame_index": "frames.jsonl",
                "frame_count": len(entries),
                "first_frame": entries[0],
                "last_frame": entries[-1],
            }
            self._atomic_write_counted(session_dir / "manifest.json", self._json_bytes(manifest))
            session.artifacts = [artifact]
        elif not session.error_code:
            session.state = "ERROR"
            session.error_code = "NO_FRAMES"
            session.error_message = "Recording finished before any frames were received."

        self._atomic_write_counted(session_dir / "session.json", self._session_bytes(session))
        return artifact

    def _reserve(self, byte_count: int) -> None:
        if byte_count <= 0:
            return
        with self._lock:
            if self.max_bytes and self._used_bytes + byte_count > self.max_bytes:
                raise SpoolQuotaExceeded(
                    f"media spool quota would be exceeded: {self._used_bytes + byte_count} > {self.max_bytes}"
                )
            free_bytes = shutil.disk_usage(self.root).free
            if self.min_free_bytes and free_bytes - byte_count < self.min_free_bytes:
                raise SpoolQuotaExceeded(
                    f"media spool would leave less than {self.min_free_bytes} bytes free"
                )
            self._used_bytes += byte_count

    def _reserve_external_capacity(self, requested_bytes: int) -> int:
        with self._lock:
            quota_available = requested_bytes
            if self.max_bytes:
                quota_available = max(0, self.max_bytes - self._used_bytes)
            disk_available = max(0, shutil.disk_usage(self.root).free - self.min_free_bytes)
            allocation_bytes = min(requested_bytes, quota_available, disk_available)
            if allocation_bytes < _MIN_EXTERNAL_RECORDING_BYTES:
                raise SpoolQuotaExceeded(
                    "media spool cannot reserve the minimum capacity for an encoded recording"
                )
            self._used_bytes += allocation_bytes
            return allocation_bytes

    def _release(self, byte_count: int) -> None:
        if byte_count <= 0:
            return
        with self._lock:
            self._used_bytes = max(0, self._used_bytes - byte_count)

    def _atomic_write_counted(self, path: Path, data: bytes) -> None:
        old_size = path.stat().st_size if path.exists() else 0
        growth = max(0, len(data) - old_size)
        self._reserve(growth)
        try:
            self._atomic_write_untracked(path, data)
        except Exception:
            self._release(growth)
            raise
        if len(data) < old_size:
            self._release(old_size - len(data))

    def _atomic_write_untracked(self, path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = path.with_name(f".{path.name}.{uuid4().hex}.part")
        try:
            with temporary_path.open("wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, path)
        finally:
            temporary_path.unlink(missing_ok=True)

    def _fsync_file(self, path: Path) -> None:
        with path.open("rb") as stream:
            os.fsync(stream.fileno())

    def _unlink_counted(self, path: Path) -> None:
        try:
            byte_count = path.stat().st_size
            path.unlink()
        except FileNotFoundError:
            return
        self._release(byte_count)

    def _scan_used_bytes(self) -> int:
        return sum(path.stat().st_size for path in self.root.rglob("*") if path.is_file())

    def _date_path(self, timestamp_ns: int) -> str:
        seconds = max(0, timestamp_ns) / 1_000_000_000.0
        return datetime.fromtimestamp(seconds, tz=timezone.utc).strftime("%Y/%m/%d")

    def _session_bytes(self, session: SessionRecord) -> bytes:
        return self._json_bytes(session.to_dict())

    def _json_bytes(self, payload: dict[str, Any]) -> bytes:
        return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")

    def _artifact_metadata(self, metadata_json: str, system_metadata: dict[str, Any]) -> str:
        try:
            request_metadata = json.loads(metadata_json or "{}")
        except json.JSONDecodeError:
            request_metadata = {}
        payload = {
            "request": request_metadata if isinstance(request_metadata, dict) else {},
            **system_metadata,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    def _bundle_digest(self, entries: list[dict[str, Any]]) -> str:
        digest = hashlib.sha256()
        for entry in entries:
            digest.update(str(entry.get("sha256", "")).encode("ascii"))
            digest.update(str(entry.get("size_bytes", 0)).encode("ascii"))
            digest.update(str(entry.get("captured_ns", 0)).encode("ascii"))
        return digest.hexdigest()

    def _sha256_file(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _audio_duration_sec(self, entries: list[dict[str, Any]]) -> float:
        if not entries:
            return 0.0
        first = entries[0]
        bytes_per_sample = {
            "u8": 1,
            "s16le": 2,
            "s24le": 3,
            "s32le": 4,
            "f32le": 4,
            "f64le": 8,
        }.get(str(first.get("sample_format", "")), 0)
        sample_rate = int(first.get("sample_rate_hz", 0))
        channels = int(first.get("channels", 0))
        denominator = bytes_per_sample * sample_rate * channels
        if denominator <= 0:
            return 0.0
        return sum(int(entry.get("size_bytes", 0)) for entry in entries) / denominator

    def _read_journal(self, journal_path: Path) -> list[dict[str, Any]]:
        if not journal_path.exists():
            return []
        entries: list[dict[str, Any]] = []
        with journal_path.open("r", encoding="utf-8") as stream:
            for line in stream:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                frame_path = journal_path.parent / str(entry.get("filename", ""))
                if isinstance(entry, dict) and frame_path.is_file():
                    entries.append(entry)
        return entries

    def _read_video_journal(self, journal_path: Path) -> list[dict[str, Any]]:
        if not journal_path.exists():
            return []
        entries: list[dict[str, Any]] = []
        with journal_path.open("r", encoding="utf-8") as stream:
            for line in stream:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(entry, dict) and isinstance(entry.get("captured_ns"), int):
                    entries.append(entry)
        return entries
