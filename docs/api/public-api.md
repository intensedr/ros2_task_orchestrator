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
