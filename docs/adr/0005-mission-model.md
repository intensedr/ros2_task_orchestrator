# ADR 0005: Mission Model

## Context

The baseline mission model executes subtasks sequentially. The new project needs
to keep that simple path while keeping the mission API compatible with richer
graph execution.

## Decision

Model missions as task graphs.

The implemented execution path is a linear graph compatible with
Karelics-style missions. Mission node fields include dependency and condition
metadata so graph execution can share the same public shape.

Mission node fields:

- `subtask_id`
- `task_name`
- `task_data_json`
- `allow_skipping`
- `retry_policy`
- `timeout_sec`
- `depends_on`
- `condition`

Mission result fields:

- mission task ID
- mission terminal status
- ordered subtask results
- skipped subtasks
- failed subtask ID and error code when applicable

Mission execution rules:

- A mission is itself a task.
- Subtasks are normal tasks and publish normal results.
- Cancelling a mission cascades cancellation to the active subtask.
- A failed non-skippable subtask fails the mission.
- A failed skippable subtask records `SKIPPED` and continues.

## Consequences

- Linear missions keep familiar behavior.
- Branching can use the same mission API shape.
- External clients can display both mission-level and subtask-level progress.
