# Public API Reference

This file defines the public contract for `task_orchestrator_msgs`.
Interface names are versioned with `V1` until the project reaches `v1.0`.

## Naming

- Node name: `task_orchestrator`
- Action namespace: `/task_orchestrator`
- Message package: `task_orchestrator_msgs`
- Internal Python package: `task_orchestrator_core`
- Optional bridges: `task_orchestrator_bridge_*`

## Common Fields

Public messages carry a common metadata shape where practical:

- `api_version`: API version string, initially `"v1alpha1"`.
- `task_id`: unique task execution ID.
- `task_name`: configured task type name.
- `source`: caller or system that requested the task.
- `correlation_id`: caller-provided ID for tracing a workflow.
- `priority`: higher value means higher priority.
- `created_at`, `started_at`, `finished_at`: ROS2 `builtin_interfaces/Time`.
- `status`: one of the `TaskStatusV1` constants.
- `error_code`: stable machine-readable error code.
- `error_message`: human-readable diagnostic text.
- `data_json`, `result_json`, `feedback_json`: JSON payloads.

## Status Values

`TaskStatusV1.msg`

```text
string RECEIVED=RECEIVED
string QUEUED=QUEUED
string IN_PROGRESS=IN_PROGRESS
string PAUSING=PAUSING
string PAUSED=PAUSED
string RESUMING=RESUMING
string DONE=DONE
string ERROR=ERROR
string CANCELED=CANCELED
string SKIPPED=SKIPPED
string REJECTED=REJECTED
```

## Error Codes

Stable error codes:

- `UNKNOWN_TASK`
- `DUPLICATE_TASK_ID`
- `TASK_DATA_PARSING_FAILED`
- `TASK_START_FAILED`
- `TASK_CANCEL_FAILED`
- `TASK_TIMEOUT`
- `POLICY_REJECTED`
- `RESOURCE_CONFLICT`
- `SERVER_UNAVAILABLE`
- `UNSUPPORTED`
- `INTERNAL_ERROR`

## Execute Task

`action/ExecuteTaskV1.action`

```text
string api_version
string task_id
string task_name
string source
string correlation_id
int32 priority
string task_data_json
string[] tags
---
string api_version
string task_id
string task_name
string source
string correlation_id
string task_status
string error_code
string error_message
string task_result_json
builtin_interfaces/Time created_at
builtin_interfaces/Time started_at
builtin_interfaces/Time finished_at
---
string api_version
string task_id
string task_name
string task_status
float32 progress
string feedback_json
builtin_interfaces/Time stamp
```

Semantics:

- Empty `task_id` is allowed and generates a UUID.
- Unknown `task_name` returns `REJECTED` with `UNKNOWN_TASK`.
- Invalid JSON returns `ERROR` with `TASK_DATA_PARSING_FAILED`.
- Cancellation through the action server maps to task cancellation when the
  backing task supports cancellation.

## Active Tasks

`msg/ActiveTaskV1.msg`

```text
string api_version
string task_id
string task_name
string source
string correlation_id
int32 priority
string task_status
builtin_interfaces/Time created_at
builtin_interfaces/Time started_at
string[] tags
```

`msg/ActiveTaskArrayV1.msg`

```text
ActiveTaskV1[] active_tasks
builtin_interfaces/Time stamp
```

Topic:

```text
/task_orchestrator/active_tasks
```

QoS:

- Reliable.
- Transient local.

## Results

`msg/TaskResultV1.msg`

```text
string api_version
string task_id
string task_name
string source
string correlation_id
string task_status
string error_code
string error_message
string task_result_json
builtin_interfaces/Time created_at
builtin_interfaces/Time started_at
builtin_interfaces/Time finished_at
```

Topic:

```text
/task_orchestrator/results
```

QoS:

- Reliable.
- Volatile by default.
- Recent task history is available through bounded query services.

## Events

`msg/TaskEventV1.msg`

```text
string api_version
string event_id
string event_type
string task_id
string task_name
string source
string correlation_id
string previous_status
string current_status
string error_code
string error_message
string data_json
builtin_interfaces/Time stamp
```

Topic:

```text
/task_orchestrator/events
```

## Feedback

`msg/TaskFeedbackV1.msg`

```text
string api_version
string task_id
string task_name
string source
string correlation_id
float32 progress
string feedback_json
builtin_interfaces/Time stamp
```

Topic:

```text
/task_orchestrator/feedback
```

## Services

`srv/ListTasksV1.srv`

```text
bool include_system_tasks
---
TaskSpecV1[] tasks
```

`srv/GetTaskV1.srv`

```text
string task_id
---
bool found
TaskRecordV1 task
```

`srv/CancelTasksV1.srv`

```text
string[] task_ids
string source
string correlation_id
---
bool success
string[] canceled_task_ids
string[] failed_task_ids
string error_code
string error_message
```

`srv/ReloadConfigV1.srv`

```text
---
bool success
string error_code
string error_message
```

`srv/ListEventsV1.srv`

```text
string task_id
string task_name
string event_type
string current_status
string source
string correlation_id
uint32 limit
---
TaskEventV1[] events
```

`srv/ListTaskRecordsV1.srv`

```text
string task_name
string task_status
string source
string correlation_id
uint32 limit
---
TaskRecordV1[] records
```

## Configuration Example

Storage is disabled by default. Enabling SQLite requires only the Python
standard-library `sqlite3` module and a writable `storage.sqlite_path`.

```yaml
task_orchestrator:
  ros__parameters:
    api_version: "v1alpha1"
    enable_compatibility_aliases: false
    enable_debug_task_servers: false
    event_record_limit: 1000
    task_record_limit: 1000
    storage:
      enabled: false
      sqlite_path: ""
      retention_days: 30
    tasks:
      - "navigate_to_pose"
    navigate_to_pose:
      task_name: "navigation/navigate_to_pose"
      topic: "/navigate_to_pose"
      msg_interface: "nav2_msgs.action.NavigateToPose"
      blocking: true
      cancel_on_stop: true
      cancel_reported_as_success: false
      reentrant: false
      priority_default: 0
      cancel_timeout: 5.0
      resources: ["base", "map"]
      tags: ["navigation"]
```

## Versioning

- `v0.x`: API may change, but all changes must be documented.
- `v1.0`: public messages/actions/services become stable.
- Breaking changes after `v1.0` require a new versioned interface name.
