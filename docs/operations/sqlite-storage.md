# SQLite Storage

SQLite durability is optional and disabled by default. The default runtime path
does not require SQLite configuration or external storage services.

## Enable Storage

```yaml
task_orchestrator:
  ros__parameters:
    storage:
      enabled: true
      sqlite_path: "/tmp/task_orchestrator.sqlite3"
      retention_days: 30
```

`sqlite3` is part of the Python standard library, so enabling SQLite does not
add a runtime package dependency.

## Stored Data

- Task records.
- Task events.
- Queued task scheduling fields required for restart recovery.
- Runtime duration and total duration fields.
- Idempotency keys, tracing IDs and fleet-safe context fields.

## Query Behavior

When SQLite storage is enabled:

- `/task_orchestrator/get_task` reads from SQLite when the task is not in the
  in-memory cache.
- `/task_orchestrator/list_task_records` reads from SQLite.
- `/task_orchestrator/list_events` reads from SQLite.
- Both list services support task, source, status, fleet-safe context,
  tracing and idempotency filters.
- `QUEUED` task records are recovered on node startup and submitted back
  through the normal execute-task path.

When storage is disabled, these services use bounded in-memory caches.

## Retention

`storage.retention_days` removes records and events older than the configured
age. Set it to `0` to disable retention deletion.
