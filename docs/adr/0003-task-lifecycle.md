# ADR 0003: Task Lifecycle Model

## Context

The orchestrator needs deterministic status transitions for actions, services,
missions, queues and policy checks. External clients need a stable model that
does not depend on ROS2 action internals.

## Decision

Use this lifecycle for every task execution:

```text
RECEIVED
  -> REJECTED
  -> QUEUED
  -> IN_PROGRESS
  -> PAUSING -> PAUSED -> RESUMING -> IN_PROGRESS
  -> DONE
  -> ERROR
  -> CANCELED
  -> SKIPPED
```

Rules:

- `RECEIVED` means the request entered the orchestrator.
- `REJECTED` means pre-start validation or policy denied the request.
- `QUEUED` means the request is accepted but waiting for scheduling or
  resources.
- Queued requests are ordered by ready time, priority and FIFO sequence.
- `IN_PROGRESS` means the backing ROS2 action or service was started.
- `DONE`, `ERROR`, `CANCELED`, `SKIPPED` are terminal states.
- Every terminal state publishes exactly one result and one terminal event.
- Service-backed tasks may not be cancelable. If cancellation is requested, the
  orchestrator waits up to `cancel_timeout` and then reports the configured
  result.
- Pause/resume is capability-based and can return `UNSUPPORTED`.

## Consequences

- Clients can reason about tasks without knowing whether the backing task is an
  action, service or system task.
- Queuing and policy checks fit naturally into the lifecycle.
- Service cancellation limitations are visible and testable.
