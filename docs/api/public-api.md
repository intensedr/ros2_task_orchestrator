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

## Field Reference

Field-level message and service contracts are documented in
[Public API Reference](../public_api_reference.md).
