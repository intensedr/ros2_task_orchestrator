# ADR 0002: API Compatibility With Karelics Task Manager

## Context

Karelics `task_manager` already provides a useful API and behavior model. The
new project preserves the useful concepts, but direct namespace reuse would
create confusion and make API changes harder.

## Decision

Create new package and interface names under `task_orchestrator_msgs`.

Provide compatibility as an adapter, not as the primary API:

- Karelics-style YAML config converter.
- Optional endpoint aliases:
  - `/task_manager/execute_task`
  - `/task_manager/active_tasks`
  - `/task_manager/results`
- Optional compatibility message conversion where practical.

Compatibility aliases are controlled by:

```yaml
task_orchestrator:
  ros__parameters:
    enable_compatibility_aliases: false
```

The primary public API uses the `/task_orchestrator` namespace and is
documented in [Public API](../public_api.md).

## Consequences

- Existing concepts are familiar to `task_manager` users.
- The new project can evolve event, feedback, persistence and scheduling APIs.
- Namespace collision with an installed Karelics package is avoided.
- Migration work is explicit and testable.
