import hashlib
import io
import json
import math
import os
import shutil
import struct
import subprocess
import time
from pathlib import Path

import pytest
from task_orchestrator_media.ffmpeg_backend import AudioInputConfig, FFmpegConfig, create_ffmpeg_mp4_bundle
from task_orchestrator_media.models import AudioDeviceConfig, AudioPacket, DeviceConfig, FramePacket, SessionRecord
from task_orchestrator_media.spool import SpoolError, SpoolStore


class _FakeStdin:
    def __init__(self, output_path):
        self._output_path = output_path
        self._payload = bytearray()
        self.closed = False

    def write(self, data):
        self._payload.extend(data)
        return len(data)

    def flush(self):
        return None

    def close(self):
        if self.closed:
            return
        self.closed = True
        self._output_path.write_bytes(b"\x00\x00\x00\x18ftypisom" + bytes(self._payload))


class _FakeProcess:
    def __init__(self, command, **_kwargs):
        self.stdin = _FakeStdin(output_path=Path(command[-1]))
        self.stderr = io.BytesIO()
        self.returncode = None

    def poll(self):
        return 0 if self.stdin.closed else None

    def wait(self, timeout=None):
        del timeout
        self.returncode = 0
        return 0

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9


def _device():
    return DeviceConfig(device_id="front", source_topic="/camera/compressed")


def _session(session_id="mp4-session"):
    return SessionRecord(
        session_id=session_id,
        device_id="front",
        media_kind="image",
        state="RECORDING",
        task_id="task",
        mission_id="mission",
        idempotency_key="",
        started_ns=time.time_ns(),
        max_duration_sec=0.0,
        metadata_json="{}",
        recording_backend="ffmpeg_mp4",
    )


def _frame(index, captured_ns):
    return FramePacket(
        sequence=index,
        data=b"jpeg-frame-" + str(index).encode(),
        captured_ns=captured_ns,
        frame_id="front_optical",
        mime_type="image/jpeg",
        extension=".jpg",
        source_format="jpeg",
    )


def test_ffmpeg_bundle_builds_bounded_mp4_artifact(tmp_path):
    store = SpoolStore(tmp_path, max_bytes=80 * 1024 * 1024)
    session = _session()
    config = FFmpegConfig(binary="fake-ffmpeg", max_recording_bytes=64 * 1024 * 1024)
    bundle = create_ffmpeg_mp4_bundle(
        store,
        _device(),
        session,
        config,
        process_factory=_FakeProcess,
        validate_binary=False,
    )
    assert bundle.command[0] == "fake-ffmpeg"
    assert "+frag_keyframe+empty_moov+default_base_moof" in bundle.command
    assert "pipe:0" in bundle.command

    bundle.append(_frame(1, session.started_ns + 100_000_000))
    bundle.append(_frame(2, session.started_ns + 200_000_000))
    session.state = "FINALIZED"
    session.stopped_ns = session.started_ns + 300_000_000

    artifact = bundle.finalize(session)

    assert artifact is not None
    assert artifact.artifact_kind == "video"
    assert artifact.mime_type == "video/mp4"
    video_path = tmp_path / artifact.storage_key
    assert video_path.read_bytes()[4:12] == b"ftypisom"
    assert artifact.sha256 == hashlib.sha256(video_path.read_bytes()).hexdigest()
    assert artifact.duration_sec == pytest.approx(2 / 30.0)
    assert json.loads(video_path.with_name("recording.mp4.json").read_text())["frame_count"] == 2
    assert session.recording_backend == "ffmpeg_mp4"
    assert store.used_bytes < 1024 * 1024


def test_ffmpeg_partial_recording_is_recovered_after_restart(tmp_path):
    store = SpoolStore(tmp_path)
    session = _session("crashed-mp4")
    session_dir, _allocation = store.prepare_external_recording(
        _device(),
        session,
        64 * 1024 * 1024,
    )
    (session_dir / "recording.mp4.part").write_bytes(b"\x00\x00\x00\x18ftypisom-recovered")
    entry = {
        "index": 1,
        "captured_ns": session.started_ns,
        "frame_id": "front_optical",
        "fragmented": True,
        "mime_type": "image/jpeg",
        "recording_fps": 30.0,
        "video_codec": "libx264",
    }
    (session_dir / "video_frames.jsonl").write_text(json.dumps(entry) + "\n", encoding="utf-8")

    recovered = SpoolStore(tmp_path).recover_sessions()

    assert len(recovered) == 1
    assert recovered[0].state == "RECOVERED"
    assert recovered[0].recording_backend == "ffmpeg_mp4"
    assert recovered[0].artifacts[0].mime_type == "video/mp4"
    assert (tmp_path / recovered[0].artifacts[0].storage_key).is_file()
    assert not (session_dir / "recording.mp4.part").exists()


def test_ffmpeg_config_rejects_unsafe_codec():
    with pytest.raises(SpoolError, match="codec"):
        FFmpegConfig(codec="libx264;touch/tmp/file").validate()


def test_missing_ffmpeg_is_rejected_before_session_is_created(tmp_path):
    store = SpoolStore(tmp_path / "spool")

    with pytest.raises(SpoolError, match="was not found"):
        create_ffmpeg_mp4_bundle(
            store,
            _device(),
            _session("missing-encoder"),
            FFmpegConfig(
                binary="/definitely/not/a/real/ffmpeg",
                max_recording_bytes=64 * 1024 * 1024,
            ),
        )

    assert not (store.root / "recordings").exists()
    assert store.used_bytes == 0


def test_real_ffmpeg_produces_decodable_mp4(tmp_path):
    binary = os.environ.get("TASK_ORCHESTRATOR_TEST_FFMPEG") or shutil.which("ffmpeg")
    if binary is None:
        pytest.skip("FFmpeg is not installed")

    frame_path = tmp_path / "source.jpg"
    subprocess.run(
        [
            binary,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=160x120:rate=1",
            "-frames:v",
            "1",
            "-f",
            "image2",
            str(frame_path),
        ],
        check=True,
    )

    store = SpoolStore(tmp_path / "spool", max_bytes=80 * 1024 * 1024)
    session = _session("real-mp4")
    config = FFmpegConfig(
        binary=binary,
        recording_fps=10.0,
        max_recording_bytes=64 * 1024 * 1024,
    )
    bundle = create_ffmpeg_mp4_bundle(store, _device(), session, config)
    jpeg = frame_path.read_bytes()
    for index in range(12):
        frame = _frame(index + 1, session.started_ns + index * 100_000_000)
        bundle.append(
            FramePacket(
                sequence=frame.sequence,
                data=jpeg,
                captured_ns=frame.captured_ns,
                frame_id=frame.frame_id,
                mime_type=frame.mime_type,
                extension=frame.extension,
                source_format=frame.source_format,
            )
        )
    session.state = "FINALIZED"
    session.stopped_ns = session.started_ns + 1_200_000_000

    artifact = bundle.finalize(session)

    assert artifact is not None
    video_path = store.root / artifact.storage_key
    assert video_path.is_file()
    subprocess.run(
        [binary, "-hide_banner", "-loglevel", "error", "-i", str(video_path), "-f", "null", "-"],
        check=True,
    )


@pytest.mark.parametrize("audio_mode", ["mux", "separate"])
def test_real_ffmpeg_records_pcm_audio(tmp_path, audio_mode):
    binary = os.environ.get("TASK_ORCHESTRATOR_TEST_FFMPEG") or shutil.which("ffmpeg")
    if binary is None:
        pytest.skip("FFmpeg is not installed")
    frame_path = tmp_path / "audio-source.jpg"
    subprocess.run(
        [
            binary,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=160x120:rate=1",
            "-frames:v",
            "1",
            "-f",
            "image2",
            str(frame_path),
        ],
        check=True,
    )
    audio_device = AudioDeviceConfig(
        device_id="microphone",
        source_topic="/audio/pcm",
        sample_format="s16le",
        sample_rate_hz=48_000,
        channels=1,
    )
    session = _session(f"audio-{audio_mode}")
    session.audio_device_id = audio_device.device_id
    session.audio_mode = audio_mode
    session.audio_sample_format = audio_device.sample_format
    session.audio_sample_rate_hz = audio_device.sample_rate_hz
    session.audio_channels = audio_device.channels
    store = SpoolStore(tmp_path / "audio-spool", max_bytes=80 * 1024 * 1024)
    bundle = create_ffmpeg_mp4_bundle(
        store,
        _device(),
        session,
        FFmpegConfig(binary=binary, recording_fps=10.0, max_recording_bytes=64 * 1024 * 1024),
        audio_input=AudioInputConfig(device=audio_device, mode=audio_mode),
    )
    jpeg = frame_path.read_bytes()
    samples_per_chunk = 4_800
    for index in range(12):
        samples = b"".join(
            struct.pack("<h", int(12_000 * math.sin(2 * math.pi * 440 * sample / 48_000)))
            for sample in range(index * samples_per_chunk, (index + 1) * samples_per_chunk)
        )
        bundle.append_audio(
            AudioPacket(
                sequence=index + 1,
                data=samples,
                captured_ns=session.started_ns + index * 100_000_000,
                sample_format="s16le",
                sample_rate_hz=48_000,
                channels=1,
                frame_id="microphone",
            )
        )
        bundle.append(
            FramePacket(
                sequence=index + 1,
                data=jpeg,
                captured_ns=session.started_ns + index * 100_000_000,
                frame_id="front_optical",
                mime_type="image/jpeg",
                extension=".jpg",
                source_format="jpeg",
            )
        )
    session.state = "FINALIZED"
    session.stopped_ns = session.started_ns + 1_200_000_000

    bundle.finalize(session)

    assert session.audio_chunk_count == 12
    assert len(session.artifacts) == (1 if audio_mode == "mux" else 2)
    target = next(
        artifact
        for artifact in session.artifacts
        if artifact.artifact_kind == ("video" if audio_mode == "mux" else "audio")
    )
    subprocess.run(
        [
            binary,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(store.root / target.storage_key),
            "-map",
            "0:a:0",
            "-f",
            "null",
            "-",
        ],
        check=True,
    )
