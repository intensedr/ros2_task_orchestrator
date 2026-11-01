"""ROS2 node exposing snapshot and background recording media operations."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import replace
from functools import partial
from typing import Any
from uuid import uuid4

import rclpy
from builtin_interfaces.msg import Time
from rclpy.action import ActionServer, CancelResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from rosidl_runtime_py.utilities import get_message
from sensor_msgs.msg import CompressedImage

from task_orchestrator_media.config import (
    MediaConfigError,
    normalize_json_object,
    parse_audio_devices_json,
    parse_devices_json,
    validate_identifier,
)
from task_orchestrator_media.ffmpeg_backend import AudioInputConfig, FFmpegConfig, create_ffmpeg_mp4_bundle
from task_orchestrator_media.models import ArtifactRecord, AudioDeviceConfig, DeviceConfig, SessionRecord
from task_orchestrator_media.runtime import AudioBuffer, FrameBuffer, RecordingSink, RecordingWorker
from task_orchestrator_media.spool import SpoolError, SpoolQuotaExceeded, SpoolStore
from task_orchestrator_media.upload import S3ArtifactUploader, S3UploadConfig
from task_orchestrator_media_msgs.action import CaptureMediaV1  # type: ignore[attr-defined]
from task_orchestrator_media_msgs.msg import (  # type: ignore[attr-defined]
    MediaArtifactV1,
    MediaDeviceV1,
    MediaSessionV1,
)
from task_orchestrator_media_msgs.srv import (  # type: ignore[attr-defined]
    GetMediaArtifactAccessV1,
    GetMediaSessionV1,
    ListMediaDevicesV1,
    StartRecordingV1,
    StopRecordingV1,
)
from task_orchestrator_msgs.msg import TaskEventV1  # type: ignore[attr-defined]

MEDIA_CAPTURE_ACTION = "/task_orchestrator/media/capture"
MEDIA_START_RECORDING_SERVICE = "/task_orchestrator/media/start_recording"
MEDIA_STOP_RECORDING_SERVICE = "/task_orchestrator/media/stop_recording"
MEDIA_GET_SESSION_SERVICE = "/task_orchestrator/media/get_session"
MEDIA_GET_ARTIFACT_ACCESS_SERVICE = "/task_orchestrator/media/get_artifact_access"
MEDIA_LIST_DEVICES_SERVICE = "/task_orchestrator/media/list_devices"
ORCHESTRATOR_EVENTS_TOPIC = "/task_orchestrator/events"

_MISSION_TERMINAL_EVENTS = {
    "mission.completed",
    "mission.failed",
    "mission.canceled",
    "mission.timeout",
}


class MediaManagerNode(Node):
    """Capture compressed images and manage local background recording sessions."""

    def __init__(self, parameter_overrides: list[Any] | None = None) -> None:
        super().__init__("media_manager", parameter_overrides=parameter_overrides or [])
        self._declare_parameters()
        self.api_version = str(self.get_parameter("api_version").value)
        self._max_capture_count = max(1, int(self.get_parameter("max_capture_count").value))
        self._default_capture_timeout_sec = max(
            0.1,
            float(self.get_parameter("default_capture_timeout_sec").value),
        )
        self._max_capture_timeout_sec = max(
            self._default_capture_timeout_sec,
            float(self.get_parameter("max_capture_timeout_sec").value),
        )
        self._recording_queue_size = max(1, int(self.get_parameter("recording_queue_size").value))
        self._recording_queue_max_bytes = max(
            1,
            int(self.get_parameter("recording_queue_max_bytes").value),
        )
        self._max_frame_bytes = max(1, int(self.get_parameter("max_frame_bytes").value))
        self._max_audio_chunk_bytes = max(
            1,
            int(self.get_parameter("max_audio_chunk_bytes").value),
        )
        self._device_stale_after_sec = max(
            0.1,
            float(self.get_parameter("device_stale_after_sec").value),
        )
        self._session_stop_timeout_sec = max(
            0.1,
            float(self.get_parameter("session_stop_timeout_sec").value),
        )
        self._max_metadata_bytes = max(2, int(self.get_parameter("max_metadata_bytes").value))
        self._auto_finalize_on_mission_terminal = bool(
            self.get_parameter("auto_finalize_on_mission_terminal").value
        )
        self._recording_backend = str(self.get_parameter("recording_backend").value)
        if self._recording_backend not in {"ffmpeg_mp4", "frame_sequence"}:
            raise MediaConfigError("recording_backend must be 'ffmpeg_mp4' or 'frame_sequence'")
        ffmpeg_config = FFmpegConfig(
            binary=str(self.get_parameter("ffmpeg_binary").value),
            codec=str(self.get_parameter("ffmpeg_video_codec").value),
            preset=str(self.get_parameter("ffmpeg_preset").value),
            crf=int(self.get_parameter("ffmpeg_crf").value),
            recording_fps=float(self.get_parameter("recording_fps").value),
            max_recording_bytes=int(self.get_parameter("max_recording_bytes").value),
            finalize_timeout_sec=float(self.get_parameter("ffmpeg_finalize_timeout_sec").value),
            threads=int(self.get_parameter("ffmpeg_threads").value),
            fragmented_mp4=True,
            audio_codec=str(self.get_parameter("ffmpeg_audio_codec").value),
            audio_bitrate_kbps=int(self.get_parameter("ffmpeg_audio_bitrate_kbps").value),
        )
        self._ffmpeg_config = ffmpeg_config.validate() if self._recording_backend == "ffmpeg_mp4" else ffmpeg_config

        self._devices = {
            device.device_id: device
            for device in parse_devices_json(str(self.get_parameter("devices_json").value))
        }
        self._buffers = {device_id: FrameBuffer() for device_id in self._devices}
        self._audio_devices = {
            device.device_id: device
            for device in parse_audio_devices_json(str(self.get_parameter("audio_devices_json").value))
        }
        duplicate_ids = set(self._devices).intersection(self._audio_devices)
        if duplicate_ids:
            raise MediaConfigError(f"device IDs must be globally unique: {sorted(duplicate_ids)}")
        self._audio_buffers = {device_id: AudioBuffer() for device_id in self._audio_devices}
        self._spool = SpoolStore(
            root_path=str(self.get_parameter("spool_path").value),
            max_bytes=int(self.get_parameter("max_spool_bytes").value),
            min_free_bytes=int(self.get_parameter("min_free_bytes").value),
        )

        self._state_lock = threading.RLock()
        self._workers: dict[str, RecordingWorker] = {}
        self._sessions = {session.session_id: session for session in self._spool.recover_sessions()}
        loaded_artifacts = {artifact.artifact_id: artifact for artifact in self._spool.load_artifacts()}
        for session in self._sessions.values():
            session.artifacts = [loaded_artifacts.get(item.artifact_id, item) for item in session.artifacts]
        self._artifacts = loaded_artifacts
        self._recording_idempotency: dict[str, str] = {
            session.idempotency_key: session.session_id
            for session in self._sessions.values()
            if session.idempotency_key
        }
        self._capture_idempotency: dict[str, list[ArtifactRecord]] = {}
        self._finalizing_sessions: set[str] = set()
        self._rejected_frames = {device_id: 0 for device_id in self._devices}
        self._rejected_audio_chunks = {device_id: 0 for device_id in self._audio_devices}
        self._uploader = S3ArtifactUploader(
            S3UploadConfig(
                enabled=bool(self.get_parameter("upload_enabled").value),
                bucket=str(self.get_parameter("upload_bucket").value),
                prefix=str(self.get_parameter("upload_prefix").value),
                endpoint_url=str(self.get_parameter("upload_endpoint_url").value),
                region=str(self.get_parameter("upload_region").value),
                public_base_url=str(self.get_parameter("upload_public_base_url").value),
                addressing_style=str(self.get_parameter("upload_addressing_style").value),
                allow_insecure_endpoint=bool(
                    self.get_parameter("upload_allow_insecure_endpoint").value
                ),
                max_attempts=int(self.get_parameter("upload_max_attempts").value),
                retry_delay_sec=float(self.get_parameter("upload_retry_delay_sec").value),
                presign_default_sec=int(self.get_parameter("upload_presign_default_sec").value),
                presign_max_sec=int(self.get_parameter("upload_presign_max_sec").value),
                server_side_encryption=str(
                    self.get_parameter("upload_server_side_encryption").value
                ),
                kms_key_id=str(self.get_parameter("upload_kms_key_id").value),
            ),
            self._on_upload_update,
        )

        self._callback_group = ReentrantCallbackGroup()
        self._frame_subscriptions: list[Any] = []
        for device in self._devices.values():
            subscription = self.create_subscription(
                CompressedImage,
                device.source_topic,
                partial(self._on_frame, device.device_id),
                qos_profile_sensor_data,
                callback_group=self._callback_group,
            )
            self._frame_subscriptions.append(subscription)
        self._audio_subscriptions: list[Any] = []
        for audio_device in self._audio_devices.values():
            try:
                message_class = get_message(audio_device.message_type)
            except (AttributeError, ImportError, ModuleNotFoundError, ValueError) as exc:
                raise MediaConfigError(
                    f"Could not load audio message type {audio_device.message_type}: {exc}"
                ) from exc
            subscription = self.create_subscription(
                message_class,
                audio_device.source_topic,
                partial(self._on_audio, audio_device.device_id),
                qos_profile_sensor_data,
                callback_group=self._callback_group,
            )
            self._audio_subscriptions.append(subscription)

        self._capture_server = ActionServer(
            self,
            CaptureMediaV1,
            MEDIA_CAPTURE_ACTION,
            execute_callback=self._capture_media,
            cancel_callback=lambda _request: CancelResponse.ACCEPT,
            callback_group=self._callback_group,
        )
        self._start_service = self.create_service(
            StartRecordingV1,
            MEDIA_START_RECORDING_SERVICE,
            self._start_recording,
            callback_group=self._callback_group,
        )
        self._stop_service = self.create_service(
            StopRecordingV1,
            MEDIA_STOP_RECORDING_SERVICE,
            self._stop_recording,
            callback_group=self._callback_group,
        )
        self._get_session_service = self.create_service(
            GetMediaSessionV1,
            MEDIA_GET_SESSION_SERVICE,
            self._get_session,
            callback_group=self._callback_group,
        )
        self._get_artifact_access_service = self.create_service(
            GetMediaArtifactAccessV1,
            MEDIA_GET_ARTIFACT_ACCESS_SERVICE,
            self._get_artifact_access,
            callback_group=self._callback_group,
        )
        self._list_devices_service = self.create_service(
            ListMediaDevicesV1,
            MEDIA_LIST_DEVICES_SERVICE,
            self._list_devices,
            callback_group=self._callback_group,
        )
        self._event_subscription = self.create_subscription(
            TaskEventV1,
            ORCHESTRATOR_EVENTS_TOPIC,
            self._on_orchestrator_event,
            100,
            callback_group=self._callback_group,
        )
        self._maintenance_timer = self.create_timer(
            0.5,
            self._maintain_sessions,
            callback_group=self._callback_group,
        )
        self.get_logger().info(
            f"Media manager ready with {len(self._devices)} image and "
            f"{len(self._audio_devices)} audio device(s); "
            f"recording_backend={self._recording_backend}; spool={self._spool.root}"
        )
        for artifact in list(self._artifacts.values()):
            if artifact.upload_state in {"LOCAL", "FAILED", "UPLOADING"}:
                self._schedule_upload(artifact)

    def destroy_node(self) -> bool:
        for session_id in self._active_session_ids():
            self._finish_session(session_id, final_state="FINALIZED")
        self._uploader.close()
        self._capture_server.destroy()
        return super().destroy_node()

    def _declare_parameters(self) -> None:
        self.declare_parameter("api_version", "v1")
        self.declare_parameter("spool_path", "~/.local/share/task_orchestrator/media")
        self.declare_parameter("max_spool_bytes", 10 * 1024 * 1024 * 1024)
        self.declare_parameter("min_free_bytes", 256 * 1024 * 1024)
        self.declare_parameter("devices_json", "[]")
        self.declare_parameter("audio_devices_json", "[]")
        self.declare_parameter("max_capture_count", 100)
        self.declare_parameter("default_capture_timeout_sec", 5.0)
        self.declare_parameter("max_capture_timeout_sec", 120.0)
        self.declare_parameter("recording_queue_size", 120)
        self.declare_parameter("recording_queue_max_bytes", 128 * 1024 * 1024)
        self.declare_parameter("max_frame_bytes", 16 * 1024 * 1024)
        self.declare_parameter("max_audio_chunk_bytes", 4 * 1024 * 1024)
        self.declare_parameter("device_stale_after_sec", 5.0)
        self.declare_parameter("session_stop_timeout_sec", 30.0)
        self.declare_parameter("max_metadata_bytes", 16384)
        self.declare_parameter("auto_finalize_on_mission_terminal", True)
        self.declare_parameter("recording_backend", "ffmpeg_mp4")
        self.declare_parameter("ffmpeg_binary", "ffmpeg")
        self.declare_parameter("ffmpeg_video_codec", "libx264")
        self.declare_parameter("ffmpeg_preset", "veryfast")
        self.declare_parameter("ffmpeg_crf", 23)
        self.declare_parameter("recording_fps", 30.0)
        self.declare_parameter("max_recording_bytes", 2 * 1024 * 1024 * 1024)
        self.declare_parameter("ffmpeg_finalize_timeout_sec", 20.0)
        self.declare_parameter("ffmpeg_threads", 0)
        self.declare_parameter("ffmpeg_audio_codec", "aac")
        self.declare_parameter("ffmpeg_audio_bitrate_kbps", 128)
        self.declare_parameter("upload_enabled", False)
        self.declare_parameter("upload_bucket", "")
        self.declare_parameter("upload_prefix", "task-orchestrator")
        self.declare_parameter("upload_endpoint_url", "")
        self.declare_parameter("upload_region", "")
        self.declare_parameter("upload_public_base_url", "")
        self.declare_parameter("upload_addressing_style", "auto")
        self.declare_parameter("upload_allow_insecure_endpoint", False)
        self.declare_parameter("upload_max_attempts", 3)
        self.declare_parameter("upload_retry_delay_sec", 1.0)
        self.declare_parameter("upload_presign_default_sec", 900)
        self.declare_parameter("upload_presign_max_sec", 3600)
        self.declare_parameter("upload_server_side_encryption", "")
        self.declare_parameter("upload_kms_key_id", "")

    def _on_frame(self, device_id: str, message: CompressedImage) -> None:
        frame_size = len(message.data)
        if frame_size == 0 or frame_size > self._max_frame_bytes:
            with self._state_lock:
                self._rejected_frames[device_id] += 1
                rejected_frames = self._rejected_frames[device_id]
            if rejected_frames == 1 or rejected_frames % 100 == 0:
                reason = "empty" if frame_size == 0 else f"larger than {self._max_frame_bytes} bytes"
                self.get_logger().warning(
                    f"Rejected {reason} frame from {device_id}; rejected_frames={rejected_frames}"
                )
            return
        device = self._devices[device_id]
        timestamp_ns = self._message_timestamp_ns(message)
        mime_type, extension = self._encoded_format(device, str(message.format))
        packet = self._buffers[device_id].push(
            data=bytes(message.data),
            captured_ns=timestamp_ns,
            frame_id=str(message.header.frame_id or device.frame_id),
            mime_type=mime_type,
            extension=extension,
            source_format=str(message.format),
        )
        with self._state_lock:
            workers = [worker for worker in self._workers.values() if worker.device_id == device_id]
        for worker in workers:
            worker.enqueue(packet)

    def _on_audio(self, device_id: str, message: Any) -> None:
        """Normalize raw PCM messages and fan them out to matching sessions."""
        device = self._audio_devices[device_id]
        payload = bytes(message.data)
        if not payload or len(payload) > self._max_audio_chunk_bytes:
            with self._state_lock:
                self._rejected_audio_chunks[device_id] += 1
            return
        sample_format = str(getattr(message, "sample_format", "") or device.sample_format)
        sample_rate_hz = int(getattr(message, "sample_rate_hz", 0) or device.sample_rate_hz)
        channels = int(getattr(message, "channels", 0) or device.channels)
        if (
            sample_format != device.sample_format
            or sample_rate_hz != device.sample_rate_hz
            or channels != device.channels
        ):
            with self._state_lock:
                self._rejected_audio_chunks[device_id] += 1
            return
        timestamp_ns = self._audio_message_timestamp_ns(message)
        header = getattr(message, "header", None)
        frame_id = str(getattr(header, "frame_id", "") or device.frame_id)
        packet = self._audio_buffers[device_id].push(
            data=payload,
            captured_ns=timestamp_ns,
            sample_format=sample_format,
            sample_rate_hz=sample_rate_hz,
            channels=channels,
            frame_id=frame_id,
        )
        with self._state_lock:
            workers = [
                worker
                for worker in self._workers.values()
                if worker.audio_device_id == device_id
            ]
        for worker in workers:
            worker.enqueue_audio(packet)

    def _capture_media(self, goal_handle: Any) -> CaptureMediaV1.Result:
        request = goal_handle.request
        result = CaptureMediaV1.Result()
        device = self._devices.get(request.device_id)
        if device is None:
            return self._abort_capture(goal_handle, result, "UNKNOWN_DEVICE", f"Unknown device: {request.device_id}")
        if "snapshot" not in device.capabilities:
            return self._abort_capture(
                goal_handle,
                result,
                "UNSUPPORTED",
                f"Device does not support snapshots: {request.device_id}",
            )
        if request.api_version and request.api_version != self.api_version:
            return self._abort_capture(
                goal_handle,
                result,
                "UNSUPPORTED_API_VERSION",
                f"Expected API version {self.api_version}, got {request.api_version}",
            )
        try:
            metadata_json = self._request_metadata(request.metadata_json)
        except MediaConfigError as exc:
            return self._abort_capture(goal_handle, result, "INVALID_REQUEST", str(exc))

        count = int(request.count or 1)
        if count < 1 or count > self._max_capture_count:
            return self._abort_capture(
                goal_handle,
                result,
                "INVALID_REQUEST",
                f"count must be between 1 and {self._max_capture_count}",
            )
        if request.interval_sec < 0 or request.timeout_sec < 0:
            return self._abort_capture(
                goal_handle,
                result,
                "INVALID_REQUEST",
                "interval_sec and timeout_sec must be non-negative",
            )
        if request.idempotency_key:
            with self._state_lock:
                cached = self._capture_idempotency.get(request.idempotency_key)
            if cached is not None:
                result.success = True
                result.artifacts = [self._artifact_to_msg(artifact) for artifact in cached]
                goal_handle.succeed()
                return result

        timeout_sec = float(request.timeout_sec or self._default_capture_timeout_sec)
        if timeout_sec > self._max_capture_timeout_sec:
            return self._abort_capture(
                goal_handle,
                result,
                "INVALID_REQUEST",
                f"timeout_sec must not exceed {self._max_capture_timeout_sec}",
            )
        deadline = time.monotonic() + timeout_sec
        sequence = self._buffers[device.device_id].sequence
        artifacts: list[ArtifactRecord] = []
        try:
            for index in range(count):
                if goal_handle.is_cancel_requested:
                    result.error_code = "CANCELED"
                    result.error_message = "Media capture was canceled."
                    result.artifacts = [self._artifact_to_msg(artifact) for artifact in artifacts]
                    goal_handle.canceled()
                    return result
                if index > 0 and not self._wait_interval(goal_handle, float(request.interval_sec)):
                    result.error_code = "CANCELED"
                    result.error_message = "Media capture was canceled."
                    result.artifacts = [self._artifact_to_msg(artifact) for artifact in artifacts]
                    goal_handle.canceled()
                    return result
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Timed out waiting for a compressed image frame.")
                frame = self._buffers[device.device_id].wait_for_next(
                    after_sequence=sequence,
                    timeout_sec=remaining,
                    canceled=lambda: bool(goal_handle.is_cancel_requested),
                )
                if frame is None:
                    if goal_handle.is_cancel_requested:
                        result.error_code = "CANCELED"
                        result.error_message = "Media capture was canceled."
                        result.artifacts = [self._artifact_to_msg(artifact) for artifact in artifacts]
                        goal_handle.canceled()
                        return result
                    raise TimeoutError("Timed out waiting for a compressed image frame.")
                sequence = frame.sequence
                artifact = self._spool.write_snapshot(
                    device=device,
                    frame=frame,
                    task_id=request.task_id,
                    mission_id=request.mission_id,
                    metadata_json=metadata_json,
                )
                artifact = self._schedule_upload(artifact)
                artifacts.append(artifact)
                feedback = CaptureMediaV1.Feedback()
                feedback.captured_count = len(artifacts)
                feedback.total_count = count
                feedback.current_artifact_id = artifact.artifact_id
                goal_handle.publish_feedback(feedback)
        except TimeoutError as exc:
            result.artifacts = [self._artifact_to_msg(artifact) for artifact in artifacts]
            return self._abort_capture(goal_handle, result, "FRAME_TIMEOUT", str(exc))
        except SpoolQuotaExceeded as exc:
            result.artifacts = [self._artifact_to_msg(artifact) for artifact in artifacts]
            return self._abort_capture(goal_handle, result, "STORAGE_FULL", str(exc))
        except Exception as exc:  # noqa: BLE001 - translate backend failures into the action contract.
            result.artifacts = [self._artifact_to_msg(artifact) for artifact in artifacts]
            return self._abort_capture(goal_handle, result, "CAPTURE_FAILED", str(exc))

        if request.idempotency_key:
            with self._state_lock:
                self._capture_idempotency[request.idempotency_key] = list(artifacts)
        result.success = True
        result.artifacts = [self._artifact_to_msg(artifact) for artifact in artifacts]
        goal_handle.succeed()
        return result

    def _start_recording(
        self,
        request: StartRecordingV1.Request,
        response: StartRecordingV1.Response,
    ) -> StartRecordingV1.Response:
        device = self._devices.get(request.device_id)
        if device is None:
            return self._recording_error(response, "UNKNOWN_DEVICE", f"Unknown device: {request.device_id}")
        if "recording" not in device.capabilities:
            return self._recording_error(
                response,
                "UNSUPPORTED",
                f"Device does not support recording: {request.device_id}",
            )
        if request.api_version and request.api_version != self.api_version:
            return self._recording_error(
                response,
                "UNSUPPORTED_API_VERSION",
                f"Expected API version {self.api_version}, got {request.api_version}",
            )
        if request.max_duration_sec < 0:
            return self._recording_error(response, "INVALID_REQUEST", "max_duration_sec must be non-negative")
        audio_device_id = str(request.audio_device_id)
        audio_mode = str(request.audio_mode or ("mux" if audio_device_id else "none")).lower()
        if audio_mode not in {"none", "mux", "separate"}:
            return self._recording_error(
                response,
                "INVALID_REQUEST",
                "audio_mode must be 'none', 'mux' or 'separate'",
            )
        audio_device = self._audio_devices.get(audio_device_id) if audio_device_id else None
        if audio_mode != "none" and audio_device is None:
            return self._recording_error(
                response,
                "UNKNOWN_AUDIO_DEVICE",
                f"Unknown audio device: {audio_device_id}",
            )
        if audio_mode == "none" and audio_device_id:
            return self._recording_error(
                response,
                "INVALID_REQUEST",
                "audio_device_id must be empty when audio_mode is 'none'",
            )
        if audio_device is not None and "recording" not in audio_device.capabilities:
            return self._recording_error(
                response,
                "UNSUPPORTED",
                f"Audio device does not support recording: {audio_device_id}",
            )
        if audio_device is not None and self._recording_backend != "ffmpeg_mp4":
            return self._recording_error(
                response,
                "UNSUPPORTED",
                "audio recording requires recording_backend=ffmpeg_mp4",
            )
        try:
            metadata_json = self._request_metadata(request.metadata_json)
            session_id = validate_identifier(request.session_id or uuid4().hex, "session_id")
        except MediaConfigError as exc:
            return self._recording_error(response, "INVALID_REQUEST", str(exc))

        with self._state_lock:
            if request.idempotency_key:
                existing_id = self._recording_idempotency.get(request.idempotency_key)
                if existing_id:
                    existing = self._session_view_locked(existing_id)
                    if existing is not None:
                        response.success = existing.state != "ERROR"
                        response.error_code = existing.error_code
                        response.error_message = existing.error_message
                        response.session = self._session_to_msg(existing)
                        return response
            existing_session = self._session_view_locked(session_id)
            if existing_session is not None:
                return self._recording_error(
                    response,
                    "DUPLICATE_SESSION_ID",
                    f"Session ID already exists: {session_id}",
                )
            for worker in self._workers.values():
                if worker.device_id == device.device_id and worker.is_alive:
                    return self._recording_error(
                        response,
                        "SESSION_ALREADY_ACTIVE",
                        f"Device already has an active recording: {device.device_id}",
                    )
                if audio_device_id and worker.audio_device_id == audio_device_id and worker.is_alive:
                    return self._recording_error(
                        response,
                        "SESSION_ALREADY_ACTIVE",
                        f"Audio device already has an active recording: {audio_device_id}",
                    )

            session = SessionRecord(
                session_id=session_id,
                device_id=device.device_id,
                media_kind=device.media_kind,
                state="RECORDING",
                task_id=request.task_id,
                mission_id=request.mission_id,
                idempotency_key=request.idempotency_key,
                started_ns=time.time_ns(),
                max_duration_sec=float(request.max_duration_sec),
                metadata_json=metadata_json,
                recording_backend=self._recording_backend,
                audio_device_id=audio_device_id,
                audio_mode=audio_mode,
                audio_sample_format=audio_device.sample_format if audio_device is not None else "",
                audio_sample_rate_hz=audio_device.sample_rate_hz if audio_device is not None else 0,
                audio_channels=audio_device.channels if audio_device is not None else 0,
            )
            try:
                bundle: RecordingSink
                if self._recording_backend == "ffmpeg_mp4":
                    bundle = create_ffmpeg_mp4_bundle(
                        self._spool,
                        device,
                        session,
                        self._ffmpeg_config,
                        audio_input=(
                            AudioInputConfig(device=audio_device, mode=audio_mode)
                            if audio_device is not None
                            else None
                        ),
                    )
                else:
                    bundle = self._spool.begin_recording(device, session)
                worker = RecordingWorker(
                    bundle,
                    session,
                    queue_size=self._recording_queue_size,
                    queue_max_bytes=self._recording_queue_max_bytes,
                )
            except SpoolQuotaExceeded as exc:
                return self._recording_error(response, "STORAGE_FULL", str(exc))
            except (OSError, SpoolError) as exc:
                return self._recording_error(response, "START_FAILED", str(exc))
            self._workers[session_id] = worker
            self._sessions[session_id] = session
            if request.idempotency_key:
                self._recording_idempotency[request.idempotency_key] = session_id

        response.success = True
        response.session = self._session_to_msg(session)
        return response

    def _stop_recording(
        self,
        request: StopRecordingV1.Request,
        response: StopRecordingV1.Response,
    ) -> StopRecordingV1.Response:
        session = self._finish_session(request.session_id, final_state="FINALIZED")
        if session is None:
            response.success = False
            response.error_code = "SESSION_NOT_FOUND"
            response.error_message = f"Unknown recording session: {request.session_id}"
            return response
        response.success = session.state != "ERROR"
        response.error_code = session.error_code
        response.error_message = session.error_message
        response.session = self._session_to_msg(session)
        return response

    def _get_session(
        self,
        request: GetMediaSessionV1.Request,
        response: GetMediaSessionV1.Response,
    ) -> GetMediaSessionV1.Response:
        with self._state_lock:
            session = self._session_view_locked(request.session_id)
        response.found = session is not None
        if session is not None:
            response.session = self._session_to_msg(session)
        return response

    def _get_artifact_access(
        self,
        request: GetMediaArtifactAccessV1.Request,
        response: GetMediaArtifactAccessV1.Response,
    ) -> GetMediaArtifactAccessV1.Response:
        with self._state_lock:
            artifact = self._artifacts.get(request.artifact_id)
        response.found = artifact is not None
        if artifact is None:
            response.error_code = "INVALID_REQUEST"
            response.error_message = f"Unknown media artifact: {request.artifact_id}"
            return response
        response.upload_state = artifact.upload_state
        response.remote_object_key = artifact.remote_object_key
        if not self._uploader.config.enabled:
            response.error_code = "UPLOAD_DISABLED"
            response.error_message = "Automatic object-storage upload is disabled."
            return response
        if artifact.upload_state in {"LOCAL", "UPLOADING"}:
            response.error_code = "UPLOAD_PENDING"
            response.error_message = "Artifact upload has not completed yet."
            return response
        if artifact.upload_state == "FAILED":
            response.error_code = "UPLOAD_FAILED"
            response.error_message = artifact.upload_error
            return response
        try:
            access = self._uploader.access(artifact, int(request.expires_sec))
        except Exception as exc:  # noqa: BLE001 - translate SDK signing errors into the service API.
            response.error_code = "UPLOAD_FAILED"
            response.error_message = str(exc)
            return response
        response.access_url = access.url
        if access.expires_sec:
            response.expires_at = self._time_msg(time.time_ns() + access.expires_sec * 1_000_000_000)
        return response

    def _list_devices(
        self,
        _request: ListMediaDevicesV1.Request,
        response: ListMediaDevicesV1.Response,
    ) -> ListMediaDevicesV1.Response:
        response.devices = [self._device_to_msg(device) for device in self._devices.values()]
        response.devices.extend(
            self._audio_device_to_msg(device) for device in self._audio_devices.values()
        )
        return response

    def _on_orchestrator_event(self, event: TaskEventV1) -> None:
        if not self._auto_finalize_on_mission_terminal or event.event_type not in _MISSION_TERMINAL_EVENTS:
            return
        try:
            data = json.loads(event.data_json or "{}")
        except json.JSONDecodeError:
            return
        mission_id = str(data.get("mission_id", "")) if isinstance(data, dict) else ""
        if not mission_id:
            return
        with self._state_lock:
            session_ids = [
                worker.session_id
                for worker in self._workers.values()
                if worker.mission_id == mission_id and worker.is_alive
            ]
        for session_id in session_ids:
            self._finish_session_async(session_id, final_state="FINALIZED")

    def _maintain_sessions(self) -> None:
        now_ns = time.time_ns()
        with self._state_lock:
            workers = list(self._workers.values())
        for worker in workers:
            if worker.should_expire(now_ns):
                self._finish_session_async(worker.session_id, final_state="FINALIZED")
            elif not worker.is_alive:
                self._finish_session(worker.session_id, final_state="FINALIZED")

    def _finish_session_async(self, session_id: str, final_state: str) -> None:
        with self._state_lock:
            if session_id in self._finalizing_sessions:
                return
            self._finalizing_sessions.add(session_id)

        def finish() -> None:
            try:
                self._finish_session(session_id, final_state=final_state)
            finally:
                with self._state_lock:
                    self._finalizing_sessions.discard(session_id)

        threading.Thread(
            target=finish,
            name=f"media-finalize-{session_id}",
            daemon=True,
        ).start()

    def _finish_session(self, session_id: str, final_state: str) -> SessionRecord | None:
        with self._state_lock:
            worker = self._workers.get(session_id)
            if worker is None:
                return self._sessions.get(session_id)
        session = worker.stop(final_state=final_state, timeout_sec=self._session_stop_timeout_sec)
        with self._state_lock:
            self._sessions[session_id] = session
            if not worker.is_alive:
                self._workers.pop(session_id, None)
        if not worker.is_alive and session.artifacts:
            session.artifacts = [self._schedule_upload(artifact) for artifact in session.artifacts]
            with self._state_lock:
                self._sessions[session_id] = session
            try:
                self._spool.persist_session(session)
            except (OSError, SpoolError) as exc:
                self.get_logger().warning(f"Could not persist upload state for {session_id}: {exc}")
        return session

    def _schedule_upload(self, artifact: ArtifactRecord) -> ArtifactRecord:
        with self._state_lock:
            self._artifacts[artifact.artifact_id] = artifact
        if (
            self._uploader.config.enabled
            and artifact.mime_type == "application/vnd.task-orchestrator.frame-manifest+json"
        ):
            unsupported = replace(
                artifact,
                upload_state="FAILED",
                upload_error="frame-sequence bundles are not uploadable as one object; use ffmpeg_mp4",
            )
            self._on_upload_update(unsupported)
            return unsupported
        pending = self._uploader.enqueue(artifact, self._spool.artifact_path(artifact))
        with self._state_lock:
            return self._artifacts.get(artifact.artifact_id, pending)

    def _on_upload_update(self, artifact: ArtifactRecord) -> None:
        sessions_to_persist: list[SessionRecord] = []
        with self._state_lock:
            self._artifacts[artifact.artifact_id] = artifact
            for session in self._sessions.values():
                if any(item.artifact_id == artifact.artifact_id for item in session.artifacts):
                    session.artifacts = [
                        artifact if item.artifact_id == artifact.artifact_id else item
                        for item in session.artifacts
                    ]
                    sessions_to_persist.append(session)
            for key, cached in self._capture_idempotency.items():
                if any(item.artifact_id == artifact.artifact_id for item in cached):
                    self._capture_idempotency[key] = [
                        artifact if item.artifact_id == artifact.artifact_id else item
                        for item in cached
                    ]
        try:
            self._spool.persist_artifact_upload(artifact)
            for session in sessions_to_persist:
                self._spool.persist_session(session)
        except (OSError, SpoolError) as exc:
            self.get_logger().warning(
                f"Could not persist upload state for {artifact.artifact_id}: {exc}"
            )

    def _session_view_locked(self, session_id: str) -> SessionRecord | None:
        worker = self._workers.get(session_id)
        if worker is not None:
            return worker.snapshot()
        return self._sessions.get(session_id)

    def _active_session_ids(self) -> list[str]:
        with self._state_lock:
            return list(self._workers)

    def _request_metadata(self, metadata_json: str) -> str:
        if len((metadata_json or "{}").encode("utf-8")) > self._max_metadata_bytes:
            raise MediaConfigError(f"metadata_json exceeds {self._max_metadata_bytes} bytes")
        return normalize_json_object(metadata_json)

    def _message_timestamp_ns(self, message: CompressedImage) -> int:
        stamp = message.header.stamp
        timestamp_ns = int(stamp.sec) * 1_000_000_000 + int(stamp.nanosec)
        return timestamp_ns if timestamp_ns > 0 else time.time_ns()

    def _audio_message_timestamp_ns(self, message: Any) -> int:
        stamp = getattr(message, "stamp", None)
        if stamp is None:
            header = getattr(message, "header", None)
            stamp = getattr(header, "stamp", None)
        if stamp is None:
            return time.time_ns()
        timestamp_ns = int(stamp.sec) * 1_000_000_000 + int(stamp.nanosec)
        return timestamp_ns if timestamp_ns > 0 else time.time_ns()

    def _encoded_format(self, device: DeviceConfig, source_format: str) -> tuple[str, str]:
        normalized = source_format.lower()
        if "png" in normalized:
            return "image/png", ".png"
        if "jpeg" in normalized or "jpg" in normalized:
            return "image/jpeg", ".jpg"
        return device.mime_type, device.extension

    def _wait_interval(self, goal_handle: Any, interval_sec: float) -> bool:
        deadline = time.monotonic() + max(0.0, interval_sec)
        while time.monotonic() < deadline:
            if goal_handle.is_cancel_requested:
                return False
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        return True

    def _abort_capture(
        self,
        goal_handle: Any,
        result: CaptureMediaV1.Result,
        error_code: str,
        error_message: str,
    ) -> CaptureMediaV1.Result:
        result.success = False
        result.error_code = error_code
        result.error_message = error_message
        goal_handle.abort()
        return result

    def _recording_error(
        self,
        response: StartRecordingV1.Response,
        error_code: str,
        error_message: str,
    ) -> StartRecordingV1.Response:
        response.success = False
        response.error_code = error_code
        response.error_message = error_message
        return response

    def _artifact_to_msg(self, artifact: ArtifactRecord) -> MediaArtifactV1:
        message = MediaArtifactV1()
        message.api_version = artifact.api_version
        message.artifact_id = artifact.artifact_id
        message.artifact_kind = artifact.artifact_kind
        message.mime_type = artifact.mime_type
        message.device_id = artifact.device_id
        message.storage_key = artifact.storage_key
        message.size_bytes = artifact.size_bytes
        message.sha256 = artifact.sha256
        message.captured_at = self._time_msg(artifact.captured_ns)
        message.finished_at = self._time_msg(artifact.finished_ns)
        message.duration_sec = artifact.duration_sec
        message.frame_id = artifact.frame_id
        message.task_id = artifact.task_id
        message.mission_id = artifact.mission_id
        message.upload_state = artifact.upload_state
        message.remote_object_key = artifact.remote_object_key
        message.public_url = artifact.public_url
        message.upload_error = artifact.upload_error
        message.metadata_json = artifact.metadata_json
        return message

    def _session_to_msg(self, session: SessionRecord) -> MediaSessionV1:
        message = MediaSessionV1()
        message.api_version = session.api_version
        message.session_id = session.session_id
        message.device_id = session.device_id
        message.media_kind = session.media_kind
        message.recording_backend = session.recording_backend
        message.audio_device_id = session.audio_device_id
        message.audio_mode = session.audio_mode
        message.state = session.state
        message.task_id = session.task_id
        message.mission_id = session.mission_id
        message.idempotency_key = session.idempotency_key
        message.started_at = self._time_msg(session.started_ns)
        message.stopped_at = self._time_msg(session.stopped_ns)
        message.duration_sec = session.duration_sec
        message.frame_count = session.frame_count
        message.dropped_frames = session.dropped_frames
        message.audio_chunk_count = session.audio_chunk_count
        message.dropped_audio_chunks = session.dropped_audio_chunks
        message.size_bytes = session.size_bytes
        message.artifacts = [self._artifact_to_msg(artifact) for artifact in session.artifacts]
        message.error_code = session.error_code
        message.error_message = session.error_message
        message.metadata_json = session.metadata_json
        return message

    def _device_to_msg(self, device: DeviceConfig) -> MediaDeviceV1:
        message = MediaDeviceV1()
        received_frames, last_capture_ns, last_received_monotonic = self._buffers[device.device_id].status()
        if received_frames == 0:
            state = "WAITING_FOR_DATA"
        elif time.monotonic() - last_received_monotonic > self._device_stale_after_sec:
            state = "STALE"
        else:
            state = "ONLINE"
        message.api_version = self.api_version
        message.device_id = device.device_id
        message.display_name = device.display_name
        message.media_kind = device.media_kind
        message.source_topic = device.source_topic
        message.mime_type = device.mime_type
        message.frame_id = device.frame_id
        message.state = state
        message.capabilities = list(device.capabilities)
        message.metadata_json = device.metadata_json
        message.received_frames = received_frames
        with self._state_lock:
            message.rejected_frames = self._rejected_frames[device.device_id]
        message.last_frame_at = self._time_msg(last_capture_ns)
        message.stamp = self.get_clock().now().to_msg()
        return message

    def _audio_device_to_msg(self, device: AudioDeviceConfig) -> MediaDeviceV1:
        message = MediaDeviceV1()
        received, last_capture_ns, last_received_monotonic = self._audio_buffers[device.device_id].status()
        if received == 0:
            state = "WAITING_FOR_DATA"
        elif time.monotonic() - last_received_monotonic > self._device_stale_after_sec:
            state = "STALE"
        else:
            state = "ONLINE"
        message.api_version = self.api_version
        message.device_id = device.device_id
        message.display_name = device.display_name
        message.media_kind = "audio"
        message.source_topic = device.source_topic
        message.mime_type = f"audio/x-raw;format={device.sample_format}"
        message.frame_id = device.frame_id
        message.message_type = device.message_type
        message.sample_format = device.sample_format
        message.sample_rate_hz = device.sample_rate_hz
        message.channels = device.channels
        message.state = state
        message.capabilities = list(device.capabilities)
        message.metadata_json = device.metadata_json
        message.received_frames = received
        with self._state_lock:
            message.rejected_frames = self._rejected_audio_chunks[device.device_id]
        message.last_frame_at = self._time_msg(last_capture_ns)
        message.stamp = self.get_clock().now().to_msg()
        return message

    def _time_msg(self, timestamp_ns: int) -> Time:
        message = Time()
        if timestamp_ns > 0:
            message.sec = timestamp_ns // 1_000_000_000
            message.nanosec = timestamp_ns % 1_000_000_000
        return message


def main(args: list[str] | None = None) -> None:
    """Run the media manager with enough executor threads for capture waiters."""
    rclpy.init(args=args)
    node: MediaManagerNode | None = None
    executor = MultiThreadedExecutor(num_threads=4)
    try:
        node = MediaManagerNode()
        executor.add_node(node)
        executor.spin()
    except (MediaConfigError, SpoolError) as exc:
        if node is not None:
            node.get_logger().fatal(str(exc))
        else:
            print(f"Media manager startup failed: {exc}")
    except KeyboardInterrupt:
        pass
    finally:
        executor.shutdown()
        if node is not None:
            node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
