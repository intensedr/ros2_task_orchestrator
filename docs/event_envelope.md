# Event Envelope

The event envelope is the common shape used by ROS2 events and optional
WebSocket, MQTT or Zenoh bridges. It is intentionally generic so products such
as fleet-management systems can consume it without adding product fields to the
core.

## ROS2 Event Message

`TaskEventV1` is published to `/task_orchestrator/events`. Field-level details
are documented in [Public API Reference](public_api_reference.md).

## Bridge JSON Envelope

```json
{
  "api_version": "v1beta1",
  "event_id": "01J00000000000000000000000",
  "event_type": "task.started",
  "task_id": "7b3a8a8d-4c8f-4d87-8c75-5cf9eae71ab7",
  "task_name": "navigation/navigate_to_pose",
  "source": "fleet-agent",
  "priority": 10,
  "correlation_id": "mission-2026-05-27-0001",
  "trace_id": "trace-01J00000000000000000000000",
  "robot_id": "amr-042",
  "fleet_id": "warehouse-a",
  "site_id": "site-1",
  "zone_id": "zone-7",
  "operator_id": "",
  "tenant_id": "",
  "idempotency_key": "mission-2026-05-27-0001/start",
  "created_at": "2026-05-27T11:59:58.000000Z",
  "started_at": "2026-05-27T12:00:00.000000Z",
  "finished_at": "",
  "previous_status": "QUEUED",
  "status": "IN_PROGRESS",
  "error_code": "",
  "error_message": "",
  "result_json": "{}",
  "duration_sec": 0.0,
  "total_duration_sec": 2.0,
  "data": {
    "mission_id": "mission-2026-05-27-0001"
  },
  "stamp": "2026-05-27T12:00:00.000000Z"
}
```

Structured ROS2 logs use the same event fields and add:

- `event`: fixed value `orchestrator_event`
- `schema`: fixed value `task_orchestrator.event.v1`
- `event_category`: prefix before the first dot in `event_type`
- `task_record_count`
- `event_record_count`

## Event Types

Task events:

- `task.received`
- `task.queued`
- `task.dequeued`
- `task.started`
- `task.feedback`
- `task.completed`
- `task.failed`
- `task.canceled`
- `task.rejected`
- `task.timeout`

Mission events:

- `mission.received`
- `mission.started`
- `mission.subtask.started`
- `mission.subtask.completed`
- `mission.subtask.failed`
- `mission.subtask.skipped`
- `mission.completed`
- `mission.failed`
- `mission.canceled`
- `mission.timeout`

System events:

- `system.cancel.requested`
- `system.cancel.completed`
- `system.stop.requested`
- `system.stop.completed`
- `system.config.reloaded`
- `system.config.reload_failed`
- `system.storage.error`

## Delivery Semantics

- Events are append-only facts.
- Recent events are queryable through `/task_orchestrator/list_events` while
  they remain in the bounded in-memory event cache.
- When SQLite storage is enabled, `/task_orchestrator/list_events` reads from
  the SQLite event history.
- Every task terminal state must produce exactly one terminal event.
- Bridges may retry delivery but must preserve `event_id`.
- External clients deduplicate by `event_id`.

## Data Field Rules

- `data_json` and bridge `data` are for structured context.
- `task.received` data includes generated-ID metadata, priority, scheduling
  hints, fleet-safe context fields and tags.
- `task.started` data includes task definition flags such as task server type,
  blocking, reentrant, resource locks, task groups and cancellation behavior.
- Terminal task event data includes status, error presence, result presence and
  duration fields.
- Product-specific data can be included by clients, but the core must not depend
  on those fields.
- Large binary data must not be embedded in events.
- Sensitive tokens must never be included.
- Mission subtask start events include graph metadata such as `depends_on`,
  `condition_action`, `graph_wave_index` and `ready_subtask_ids`.
- Mission terminal events include final subtask counts, and timeout terminal
  events use `mission.timeout`.

## External Client Consumption Pattern

An external fleet agent consumes events through the public API:

- Subscribe to `/task_orchestrator/events`, `/results`, `/feedback` and
  `/active_tasks`.
- Query `/task_orchestrator/list_events` after reconnects to recover recent
  event history from memory or from SQLite when storage is enabled.
- For audit replay, request matching events and reverse the newest-first
  response before rebuilding state transitions.
- Convert ROS2 messages into the bridge JSON envelope.
- Add product-specific routing metadata outside the core if needed.
- Send events to the external backend over WebSocket, Zenoh or MQTT.
