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

For fleet or bridge clients, both history services can be narrowed by
`robot_id`, `fleet_id`, `site_id`, `zone_id`, `operator_id`, `tenant_id`,
`trace_id` and `idempotency_key`.

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

When SQLite storage is enabled, task records and events are durable across node
restarts. `QUEUED` task records are recovered on startup and resubmitted through
the same execution path as live `/task_orchestrator/execute_task` requests.

Recovered queued tasks preserve:

- `task_id`
- `task_name`
- `task_data_json`
- `scheduled_at`
- `delay_sec`
- `deadline_at`
- `timeout_sec`
- `queue_on_conflict`
- `idempotency_key`
- fleet-safe metadata and tracing fields

The recovery path publishes `task.recovered` before the recovered task proceeds
through `task.received`, `task.queued`, `task.started` and the terminal event.

Terminal error results keep stable top-level `error_code` and `error_message`
fields and include a structured `error` object in `result_json` when the result
payload is a JSON object.
