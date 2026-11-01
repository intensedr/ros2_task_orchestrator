# Public API

The public API is defined by `task_orchestrator_msgs`.

## Actions

- `/task_orchestrator/execute_task`: `ExecuteTaskV1`

## Topics

- `/task_orchestrator/active_tasks`: `ActiveTaskArrayV1`
- `/task_orchestrator/results`: `TaskResultV1`
- `/task_orchestrator/events`: `TaskEventV1`
- `/task_orchestrator/feedback`: `TaskFeedbackV1`

## Services

- `/task_orchestrator/list_tasks`: `ListTasksV1`
- `/task_orchestrator/get_task`: `GetTaskV1`
- `/task_orchestrator/cancel_tasks`: `CancelTasksV1`
- `/task_orchestrator/pause_tasks`: `PauseTasksV1`
- `/task_orchestrator/resume_tasks`: `ResumeTasksV1`
- `/task_orchestrator/validate_task`: `ValidateTaskV1`

Agent-ready mission operation:

- `/task_orchestrator/register_agent`: `RegisterAgentV1`
- `/task_orchestrator/list_agents`: `ListAgentsV1`
- `/task_orchestrator/claim_mission`: `ClaimMissionV1`
- `/task_orchestrator/release_mission`: `ReleaseMissionV1`
- `/task_orchestrator/validate_mission`: `ValidateMissionV1`
- `/task_orchestrator/submit_mission`: `SubmitMissionV1`
- `/task_orchestrator/cancel_mission`: `CancelMissionV1`
- `/task_orchestrator/pause_mission`: `PauseMissionV1`
- `/task_orchestrator/resume_mission`: `ResumeMissionV1`
- `/task_orchestrator/retry_mission`: `RetryMissionV1`
- `/task_orchestrator/get_mission_state`: `GetMissionStateV1`

Additional V1 query/admin endpoints:

- `/task_orchestrator/list_task_records`: `ListTaskRecordsV1`
- `/task_orchestrator/list_events`: `ListEventsV1`
- `/task_orchestrator/stop`: `StopTasksV1`
- `/task_orchestrator/reload_config`: `ReloadConfigV1`

## Optional Media API

The `task_orchestrator_media_msgs` package and media manager expose:

- `/task_orchestrator/media/capture`: `CaptureMediaV1`
- `/task_orchestrator/media/start_recording`: `StartRecordingV1`
- `/task_orchestrator/media/stop_recording`: `StopRecordingV1`
- `/task_orchestrator/media/get_session`: `GetMediaSessionV1`
- `/task_orchestrator/media/get_artifact_access`: `GetMediaArtifactAccessV1`
- `/task_orchestrator/media/list_devices`: `ListMediaDevicesV1`

These endpoints exchange descriptors only. Encoded media is never carried by
the task result or event API. `MediaDeviceStateV1`, `MediaSessionStateV1`,
`MediaUploadStateV1` and `MediaErrorCodeV1` provide stable string constants for
clients. `MediaSessionV1.recording_backend` identifies the selected storage
backend; the default `ffmpeg_mp4` backend returns a `video/mp4` artifact, while
`frame_sequence` returns a frame-manifest artifact.

`StartRecordingV1.audio_device_id` and `audio_mode` select no audio, an AAC
track muxed into MP4, or a separate M4A artifact. `MediaArtifactV1` returns
upload state and a non-secret remote object key. Temporary presigned URLs are
created only by `GetMediaArtifactAccessV1` and must not be copied into task
history.

## Built-In System Tasks

- `system/wait`
- `system/mission`
- `system/cancel_task`
- `system/stop`

## Agent Boundary

Agents register and heartbeat through the public API, then claim mission leases
before issuing mission commands. Agents produce structured mission JSON; the
core normalizes and validates that JSON before executing `system/mission`.
Planner selection, agent runtime loops and optional human approval remain
outside the core.

## Field Reference

Field-level message and service contracts are documented in
[Public API Reference](../public_api_reference.md).
