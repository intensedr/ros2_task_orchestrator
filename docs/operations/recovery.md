# Recovery

Clients recover state through public query services instead of scraping logs.

## Late Client Recovery

A client that starts after task completion can query recent state:

```bash
ros2 service call /task_orchestrator/list_task_records \
  task_orchestrator_msgs/srv/ListTaskRecordsV1 "{}"

ros2 service call /task_orchestrator/list_events \
  task_orchestrator_msgs/srv/ListEventsV1 "{}"
```

## In-Memory Recovery

By default, records and events are retained in bounded in-memory caches:

- `task_record_limit`
- `event_record_limit`

Set either limit to `0` to disable that cache.

## Restart Recovery

Enable SQLite storage for recovery across node restarts:

```yaml
storage:
  enabled: true
  sqlite_path: "/tmp/task_orchestrator.sqlite3"
```
