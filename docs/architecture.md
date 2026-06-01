# Architecture

ROS2 Task Orchestrator is split into a small ROS2-native core, public message
contracts and examples.

## Packages

- `task_orchestrator_msgs`: public action, service and message contracts.
- `task_orchestrator_core`: core node, task registry and task lifecycle logic.
- `task_orchestrator_examples`: example configs and launch files.

## Core Boundary

The core owns:

- task lifecycle
- task execution through existing ROS2 actions and services
- active task state
- terminal results
- feedback
- events

The core does not own:

- cloud routing
- tenant authorization
- product-specific mission formats
- UI state
- protocol-specific bridge behavior

## Runtime Shape

The core loads a YAML task registry, publishes active tasks, executes built-in
`system/wait`, `system/mission`, `system/cancel_task` and `system/stop` tasks,
and dispatches configured tasks to existing ROS2 services or actions. It also
forwards cancellation to cancelable active tasks and reloads task configuration
from `tasks_config_path` without a node restart.

Conflict handling covers blocking tasks, reentrant tasks, non-reentrant
same-type replacement and configured cancel-as-success reporting.

Observability is ROS2-native and dependency-free by default: task events include
structured `data_json`, all tasks publish start and terminal feedback, and task
event logs are emitted as JSON through the standard ROS2 logger. Recent events
are kept in a bounded in-memory cache controlled by `event_record_limit`; set
the limit to `0` to disable that cache.
Mission lifecycle, mission subtask lifecycle and system control/config changes
also publish structured events.
Structured event logs use the `task_orchestrator.event.v1` schema across task,
mission and system events.

The control system tasks use the same execute-task lifecycle as user-defined
tasks, but bypass blocking admission so an active blocking task can still be
canceled or stopped.

Canceling an accepted `/task_orchestrator/execute_task` action goal forwards
the cancellation request to the active backing task when it exposes a cancel
callback.

Task records are process-local and storage-free by default. They are bounded by
`task_record_limit`; active records are preserved even when the limit is
reached. `/task_orchestrator/list_task_records` provides filtered queries over
that bounded in-memory set without requiring storage.

SQLite durability is available as an optional backend through
`storage.enabled` and `storage.sqlite_path`. When enabled, task records and
events are written to SQLite using Python standard-library `sqlite3`; the same
query services read from SQLite for recovery across node restarts.
