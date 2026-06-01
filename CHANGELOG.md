# Changelog

## 0.1.0 - 2026-06-01

- Added YAML-backed task registry with built-in `system/wait`,
  `system/mission`, `system/cancel_task` and `system/stop`.
- Added in-memory active task registry and active task publication.
- Added execute-task lifecycle handling for unknown tasks, `system/wait`,
  service-backed tasks and action-backed tasks.
- Added generic service-backed task execution with JSON-to-ROS2 request
  conversion and JSON result output.
- Added generic action-backed task execution with JSON-to-ROS2 goal conversion
  and JSON result output.
- Added active task cancellation metadata, action goal cancellation support and
  stop handling for `cancel_on_stop` tasks.
- Added `cancel_reported_as_success` task configuration for action tasks that
  treat canceled goals as successful completion.
- Added execute-task action-goal cancellation forwarding to cancelable active
  tasks.
- Added conflict policy handling for blocking tasks, reentrant tasks and
  non-reentrant same-type replacement.
- Added linear mission execution with ordered subtasks, skippable subtask
  failures and mission progress feedback.
- Added executable `system/cancel_task` and `system/stop` control tasks with
  stable JSON results.
- Added in-memory task records for `/task_orchestrator/get_task`.
- Added config reload from `tasks_config_path` with atomic registry replacement
  on success.
- Added `task_orchestrator_examples` with a Nav2 task registry, launch file and
  sample mission payload.
- Changed default storage settings to keep persistence disabled for lightweight
  startup.
- Added structured task event payloads, generic start/terminal task feedback
  and JSON task-event logs without new dependencies.
- Added configurable bounded in-memory task record retention with
  `task_record_limit`.
- Added `/task_orchestrator/list_task_records` for dependency-free querying of
  bounded in-memory task records.
- Added bounded in-memory event history with `event_record_limit` and
  `/task_orchestrator/list_events`.
- Added mission lifecycle, subtask lifecycle and system control/config events.
- Added optional SQLite task record and event storage behind `storage.enabled`.
- Added late-client recovery integration coverage using in-memory task records
  and events.
- Added exactly-once terminal result/event coverage for DONE, ERROR, REJECTED
  and CANCELED task states.
- Standardized structured event log payloads across task, mission and system
  events.
- Added stable error mapping for malformed task data, unavailable
  services/action servers and task timeouts.
- Added focused tests for task registry, active task tracking, task records,
  config reload, wait/mission execution, service/action-backed tasks,
  cancellation/stop behavior and conflict policies.
- Added ROS2 API integration tests for execute-task action calls and
  service-backed task execution.
- Added minimal MkDocs site and GitHub Pages deployment workflow.
