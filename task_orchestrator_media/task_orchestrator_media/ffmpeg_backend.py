"""FFmpeg-backed H.264/MP4 recording sink."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Full, Queue
from typing import Any, BinaryIO, Callable

from task_orchestrator_media.models import (
    ArtifactRecord,
    AudioDeviceConfig,
    AudioPacket,
    DeviceConfig,
    FramePacket,
    SessionRecord,
)
from task_orchestrator_media.spool import SpoolError, SpoolQuotaExceeded, SpoolStore

_ENCODER_NAME = re.compile(r"^[A-Za-z0-9_]+$")
_PRESET_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
_MIN_ALLOCATION_BYTES = 64 * 1024 * 1024
_MIN_NON_VIDEO_HEADROOM_BYTES = 16 * 1024 * 1024
_AUDIO_SAMPLE_FORMATS = {"u8", "s16le", "s24le", "s32le", "f32le", "f64le"}


@dataclass(frozen=True)
class FFmpegConfig:
    """Validated FFmpeg process settings for one manager instance."""

    binary: str = "ffmpeg"
    codec: str = "libx264"
    preset: str = "veryfast"
    crf: int = 23
    recording_fps: float = 30.0
    max_recording_bytes: int = 2 * 1024 * 1024 * 1024
    finalize_timeout_sec: float = 20.0
    threads: int = 0
    fragmented_mp4: bool = True
    audio_codec: str = "aac"
    audio_bitrate_kbps: int = 128

    def validate(self) -> "FFmpegConfig":
        """Reject unsafe or unsupported command values before a process is started."""
        if not self.binary:
            raise SpoolError("ffmpeg_binary must not be empty")
        if not _ENCODER_NAME.fullmatch(self.codec):
            raise SpoolError("ffmpeg_video_codec contains unsupported characters")
        if not _PRESET_NAME.fullmatch(self.preset):
            raise SpoolError("ffmpeg_preset contains unsupported characters")
        if not 0 <= self.crf <= 51:
            raise SpoolError("ffmpeg_crf must be between 0 and 51")
        if not 0.1 <= self.recording_fps <= 240.0:
            raise SpoolError("recording_fps must be between 0.1 and 240")
        if self.max_recording_bytes < _MIN_ALLOCATION_BYTES:
            raise SpoolError(f"max_recording_bytes must be at least {_MIN_ALLOCATION_BYTES}")
        if self.finalize_timeout_sec <= 0:
            raise SpoolError("ffmpeg_finalize_timeout_sec must be positive")
        if self.threads < 0:
            raise SpoolError("ffmpeg_threads must be non-negative")
        if not _ENCODER_NAME.fullmatch(self.audio_codec):
            raise SpoolError("ffmpeg_audio_codec contains unsupported characters")
        if not 16 <= self.audio_bitrate_kbps <= 1024:
            raise SpoolError("ffmpeg_audio_bitrate_kbps must be between 16 and 1024")
        return self

    def resolved_binary(self) -> str:
        """Resolve the configured executable without invoking a shell."""
        candidate = shutil.which(self.binary)
        if candidate is None:
            raise SpoolError(f"FFmpeg executable was not found: {self.binary}")
        return candidate


ProcessFactory = Callable[..., Any]


@dataclass(frozen=True)
class AudioInputConfig:
    """Validated PCM input attached to a video recording."""

    device: AudioDeviceConfig
    mode: str

    def validate(self) -> "AudioInputConfig":
        if self.mode not in {"mux", "separate"}:
            raise SpoolError("audio_mode must be 'none', 'mux' or 'separate'")
        if self.device.sample_format not in _AUDIO_SAMPLE_FORMATS:
            raise SpoolError(f"unsupported audio sample format: {self.device.sample_format}")
        if not 8_000 <= self.device.sample_rate_hz <= 384_000:
            raise SpoolError("audio sample rate must be between 8000 and 384000")
        if not 1 <= self.device.channels <= 8:
            raise SpoolError("audio channels must be between 1 and 8")
        return self


class FFmpegMp4Bundle:
    """Feed encoded image frames to FFmpeg and finalize a bounded MP4 artifact."""

    def __init__(
        self,
        store: SpoolStore,
        session_dir: Path,
        session: SessionRecord,
        config: FFmpegConfig,
        allocation_bytes: int,
        *,
        binary: str,
        audio_input: AudioInputConfig | None = None,
        process_factory: ProcessFactory = subprocess.Popen,
    ) -> None:
        self._store = store
        self._session_dir = session_dir
        self._session = session
        self._config = config
        self._allocation_bytes = allocation_bytes
        self._binary = binary
        self._audio_input = audio_input.validate() if audio_input is not None else None
        self._process_factory = process_factory
        self._process: Any | None = None
        self._stderr_thread: threading.Thread | None = None
        self._stderr_tail = bytearray()
        self._stderr_lock = threading.Lock()
        self._entries: list[dict[str, Any]] = []
        self._audio_entries: list[dict[str, Any]] = []
        self._audio_queue: Queue[AudioPacket | None] | None = None
        self._audio_writer: threading.Thread | None = None
        self._audio_stream: BinaryIO | None = None
        self._audio_writer_error: Exception | None = None
        self._input_mime_type = ""
        self._lock = threading.Lock()
        self._closed = False
        self._journal_path = session_dir / "video_frames.jsonl"
        self._partial_path = session_dir / "recording.mp4.part"
        self._audio_partial_path = session_dir / "recording-audio.m4a.part"
        self._audio_journal_path = session_dir / "audio_chunks.jsonl"
        headroom_ratio = 5 if self._audio_input is not None and self._audio_input.mode == "separate" else 20
        headroom_bytes = max(_MIN_NON_VIDEO_HEADROOM_BYTES, allocation_bytes // headroom_ratio)
        self._video_limit_bytes = max(1, allocation_bytes - headroom_bytes)
        self._audio_limit_bytes = max(1, headroom_bytes - 4 * 1024 * 1024)

    @property
    def command(self) -> list[str]:
        """Return the command that will be used once the first frame arrives."""
        return self._command(audio_fd=3 if self._audio_input is not None else None)

    def append(self, frame: FramePacket) -> None:
        """Send one JPEG/PNG frame to FFmpeg and journal its capture metadata."""
        with self._lock:
            if self._closed:
                raise SpoolError(f"recording session is already closed: {self._session.session_id}")
            if frame.mime_type not in {"image/jpeg", "image/png"}:
                raise SpoolError(f"ffmpeg_mp4 does not support input MIME type {frame.mime_type}")
            if self._input_mime_type and frame.mime_type != self._input_mime_type:
                raise SpoolError("input image format changed during an MP4 recording")
            if not self._input_mime_type:
                self._input_mime_type = frame.mime_type
            if self._process is None:
                self._start_process()
            self._write_frame(frame)
            entry = {
                "index": len(self._entries) + 1,
                "captured_ns": frame.captured_ns,
                "frame_id": frame.frame_id,
                "fragmented": self._config.fragmented_mp4,
                "mime_type": frame.mime_type,
                "recording_fps": self._config.recording_fps,
                "source_format": frame.source_format,
                "video_codec": self._config.codec,
            }
            journal_line = (json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
            with self._journal_path.open("ab") as stream:
                stream.write(journal_line)
                stream.flush()
                os.fsync(stream.fileno())
            self._entries.append(entry)

    def append_audio(self, chunk: AudioPacket) -> None:
        """Queue one PCM chunk for FFmpeg without blocking the recording worker."""
        with self._lock:
            if self._closed:
                raise SpoolError(f"recording session is already closed: {self._session.session_id}")
            if self._audio_input is None:
                raise SpoolError("this recording session has no audio input")
            expected = self._audio_input.device
            if (
                chunk.sample_format != expected.sample_format
                or chunk.sample_rate_hz != expected.sample_rate_hz
                or chunk.channels != expected.channels
            ):
                raise SpoolError("audio format changed during recording")
            if self._audio_writer_error is not None:
                raise SpoolError(f"FFmpeg audio input failed: {self._audio_writer_error}")
            if self._process is None:
                self._start_process()
            assert self._audio_queue is not None
            try:
                self._audio_queue.put_nowait(chunk)
            except Full as exc:
                raise SpoolQuotaExceeded("FFmpeg audio input queue is full") from exc

    def finalize(self, session: SessionRecord) -> ArtifactRecord | None:
        """Close FFmpeg, commit disk accounting and create the MP4 sidecar."""
        with self._lock:
            if self._closed:
                return session.artifacts[0] if session.artifacts else None
            self._finish_process(session)
            actual_bytes = self._external_file_bytes()
            if actual_bytes > 0:
                reserved_bytes = self._allocation_bytes
                self._allocation_bytes = 0
                self._store.commit_external_recording(reserved_bytes, actual_bytes)
            else:
                self._release_allocation()
            try:
                artifact = self._store.finalize_video_recording(
                    self._session_dir,
                    session,
                    self._entries,
                    codec=self._config.codec,
                    recording_fps=self._config.recording_fps,
                    fragmented=self._config.fragmented_mp4,
                    audio_entries=self._audio_entries,
                    audio_mode=self._audio_input.mode if self._audio_input is not None else "none",
                    audio_codec=self._config.audio_codec,
                )
            except Exception:
                # The valid MP4 remains in place for startup recovery if its
                # terminal metadata cannot be committed right now.
                raise
            self._closed = True
            return artifact

    def _command(self, audio_fd: int | None = None) -> list[str]:
        command = [
            self._binary,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-f",
            "image2pipe",
            "-framerate",
            self._format_number(self._config.recording_fps),
            "-i",
            "pipe:0",
        ]
        if self._audio_input is not None:
            audio = self._audio_input.device
            assert audio_fd is not None
            command.extend(
                [
                    "-thread_queue_size",
                    "512",
                    "-f",
                    audio.sample_format,
                    "-ar",
                    str(audio.sample_rate_hz),
                    "-ac",
                    str(audio.channels),
                    "-i",
                    f"pipe:{audio_fd}",
                ]
            )
        command.extend(
            [
                "-map_metadata",
                "-1",
                "-map",
                "0:v:0",
                "-c:v",
                self._config.codec,
                "-preset",
                self._config.preset,
                "-crf",
                str(self._config.crf),
                "-vf",
                "pad=ceil(iw/2)*2:ceil(ih/2)*2",
                "-pix_fmt",
                "yuv420p",
                "-g",
                str(max(1, round(self._config.recording_fps * 2))),
            ]
        )
        if self._config.threads > 0:
            command.extend(["-threads", str(self._config.threads)])
        if self._audio_input is None:
            command.append("-an")
        elif self._audio_input.mode == "mux":
            command.extend(
                [
                    "-map",
                    "1:a:0",
                    "-c:a",
                    self._config.audio_codec,
                    "-b:a",
                    f"{self._config.audio_bitrate_kbps}k",
                    "-shortest",
                ]
            )
        else:
            command.append("-an")
        command.extend(["-movflags", self._movflags()])
        command.extend(
            [
                "-fs",
                str(self._video_limit_bytes),
                "-f",
                "mp4",
                str(self._partial_path),
            ]
        )
        if self._audio_input is not None and self._audio_input.mode == "separate":
            command.extend(
                [
                    "-map",
                    "1:a:0",
                    "-vn",
                    "-c:a",
                    self._config.audio_codec,
                    "-b:a",
                    f"{self._config.audio_bitrate_kbps}k",
                    "-movflags",
                    self._movflags(),
                    "-fs",
                    str(self._audio_limit_bytes),
                    "-f",
                    "mp4",
                    str(self._audio_partial_path),
                ]
            )
        return command

    def _start_process(self) -> None:
        audio_read_fd: int | None = None
        audio_write_fd: int | None = None
        if self._audio_input is not None:
            audio_read_fd, audio_write_fd = os.pipe()
        try:
            kwargs: dict[str, Any] = {
                "stdin": subprocess.PIPE,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.PIPE,
            }
            if audio_read_fd is not None:
                kwargs["pass_fds"] = (audio_read_fd,)
            self._process = self._process_factory(self._command(audio_fd=audio_read_fd), **kwargs)
        except OSError as exc:
            if audio_read_fd is not None:
                os.close(audio_read_fd)
            if audio_write_fd is not None:
                os.close(audio_write_fd)
            raise SpoolError(f"failed to start FFmpeg: {exc}") from exc
        if audio_read_fd is not None:
            os.close(audio_read_fd)
        if self._process.stdin is None or self._process.stderr is None:
            if audio_write_fd is not None:
                os.close(audio_write_fd)
            self._process.terminate()
            self._process.wait(timeout=2.0)
            self._process = None
            raise SpoolError("FFmpeg process did not expose stdin/stderr pipes")
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr,
            args=(self._process.stderr,),
            name=f"media-ffmpeg-stderr-{self._session.session_id}",
            daemon=True,
        )
        self._stderr_thread.start()
        if audio_write_fd is not None:
            self._audio_stream = os.fdopen(audio_write_fd, "wb", buffering=0)
            self._audio_queue = Queue(maxsize=1024)
            self._audio_writer = threading.Thread(
                target=self._write_audio,
                name=f"media-ffmpeg-audio-{self._session.session_id}",
                daemon=True,
            )
            self._audio_writer.start()

    def _write_frame(self, frame: FramePacket) -> None:
        assert self._process is not None
        if self._process.poll() is not None:
            self._raise_process_failure()
        try:
            self._process.stdin.write(frame.data)
            self._process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            if self._partial_path.exists() and self._partial_path.stat().st_size >= self._video_limit_bytes:
                raise SpoolQuotaExceeded("encoded recording reached max_recording_bytes") from exc
            raise SpoolError(f"FFmpeg stopped accepting frames: {self._stderr_message()}") from exc

    def _finish_process(self, session: SessionRecord) -> None:
        if self._process is None:
            return
        try:
            self._process.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        if self._audio_queue is not None and self._audio_writer is not None and self._audio_writer.is_alive():
            try:
                self._audio_queue.put(None, timeout=self._config.finalize_timeout_sec)
            except Full:
                self._set_session_error(
                    session,
                    "ENCODER_TIMEOUT",
                    "FFmpeg audio queue did not drain before the deadline.",
                )
        if self._audio_writer is not None:
            self._audio_writer.join(timeout=self._config.finalize_timeout_sec)
            if self._audio_writer.is_alive():
                self._set_session_error(
                    session,
                    "ENCODER_TIMEOUT",
                    "FFmpeg audio input did not finalize before the deadline.",
                )
        timed_out = False
        try:
            return_code = self._process.wait(timeout=self._config.finalize_timeout_sec)
        except subprocess.TimeoutExpired:
            timed_out = True
            self._process.terminate()
            try:
                return_code = self._process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                self._process.kill()
                return_code = self._process.wait(timeout=2.0)
        if self._stderr_thread is not None:
            self._stderr_thread.join(timeout=1.0)
        if timed_out:
            self._set_session_error(session, "ENCODER_TIMEOUT", "FFmpeg did not finalize before the deadline.")
        elif return_code != 0:
            self._set_session_error(
                session,
                "ENCODER_FAILED",
                f"FFmpeg exited with code {return_code}: {self._stderr_message()}",
            )
        elif self._audio_writer_error is not None:
            self._set_session_error(session, "ENCODER_FAILED", str(self._audio_writer_error))
        if self._audio_input is not None and not self._audio_entries:
            self._set_session_error(session, "NO_AUDIO", "Recording finished before any audio was received.")

    def _raise_process_failure(self) -> None:
        if self._partial_path.exists() and self._partial_path.stat().st_size >= self._video_limit_bytes:
            raise SpoolQuotaExceeded("encoded recording reached max_recording_bytes")
        raise SpoolError(f"FFmpeg exited before the recording stopped: {self._stderr_message()}")

    def _drain_stderr(self, stream: BinaryIO) -> None:
        while True:
            chunk = stream.read(4096)
            if not chunk:
                return
            with self._stderr_lock:
                self._stderr_tail.extend(chunk)
                if len(self._stderr_tail) > 8192:
                    del self._stderr_tail[:-8192]

    def _stderr_message(self) -> str:
        with self._stderr_lock:
            message = bytes(self._stderr_tail).decode("utf-8", errors="replace").strip()
        return message or "no FFmpeg diagnostics"

    def _external_file_bytes(self) -> int:
        return sum(
            path.stat().st_size
            for path in (
                self._partial_path,
                self._journal_path,
                self._audio_partial_path,
                self._audio_journal_path,
            )
            if path.is_file()
        )

    def _write_audio(self) -> None:
        assert self._audio_queue is not None
        assert self._audio_stream is not None
        try:
            while True:
                try:
                    chunk = self._audio_queue.get(timeout=0.1)
                except Empty:
                    continue
                try:
                    if chunk is None:
                        return
                    self._audio_stream.write(chunk.data)
                    entry = {
                        "index": len(self._audio_entries) + 1,
                        "captured_ns": chunk.captured_ns,
                        "frame_id": chunk.frame_id,
                        "sample_format": chunk.sample_format,
                        "sample_rate_hz": chunk.sample_rate_hz,
                        "channels": chunk.channels,
                        "size_bytes": len(chunk.data),
                        "audio_codec": self._config.audio_codec,
                        "audio_mode": self._audio_input.mode if self._audio_input is not None else "none",
                    }
                    line = (json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n").encode()
                    with self._audio_journal_path.open("ab") as journal:
                        journal.write(line)
                        journal.flush()
                        os.fsync(journal.fileno())
                    self._audio_entries.append(entry)
                finally:
                    self._audio_queue.task_done()
        except (BrokenPipeError, OSError) as exc:
            self._audio_writer_error = exc
        finally:
            try:
                self._audio_stream.close()
            except OSError:
                pass

    def _movflags(self) -> str:
        if self._config.fragmented_mp4:
            return "+frag_keyframe+empty_moov+default_base_moof"
        return "+faststart"

    def _release_allocation(self) -> None:
        if self._allocation_bytes:
            self._store.release_external_recording(self._allocation_bytes)
            self._allocation_bytes = 0

    def _set_session_error(self, session: SessionRecord, code: str, message: str) -> None:
        session.state = "ERROR"
        if not session.error_code:
            session.error_code = code
            session.error_message = message

    def _format_number(self, value: float) -> str:
        return format(value, ".6g")


def create_ffmpeg_mp4_bundle(
    store: SpoolStore,
    device: DeviceConfig,
    session: SessionRecord,
    config: FFmpegConfig,
    *,
    audio_input: AudioInputConfig | None = None,
    process_factory: ProcessFactory = subprocess.Popen,
    validate_binary: bool = True,
) -> FFmpegMp4Bundle:
    """Validate FFmpeg and allocate one bounded recording sink."""
    config.validate()
    binary = config.resolved_binary() if validate_binary else config.binary
    session_dir, allocation_bytes = store.prepare_external_recording(
        device,
        session,
        config.max_recording_bytes,
    )
    try:
        return FFmpegMp4Bundle(
            store,
            session_dir,
            session,
            config,
            allocation_bytes,
            binary=binary,
            audio_input=audio_input,
            process_factory=process_factory,
        )
    except Exception:
        store.release_external_recording(allocation_bytes)
        raise
