# Media Capture

The optional media packages provide snapshots, video/audio recording and
automatic object-storage upload for robots and drones without sending binary
payloads through the orchestrator.

## Data Flow

```text
sensor_msgs/CompressedImage + AudioChunkV1/raw PCM
  -> media_manager + FFmpeg
    -> bounded local spool
      -> asynchronous S3-compatible uploader
        -> artifact descriptor and transfer status
          -> task result/event
```

The default `ffmpeg_mp4` backend sends JPEG/PNG frames and optional PCM audio to
FFmpeg. It produces either one H.264/AAC MP4 or separate MP4 and AAC/M4A files.
Fragmented MP4 keeps completed fragments decodable after an interrupted write.
The video-only `frame_sequence` backend remains available as a low-dependency
fallback.

## Configure Image And Audio Devices

```yaml
media_manager:
  ros__parameters:
    spool_path: "~/.local/share/task_orchestrator/media"
    max_spool_bytes: 10737418240
    min_free_bytes: 268435456
    max_frame_bytes: 16777216
    max_audio_chunk_bytes: 4194304
    recording_queue_max_bytes: 134217728
    recording_backend: "ffmpeg_mp4"
    ffmpeg_binary: "ffmpeg"
    ffmpeg_video_codec: "libx264"
    ffmpeg_crf: 23
    recording_fps: 30.0
    max_recording_bytes: 2147483648
    ffmpeg_audio_codec: "aac"
    ffmpeg_audio_bitrate_kbps: 128
    devices_json: >-
      [{
        "device_id": "front_rgb",
        "source_topic": "/camera/image/compressed",
        "media_kind": "image",
        "mime_type": "image/jpeg",
        "extension": ".jpg",
        "frame_id": "front_camera_optical_frame",
        "capabilities": ["snapshot", "recording"]
      }]
    audio_devices_json: >-
      [{
        "device_id": "body_microphone",
        "source_topic": "/audio/pcm",
        "message_type": "task_orchestrator_media_msgs/msg/AudioChunkV1",
        "sample_format": "s16le",
        "sample_rate_hz": 48000,
        "channels": 1,
        "frame_id": "body_microphone_frame",
        "capabilities": ["recording"]
      }]
```

Start the manager with:

```bash
ros2 launch task_orchestrator_media media_manager.launch.py
```

The MP4 backend requires `ffmpeg`; the project Docker image includes it. On an
Ubuntu/ROS host install it with `sudo apt-get install ffmpeg`. Set
`recording_backend: "frame_sequence"` only for video-only recording when FFmpeg
is unavailable or every original compressed frame must be retained.

### Audio Input

Microphones publish interleaved raw PCM as
`task_orchestrator_media_msgs/msg/AudioChunkV1`. Supported formats are `u8`,
`s16le`, `s24le`, `s32le`, `f32le` and `f64le`. A source publishing
`audio_common_msgs/msg/AudioData` can also be selected using `message_type`;
because that message carries only bytes, configure its sample format, rate and
channel count in `audio_devices_json` and install `audio_common_msgs` in that
deployment.

`StartRecordingV1` accepts `audio_device_id` and:

- `audio_mode: "mux"` for one H.264/AAC MP4 artifact;
- `audio_mode: "separate"` for video MP4 plus a separate AAC/M4A artifact;
- `audio_mode: "none"` for video only.

When an audio device is supplied and the mode is empty, `mux` is selected.
Format changes during a session are rejected. V1 preserves PCM chunk order but
does not yet correct microphone clock drift or insert silence for missing
chunks.

## Media API

- Action `/task_orchestrator/media/capture`: capture one image or a burst.
- Service `/task_orchestrator/media/start_recording`: start a background
  video/audio session.
- Service `/task_orchestrator/media/stop_recording`: finalize a session.
- Service `/task_orchestrator/media/get_session`: inspect session and upload
  status.
- Service `/task_orchestrator/media/get_artifact_access`: obtain a public or
  short-lived signed download URL after upload.
- Service `/task_orchestrator/media/list_devices`: list image and audio source
  health and receive/reject counters.

`CaptureMediaV1.timeout_sec` is the deadline for the whole burst. Recording
`max_duration_sec=0` disables the duration limit. Image and audio callback
queues are bounded by count and bytes; overflow increments the corresponding
drop counter instead of blocking sensor callbacks. `recording_fps` defines MP4
playback speed; source timestamps remain in sidecars and recovery journals.

## Automatic S3/MinIO Upload

The uploader supports AWS S3 and S3-compatible endpoints such as MinIO. It is
disabled by default. A minimal AWS configuration is:

```yaml
media_manager:
  ros__parameters:
    upload_enabled: true
    upload_bucket: "robot-media"
    upload_prefix: "fleet-a"
    upload_region: "eu-central-1"
    upload_presign_default_sec: 900
    upload_presign_max_sec: 3600
```

For MinIO, set its endpoint. Plain HTTP requires an explicit development-only
opt-in:

```yaml
    upload_endpoint_url: "http://minio:9000"
    upload_addressing_style: "path"
    upload_allow_insecure_endpoint: true
```

Do not put access keys in ROS parameters. Boto3 uses its normal credential
chain: for example `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`, an AWS profile,
container credentials or an instance role. Optional server-side encryption is
configured with `upload_server_side_encryption: "AES256"` or `"aws:kms"` and
`upload_kms_key_id`.

Finalized JPEG/PNG, MP4 and M4A files enter `UPLOADING` without blocking the
recording service. Success changes the state to `AVAILABLE`; exhausted retries
produce `FAILED` plus a bounded diagnostic. Transfer state survives restart,
and unfinished items are queued again. Local files are retained. The fallback
frame-sequence bundle is not uploaded as one object; use `ffmpeg_mp4` when
automatic upload is required.

`MediaArtifactV1.remote_object_key` is safe to persist and is not a credential.
If `upload_public_base_url` is set, `public_url` contains the resulting HTTPS
URL. Otherwise `GetMediaArtifactAccessV1` generates a presigned URL whose TTL is
bounded by `upload_presign_max_sec`. Presigned URLs are never written into
session files or task events.

## Orchestrator Tasks

Copy `task_orchestrator_media/config/media_tasks.yaml` entries into the
deployment task registry. A mission can start recording before movement and
stop it afterward:

```json
{
  "mission_id": "inspection-42",
  "subtasks": [
    {
      "subtask_id": "record-start",
      "task_name": "media/start_recording",
      "task_data_json": {
        "api_version": "v1",
        "session_id": "inspection-42-recording",
        "device_id": "front_rgb",
        "audio_device_id": "body_microphone",
        "audio_mode": "mux",
        "mission_id": "inspection-42",
        "max_duration_sec": 600
      }
    },
    {
      "subtask_id": "inspect",
      "task_name": "drone/go_to_waypoint",
      "depends_on": ["record-start"],
      "task_data_json": {}
    },
    {
      "subtask_id": "record-stop",
      "task_name": "media/stop_recording",
      "depends_on": ["inspect"],
      "task_data_json": {"session_id": "inspection-42-recording"}
    }
  ]
}
```

## Storage And Recovery

- Snapshots are atomic and include JSON sidecars and SHA-256 checksums.
- Video frames and PCM chunks use separate append-only, fsynced journals.
- Finalization verifies FFmpeg, hashes each file and writes video/audio
  sidecars plus terminal session state.
- Active sessions reserve disk capacity so terminal metadata can still be
  committed under spool pressure.
- Open journals are recovered on startup as `RECOVERED` sessions.
- Upload state is persisted beside each artifact and unfinished uploads are
  rescheduled on startup.
- Writes are rejected before violating `max_spool_bytes` or `min_free_bytes`.

Recording idempotency survives restart. Snapshot idempotency is process-local
in V1, so durable callers should also use task-level idempotency. For normal
file artifacts, `size_bytes` and `sha256` describe the file at `storage_key`.

## Current Limits And Security

V1 accepts complete JPEG/PNG images and raw PCM. It does not yet handle ROS
compressed-depth payloads, microphone clock-drift correction, crash-resumable
multipart upload checkpoints, automatic local deletion, or live streaming.

`storage_key` and `remote_object_key` are non-secret identifiers. Never put
credentials or presigned URLs in `metadata_json`. Treat an access-service URL as
a temporary credential, protect that service with SROS2, and avoid logging its
response. Encrypt the local spool when the captured environment is sensitive.
