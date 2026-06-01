# Observability

Observability is ROS2-native and dependency-free by default.

## Live State

- `/task_orchestrator/active_tasks`
- `/task_orchestrator/results`
- `/task_orchestrator/events`
- `/task_orchestrator/feedback`

## Query State

- `/task_orchestrator/get_task`
- `/task_orchestrator/list_task_records`
- `/task_orchestrator/list_events`

## Events

`TaskEventV1` events include structured `data_json`. Task, mission, subtask,
system control and config reload operations all publish events through
`/task_orchestrator/events`.

Terminal task states publish exactly one terminal event:

- `task.completed`
- `task.failed`
- `task.canceled`
- `task.rejected`

## Feedback

All tasks publish start and terminal feedback through
`/task_orchestrator/feedback`. Missions also publish progress feedback.

## Structured Logs

The node logs event payloads as JSON through the standard ROS2 logger. The
schema name is `task_orchestrator.event.v1`.

Structured log payloads include:

- event identity and category
- task identity
- previous and current status
- error state
- duration fields
- result size
- in-memory record counters
- original event data
