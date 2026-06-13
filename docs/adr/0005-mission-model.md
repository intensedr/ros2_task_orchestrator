# ADR 0005: Mission Model

## Context

The baseline mission model executed subtasks sequentially. The project now needs
dependency-aware missions without breaking existing linear payloads.

## Decision

Model missions as task graphs.

The implemented execution path validates missions as directed acyclic graphs and
runs deterministic ready waves. A subtask becomes ready when all `depends_on`
subtasks completed or were skipped. Ready subtasks keep payload order inside the
mission callback so existing linear mission payloads keep stable behavior.

Mission node fields:

- `subtask_id`
- `task_name`
- `task_data_json`
- `allow_skipping`
- `retry_policy`
- `retry_backoff_sec`
- `retry_backoff_type`
- `retry_max_backoff_sec`
- `retry_error_codes`
- `timeout_sec`
- `depends_on`
- `condition`
- `condition_json`

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
- `condition`/`condition_json` action `skip` records a skipped subtask without
  executing the child task.
- `condition`/`condition_json` action `abort` fails the mission with
  `POLICY_REJECTED`.
- `max_attempts`, `retry_backoff_sec`, structured `retry_policy`,
  `timeout_sec` and mission `deadline_at` are honored by the graph executor.
- Mission/subtask timeout failures publish `mission.timeout`.
- YAML/JSON mission templates are resolved into the same mission payload shape
  before validation and execution.

## Consequences

- Linear missions keep familiar behavior.
- Branching uses the same mission API shape.
- External clients can display both mission-level and subtask-level progress.
- Audit consumers can replay mission state from task and mission events returned
  by `ListEventsV1`; SQLite storage makes that history durable across restarts.
