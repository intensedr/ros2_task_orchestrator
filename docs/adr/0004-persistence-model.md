# ADR 0004: Persistence Model

## Context

Karelics `task_manager` publishes active tasks and results, but it does not keep
a durable task history. External clients such as fleet agents need to recover
after reconnects without reconstructing state from logs.

## Decision

Use SQLite as the first optional local persistence backend. The core must not
require storage for normal startup.

Persist:

- task records
- task events

Storage is optional and disabled by default for lightweight deployments:

```yaml
storage:
  enabled: false
  sqlite_path: ""
  retention_days: 30
```

When storage is enabled, deployments provide a local SQLite path such as
`~/.local/share/task_orchestrator/tasks.db`. Query services read from SQLite
instead of the bounded in-memory caches.

## Consequences

- Late subscribers and reconnecting agents can query recent history.
- The default deployment stays fast and storage-free.
- SQLite keeps optional durable deployments simple and local.
- Very small embedded deployments can keep storage disabled.
