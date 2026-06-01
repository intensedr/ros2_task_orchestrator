# Public API

The public API is defined by `task_orchestrator_msgs`.

## Actions

- `/task_orchestrator/execute_task`: `task_orchestrator_msgs/action/ExecuteTaskV1`

Built-in system tasks:

- `system/cancel_task`: executes `CancelTasksV1` semantics through
  `/task_orchestrator/execute_task`
- `system/mission`: executes linear missions with `MissionV1` result shape
- `system/stop`: executes `StopTasksV1` semantics through
  `/task_orchestrator/execute_task`
- `system/wait`: executes a local wait with `WaitV1` result shape

## Topics

- `/task_orchestrator/active_tasks`: `ActiveTaskArrayV1`
- `/task_orchestrator/results`: `TaskResultV1`
- `/task_orchestrator/events`: `TaskEventV1` with structured `data_json`
- `/task_orchestrator/feedback`: `TaskFeedbackV1` for task start, terminal
  states and mission progress

## Services

- `/task_orchestrator/list_tasks`: `ListTasksV1`
- `/task_orchestrator/list_events`: `ListEventsV1`, filtered query over recent
  in-memory events or SQLite events when storage is enabled
- `/task_orchestrator/get_task`: `GetTaskV1`, backed by bounded in-memory task
  records or SQLite records when storage is enabled
- `/task_orchestrator/list_task_records`: `ListTaskRecordsV1`, filtered query
  over in-memory task records or SQLite records when storage is enabled
- `/task_orchestrator/cancel_tasks`: `CancelTasksV1`
- `/task_orchestrator/pause_tasks`: `PauseTasksV1`
- `/task_orchestrator/resume_tasks`: `ResumeTasksV1`
- `/task_orchestrator/reload_config`: `ReloadConfigV1`, reloads
  `tasks_config_path`
- `/task_orchestrator/stop`: `StopTasksV1`

## Field Reference

Field-level message and service details are documented in
[Public API Reference](public_api_reference.md).
