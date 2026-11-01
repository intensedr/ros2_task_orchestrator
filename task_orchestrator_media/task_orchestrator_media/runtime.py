"""Thread-safe frame buffers and background recording workers."""

from __future__ import annotations

import copy
import threading
import time
from queue import Empty, Full, Queue
from typing import Callable, Protocol

from task_orchestrator_media.models import ArtifactRecord, AudioPacket, FramePacket, SessionRecord
from task_orchestrator_media.spool import SpoolQuotaExceeded


class RecordingSink(Protocol):
    """Minimal sink implemented by frame-sequence and encoded-video backends."""

    def append(self, frame: FramePacket) -> None:
        """Persist or encode one input frame."""

    def append_audio(self, chunk: AudioPacket) -> None:
        """Persist or encode one raw PCM chunk."""

    def finalize(self, session: SessionRecord) -> ArtifactRecord | None:
        """Commit terminal metadata and return the resulting artifact."""


class FrameBuffer:
    """Keep the latest encoded frame and allow callers to wait for a newer one."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._latest: FramePacket | None = None
        self._sequence = 0
        self._last_received_monotonic = 0.0

    @property
    def sequence(self) -> int:
        with self._condition:
            return self._sequence

    def status(self) -> tuple[int, int, float]:
        """Return received count, latest capture timestamp and local receipt time."""
        with self._condition:
            captured_ns = self._latest.captured_ns if self._latest is not None else 0
            return self._sequence, captured_ns, self._last_received_monotonic

    def push(
        self,
        data: bytes,
        captured_ns: int,
        frame_id: str,
        mime_type: str,
        extension: str,
        source_format: str = "",
    ) -> FramePacket:
        """Publish a new immutable packet and wake snapshot waiters."""
        with self._condition:
            self._sequence += 1
            packet = FramePacket(
                sequence=self._sequence,
                data=data,
                captured_ns=captured_ns,
                frame_id=frame_id,
                mime_type=mime_type,
                extension=extension,
                source_format=source_format,
            )
            self._latest = packet
            self._last_received_monotonic = time.monotonic()
            self._condition.notify_all()
            return packet

    def wait_for_next(
        self,
        after_sequence: int,
        timeout_sec: float,
        canceled: Callable[[], bool] | None = None,
    ) -> FramePacket | None:
        """Wait for a frame newer than the supplied sequence number."""
        deadline = time.monotonic() + max(0.0, timeout_sec)
        with self._condition:
            while self._latest is None or self._latest.sequence <= after_sequence:
                if canceled is not None and canceled():
                    return None
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(timeout=min(0.1, remaining))
            return self._latest


class AudioBuffer:
    """Track microphone health and create immutable PCM packets."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sequence = 0
        self._last_capture_ns = 0
        self._last_received_monotonic = 0.0

    def push(
        self,
        data: bytes,
        captured_ns: int,
        sample_format: str,
        sample_rate_hz: int,
        channels: int,
        frame_id: str = "",
    ) -> AudioPacket:
        """Record source health and return one immutable audio packet."""
        with self._lock:
            self._sequence += 1
            self._last_capture_ns = captured_ns
            self._last_received_monotonic = time.monotonic()
            return AudioPacket(
                sequence=self._sequence,
                data=data,
                captured_ns=captured_ns,
                sample_format=sample_format,
                sample_rate_hz=sample_rate_hz,
                channels=channels,
                frame_id=frame_id,
            )

    def status(self) -> tuple[int, int, float]:
        """Return received count, latest capture timestamp and local receipt time."""
        with self._lock:
            return self._sequence, self._last_capture_ns, self._last_received_monotonic


class RecordingWorker:
    """Persist frames away from ROS subscription callbacks using a bounded queue."""

    def __init__(
        self,
        bundle: RecordingSink,
        session: SessionRecord,
        queue_size: int,
        queue_max_bytes: int = 0,
    ) -> None:
        self._bundle = bundle
        self._session = session
        self._queue: Queue[FramePacket | AudioPacket] = Queue(maxsize=max(1, queue_size))
        self._queue_max_bytes = max(0, queue_max_bytes)
        self._queued_bytes = 0
        self._state_lock = threading.Lock()
        self._stop_lock = threading.Lock()
        self._stopping = threading.Event()
        self._requested_final_state = "FINALIZED"
        self._thread = threading.Thread(
            target=self._run,
            name=f"media-recording-{session.session_id}",
            daemon=True,
        )
        self._thread.start()

    @property
    def device_id(self) -> str:
        return self._session.device_id

    @property
    def session_id(self) -> str:
        return self._session.session_id

    @property
    def mission_id(self) -> str:
        return self._session.mission_id

    @property
    def audio_device_id(self) -> str:
        return self._session.audio_device_id

    @property
    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def enqueue(self, frame: FramePacket) -> None:
        """Queue a frame without blocking the subscriber callback."""
        if self._stopping.is_set():
            return
        frame_bytes = len(frame.data)
        with self._state_lock:
            if self._queue_max_bytes and self._queued_bytes + frame_bytes > self._queue_max_bytes:
                self._session.dropped_frames += 1
                return
            self._queued_bytes += frame_bytes
        try:
            self._queue.put_nowait(frame)
        except Full:
            with self._state_lock:
                self._queued_bytes = max(0, self._queued_bytes - frame_bytes)
                self._session.dropped_frames += 1

    def enqueue_audio(self, chunk: AudioPacket) -> None:
        """Queue an audio chunk without blocking the ROS subscription callback."""
        if self._stopping.is_set():
            return
        chunk_bytes = len(chunk.data)
        with self._state_lock:
            if self._queue_max_bytes and self._queued_bytes + chunk_bytes > self._queue_max_bytes:
                self._session.dropped_audio_chunks += 1
                return
            self._queued_bytes += chunk_bytes
        try:
            self._queue.put_nowait(chunk)
        except Full:
            with self._state_lock:
                self._queued_bytes = max(0, self._queued_bytes - chunk_bytes)
                self._session.dropped_audio_chunks += 1

    def should_expire(self, now_ns: int) -> bool:
        max_duration_sec = self._session.max_duration_sec
        return bool(
            max_duration_sec > 0
            and now_ns - self._session.started_ns >= int(max_duration_sec * 1_000_000_000)
        )

    def stop(self, final_state: str = "FINALIZED", timeout_sec: float = 30.0) -> SessionRecord:
        """Drain queued frames, finalize the manifest and return session state."""
        with self._stop_lock:
            if self._thread.is_alive():
                self._requested_final_state = final_state
                self._stopping.set()
                self._thread.join(timeout=max(0.0, timeout_sec))
                if self._thread.is_alive():
                    with self._state_lock:
                        self._session.state = "ERROR"
                        self._session.error_code = "FINALIZE_TIMEOUT"
                        self._session.error_message = "Recording worker did not stop before the deadline."
            return self.snapshot()

    def snapshot(self) -> SessionRecord:
        """Return a consistent copy for ROS responses."""
        with self._state_lock:
            return copy.deepcopy(self._session)

    def _run(self) -> None:
        try:
            while not self._stopping.is_set() or not self._queue.empty():
                try:
                    frame = self._queue.get(timeout=0.1)
                except Empty:
                    continue
                try:
                    if isinstance(frame, AudioPacket):
                        self._bundle.append_audio(frame)
                    else:
                        self._bundle.append(frame)
                    with self._state_lock:
                        if isinstance(frame, AudioPacket):
                            self._session.audio_chunk_count += 1
                        else:
                            self._session.frame_count += 1
                        self._session.size_bytes += len(frame.data)
                finally:
                    with self._state_lock:
                        self._queued_bytes = max(0, self._queued_bytes - len(frame.data))
                    self._queue.task_done()
        except SpoolQuotaExceeded as exc:
            self._set_error("STORAGE_FULL", str(exc))
        except Exception as exc:  # noqa: BLE001 - worker failures become public session state.
            self._set_error("WRITE_FAILED", str(exc))
        finally:
            with self._state_lock:
                if self._session.state == "RECORDING":
                    self._session.state = self._requested_final_state
                self._session.stopped_ns = time.time_ns()
                try:
                    self._bundle.finalize(self._session)
                except SpoolQuotaExceeded as exc:
                    self._session.state = "ERROR"
                    self._session.error_code = "STORAGE_FULL"
                    self._session.error_message = str(exc)
                except Exception as exc:  # noqa: BLE001 - expose finalization failures to callers.
                    self._session.state = "ERROR"
                    self._session.error_code = "FINALIZE_FAILED"
                    self._session.error_message = str(exc)

    def _set_error(self, error_code: str, error_message: str) -> None:
        with self._state_lock:
            self._session.state = "ERROR"
            self._session.error_code = error_code
            self._session.error_message = error_message
            self._stopping.set()
