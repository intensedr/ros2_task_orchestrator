import json
import math
import os
import shutil
import struct
import subprocess

import pytest

rclpy = pytest.importorskip("rclpy")

try:
    from rclpy.parameter import Parameter
    from sensor_msgs.msg import CompressedImage
    from task_orchestrator_media.manager_node import MediaManagerNode

    from task_orchestrator_media_msgs.msg import AudioChunkV1
    from task_orchestrator_media_msgs.srv import (
        GetMediaArtifactAccessV1,
        ListMediaDevicesV1,
        StartRecordingV1,
        StopRecordingV1,
    )
except ImportError:
    pytest.skip("generated ROS media interfaces are not sourced", allow_module_level=True)


def test_manager_reports_source_health_and_rejected_frames(tmp_path, monkeypatch):
    monkeypatch.setenv("ROS_LOG_DIR", str(tmp_path / "ros-logs"))
    rclpy.init()
    node = None
    try:
        devices_json = json.dumps(
            [
                {
                    "device_id": "front",
                    "source_topic": "/camera/compressed",
                }
            ]
        )
        node = MediaManagerNode(
            parameter_overrides=[
                Parameter("spool_path", value=str(tmp_path / "spool")),
                Parameter("min_free_bytes", value=0),
                Parameter("max_frame_bytes", value=3),
                Parameter("recording_backend", value="frame_sequence"),
                Parameter("devices_json", value=devices_json),
            ]
        )

        accepted = CompressedImage()
        accepted.format = "jpeg"
        accepted.data = [1, 2, 3]
        node._on_frame("front", accepted)

        rejected = CompressedImage()
        rejected.format = "jpeg"
        rejected.data = [1, 2, 3, 4]
        node._on_frame("front", rejected)

        response = node._list_devices(ListMediaDevicesV1.Request(), ListMediaDevicesV1.Response())

        assert len(response.devices) == 1
        assert response.devices[0].state == "ONLINE"
        assert response.devices[0].received_frames == 1
        assert response.devices[0].rejected_frames == 1

        start_request = StartRecordingV1.Request()
        start_request.api_version = "v1"
        start_request.session_id = "test-recording"
        start_request.device_id = "front"
        start_response = node._start_recording(start_request, StartRecordingV1.Response())
        assert start_response.success is True
        assert start_response.session.state == "RECORDING"

        node._on_frame("front", accepted)

        stop_request = StopRecordingV1.Request()
        stop_request.session_id = "test-recording"
        stop_response = node._stop_recording(stop_request, StopRecordingV1.Response())
        assert stop_response.success is True
        assert stop_response.session.state == "FINALIZED"
        assert stop_response.session.frame_count == 1
        assert len(stop_response.session.artifacts) == 1
        artifact_path = tmp_path / "spool" / stop_response.session.artifacts[0].storage_key
        assert artifact_path.is_file()

        access_request = GetMediaArtifactAccessV1.Request()
        access_request.artifact_id = stop_response.session.artifacts[0].artifact_id
        access_response = node._get_artifact_access(
            access_request,
            GetMediaArtifactAccessV1.Response(),
        )
        assert access_response.found is True
        assert access_response.error_code == "UPLOAD_DISABLED"
    finally:
        if node is not None:
            node.destroy_node()
        rclpy.try_shutdown()


def test_manager_start_stop_produces_mp4(tmp_path, monkeypatch):
    binary = os.environ.get("TASK_ORCHESTRATOR_TEST_FFMPEG") or shutil.which("ffmpeg")
    if binary is None:
        pytest.skip("FFmpeg is not installed")

    frame_path = tmp_path / "manager-source.jpg"
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
    monkeypatch.setenv("ROS_LOG_DIR", str(tmp_path / "ros-logs-mp4"))
    rclpy.init()
    node = None
    try:
        devices_json = json.dumps([{"device_id": "front", "source_topic": "/camera/compressed"}])
        audio_devices_json = json.dumps(
            [
                {
                    "device_id": "microphone",
                    "source_topic": "/audio/pcm",
                    "sample_format": "s16le",
                    "sample_rate_hz": 48000,
                    "channels": 1,
                }
            ]
        )
        node = MediaManagerNode(
            parameter_overrides=[
                Parameter("spool_path", value=str(tmp_path / "mp4-spool")),
                Parameter("min_free_bytes", value=0),
                Parameter("max_spool_bytes", value=80 * 1024 * 1024),
                Parameter("max_recording_bytes", value=64 * 1024 * 1024),
                Parameter("recording_fps", value=10.0),
                Parameter("ffmpeg_binary", value=binary),
                Parameter("devices_json", value=devices_json),
                Parameter("audio_devices_json", value=audio_devices_json),
            ]
        )
        start_request = StartRecordingV1.Request()
        start_request.api_version = "v1"
        start_request.session_id = "manager-mp4"
        start_request.device_id = "front"
        start_request.audio_device_id = "microphone"
        start_request.audio_mode = "mux"
        start_response = node._start_recording(start_request, StartRecordingV1.Response())
        assert start_response.success is True
        assert start_response.session.recording_backend == "ffmpeg_mp4"

        jpeg = frame_path.read_bytes()
        for index in range(12):
            message = CompressedImage()
            message.header.stamp.sec = 1 + index // 10
            message.header.stamp.nanosec = (index % 10) * 100_000_000
            message.header.frame_id = "front_optical"
            message.format = "jpeg"
            message.data = jpeg
            node._on_frame("front", message)
            samples = b"".join(
                struct.pack("<h", int(12_000 * math.sin(2 * math.pi * 440 * sample / 48_000)))
                for sample in range(index * 4_800, (index + 1) * 4_800)
            )
            audio = AudioChunkV1()
            audio.stamp.sec = 1 + index // 10
            audio.stamp.nanosec = (index % 10) * 100_000_000
            audio.sample_format = "s16le"
            audio.sample_rate_hz = 48_000
            audio.channels = 1
            audio.data = samples
            node._on_audio("microphone", audio)

        stop_request = StopRecordingV1.Request()
        stop_request.session_id = "manager-mp4"
        stop_response = node._stop_recording(stop_request, StopRecordingV1.Response())

        assert stop_response.success is True
        assert stop_response.session.state == "FINALIZED"
        assert stop_response.session.frame_count == 12
        assert stop_response.session.audio_chunk_count == 12
        assert stop_response.session.audio_mode == "mux"
        assert stop_response.session.artifacts[0].mime_type == "video/mp4"
        video_path = tmp_path / "mp4-spool" / stop_response.session.artifacts[0].storage_key
        subprocess.run(
            [
                binary,
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(video_path),
                "-map",
                "0:a:0",
                "-f",
                "null",
                "-",
            ],
            check=True,
        )
    finally:
        if node is not None:
            node.destroy_node()
        rclpy.try_shutdown()
