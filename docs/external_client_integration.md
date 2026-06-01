# External Client Integration

External systems integrate through the public ROS2 API.

## Command Flow

```text
Backend/UI
  -> external fleet agent
    -> ROS2 action client
      -> /task_orchestrator/execute_task
        -> existing ROS2 service/action
```

## State Flow

```text
/task_orchestrator/events
/task_orchestrator/active_tasks
/task_orchestrator/results
/task_orchestrator/feedback
/task_orchestrator/list_events
/task_orchestrator/list_task_records
  -> external fleet agent
    -> backend/UI
```

## Client Responsibilities

- Convert product-specific task JSON into `ExecuteTaskV1` goals.
- Subscribe to orchestrator topics for live state.
- Query `/task_orchestrator/list_events` and
  `/task_orchestrator/list_task_records` after reconnects.
- Enable SQLite storage on robots that need recovery across node restarts.
- Handle cloud authentication and routing.
- Deduplicate events by `event_id`.
- Keep product-specific metadata out of core orchestrator code.
