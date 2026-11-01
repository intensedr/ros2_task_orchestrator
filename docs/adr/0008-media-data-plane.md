# ADR 0008: Media Control And Data Planes

## Context

Robot and drone missions need snapshots, recordings and live streams. Encoded
media is orders of magnitude larger than task metadata and has different
delivery, retention and security requirements.

The generic action and service clients convert backend responses into JSON.
Task results and events may then be retained in memory, logs and SQLite. Sending
media bytes through those paths would duplicate large payloads and make task
recovery depend on media volume.

## Decision

Separate media control from media data:

- The orchestrator starts and stops media operations and records their status.
- `task_orchestrator_media` owns local capture sessions and a bounded spool.
- Media bytes are written directly to the spool and later transferred by an
  optional uploader or served by a streaming gateway.
- Task results contain only `MediaArtifactV1` or `MediaSessionV1` descriptors.
- `storage_key` is a relative, non-secret key. Signed URLs, credentials and
  access tokens are never published through ROS events or task results.
- The core SQLite backend continues to store task and event metadata only.

The default recording backend feeds compressed image frames and optional raw
PCM to FFmpeg. It produces either a bounded, fragmented H.264/AAC MP4 artifact
or separate MP4 and M4A artifacts. Fragmentation keeps completed fragments
decodable if writing is interrupted. An append-only image-sequence backend
remains available as a video-only fallback.

Finalized file artifacts may enter an optional S3-compatible upload queue. The
artifact contract persists transfer state and a non-secret object key. A
separate service creates time-bounded presigned URLs on demand so bearer URLs
do not enter task history or durable session metadata.

## Session Lifecycle

`StartRecordingV1` returns after creating a background session. This allows a
robot or drone movement task to start while recording continues. Sessions are
finalized by `StopRecordingV1`, their configured maximum duration, node
shutdown, or a terminal event for the owning mission.

Each recording writes recovery journals for video frames and PCM chunks. The
frame-sequence manifest points to its journal instead of duplicating it; MP4
recovery uses journals for timing and session metadata. An active session holds
physical finalization space and, for an external encoder, a logical
bounded-capacity reservation. On restart, the manager finalizes open journals
as `RECOVERED` sessions and reschedules incomplete uploads.

## Consequences

- Large data never enters task event logs or core SQLite storage.
- Media capture can overlap with blocking movement tasks without immediately
  changing the V1 mission executor.
- Local disk use is bounded independently of task-history retention.
- Audio and object upload remain optional at deployment time; live streaming
  remains a separate future gateway.
- MP4 encoding consumes CPU; deployments can select the frame-sequence fallback.
  Hardware encoders require explicit backend profiles because their rate-control
  and device options are not interchangeable with the default `libx264` profile.
