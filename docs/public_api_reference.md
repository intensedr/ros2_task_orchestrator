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

Task-related public messages carry this common metadata shape. Constants-only
messages such as `TaskStatusV1` and `ErrorCodeV1` are intentionally excluded:

- `api_version`: API version string, initially `"v1beta1"`.
- `task_id`: unique task execution ID.
- `task_name`: configured task type name.
- `source`: caller or system that requested the task.
- `priority`: higher value means higher priority.
- `correlation_id`: caller-provided ID for tracing a workflow.
- `created_at`, `started_at`, `finished_at`: ROS2 `builtin_interfaces/Time`.
- `status`: one of the `TaskStatusV1` constants.
- `error_code`: stable machine-readable error code.
- `error_message`: human-readable diagnostic text.
- `result_json`: JSON result payload.
- `duration_sec`: runtime duration from `started_at` to `finished_at`.
- `total_duration_sec`: total duration from `created_at` to `finished_at`.

Fleet-safe context fields are optional and default to empty strings:

- `robot_id`
- `fleet_id`
- `site_id`
- `zone_id`
- `operator_id`
- `tenant_id`
- `trace_id`
- `idempotency_key`
- `metadata_json`

Execute requests can also carry scheduling fields:

- `scheduled_at`: earliest start time.
- `delay_sec`: relative delay before a queued task can start.
- `deadline_at`: latest acceptable completion time.
- `timeout_sec`: execution timeout hint for compatible task backends.
- `queue_on_conflict`: queue instead of rejecting while admission policy is
  blocked.

## Status Values

`TaskStatusV1.msg`

```text
string RECEIVED=RECEIVED
string PENDING=PENDING
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
- `DEADLINE_EXCEEDED`
- `SERVER_UNAVAILABLE`
- `UNSUPPORTED`
- `INTERNAL_ERROR`

Failed terminal results keep `error_code` and `error_message` as top-level
fields. When `result_json` is a JSON object, it also includes a structured
error object:

```json
{
  "error": {
    "code": "UNKNOWN_TASK",
    "message": "Unknown task_name: missing/task",
    "details": {}
  },
  "error_code": "UNKNOWN_TASK",
  "error_message": "Unknown task_name: missing/task"
}
```

## Execute Task

`action/ExecuteTaskV1.action`

```text
string api_version
string task_id
string task_name
string source
int32 priority
string correlation_id
builtin_interfaces/Time created_at
builtin_interfaces/Time started_at
builtin_interfaces/Time finished_at
string status
string error_code
string error_message
string result_json
string task_data_json
string[] tags
builtin_interfaces/Time scheduled_at
float64 delay_sec
builtin_interfaces/Time deadline_at
float64 timeout_sec
bool queue_on_conflict
string idempotency_key
string metadata_json
string robot_id
string fleet_id
string site_id
string zone_id
string operator_id
string tenant_id
string trace_id
---
string api_version
string task_id
string task_name
string source
int32 priority
string correlation_id
builtin_interfaces/Time created_at
builtin_interfaces/Time started_at
builtin_interfaces/Time finished_at
string status
string error_code
string error_message
string result_json
float64 duration_sec
float64 total_duration_sec
string idempotency_key
string metadata_json
string robot_id
string fleet_id
string site_id
string zone_id
string operator_id
string tenant_id
string trace_id
---
string api_version
string task_id
string task_name
string source
int32 priority
string correlation_id
builtin_interfaces/Time created_at
builtin_interfaces/Time started_at
builtin_interfaces/Time finished_at
string status
string error_code
string error_message
string result_json
float32 progress
string feedback_json
float64 duration_sec
float64 total_duration_sec
string idempotency_key
string metadata_json
string robot_id
string fleet_id
string site_id
string zone_id
string operator_id
string tenant_id
string trace_id
builtin_interfaces/Time stamp
```

Semantics:

- Empty `task_id` is allowed and generates a UUID.
- Unknown `task_name` returns `REJECTED` with `UNKNOWN_TASK`.
- Invalid JSON returns `ERROR` with `TASK_DATA_PARSING_FAILED`.
- `queue_on_conflict`, `scheduled_at` and `delay_sec` move the request through
  `QUEUED` before `IN_PROGRESS`.
- `timeout_sec` and `deadline_at` map to `TASK_TIMEOUT` or
  `DEADLINE_EXCEEDED` when exceeded.
- Cancellation through the action server maps to task cancellation when the
  backing task supports cancellation.

### Built-In `system/mission` Payload

`system/mission` is submitted through `ExecuteTaskV1.task_data_json`.

```json
{
  "mission_id": "mission-1",
  "subtasks": [
    {
      "subtask_id": "inspect",
      "task_id": "mission-1/inspect",
      "task_name": "system/wait",
      "task_data_json": {"duration_sec": 0},
      "depends_on": [],
      "condition": {"action": "continue"},
      "allow_skipping": false,
      "max_attempts": 3,
      "retry_backoff_sec": 1.0,
      "retry_backoff_type": "exponential",
      "retry_max_backoff_sec": 10.0,
      "retry_error_codes": ["TASK_TIMEOUT"],
      "retry_policy": {
        "max_attempts": 3,
        "backoff_sec": 1.0,
        "backoff_type": "exponential",
        "max_backoff_sec": 10.0,
        "error_codes": ["TASK_TIMEOUT"]
      },
      "timeout_sec": 5.0
    }
  ]
}
```

Mission semantics:

- `depends_on` forms a directed acyclic graph; duplicate IDs, unknown
  dependencies and cycles are rejected before execution.
- Ready subtasks are grouped into deterministic graph waves and executed in
  mission payload order inside the mission callback.
- `condition` and `condition_json` accept `continue`, `skip`, `retry` and
  `abort`; `condition_json` is the serialized ROS message field.
- `retry_policy` overrides top-level `max_attempts`, `retry_backoff_sec`,
  `retry_backoff_type`, `retry_max_backoff_sec` and `retry_error_codes` when
  provided.
- Mission `result_json` includes `mission_results` with subtask status,
  attempts, skipped state and error details.

## Active Tasks

`msg/ActiveTaskV1.msg`

```text
string api_version
string task_id
string task_name
string source
int32 priority
string correlation_id
builtin_interfaces/Time created_at
builtin_interfaces/Time started_at
builtin_interfaces/Time finished_at
string status
string error_code
string error_message
string result_json
string[] tags
string robot_id
string fleet_id
string site_id
string zone_id
string operator_id
string tenant_id
string trace_id
string metadata_json
string idempotency_key
```

`msg/ActiveTaskArrayV1.msg`

```text
string api_version
string task_id
string task_name
string source
int32 priority
string correlation_id
builtin_interfaces/Time created_at
builtin_interfaces/Time started_at
builtin_interfaces/Time finished_at
string status
string error_code
string error_message
string result_json
string robot_id
string fleet_id
string site_id
string zone_id
string operator_id
string tenant_id
string trace_id
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
int32 priority
string correlation_id
builtin_interfaces/Time created_at
builtin_interfaces/Time started_at
builtin_interfaces/Time finished_at
string status
string error_code
string error_message
string result_json
float64 duration_sec
float64 total_duration_sec
string idempotency_key
string metadata_json
string robot_id
string fleet_id
string site_id
string zone_id
string operator_id
string tenant_id
string trace_id
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
int32 priority
string correlation_id
builtin_interfaces/Time created_at
builtin_interfaces/Time started_at
builtin_interfaces/Time finished_at
string status
string error_code
string error_message
string result_json
string previous_status
string data_json
float64 duration_sec
float64 total_duration_sec
string idempotency_key
string robot_id
string fleet_id
string site_id
string zone_id
string operator_id
string tenant_id
string trace_id
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
int32 priority
string correlation_id
builtin_interfaces/Time created_at
builtin_interfaces/Time started_at
builtin_interfaces/Time finished_at
string status
string error_code
string error_message
string result_json
float32 progress
string feedback_json
float64 duration_sec
float64 total_duration_sec
string idempotency_key
string robot_id
string fleet_id
string site_id
string zone_id
string operator_id
string tenant_id
string trace_id
builtin_interfaces/Time stamp
```

Topic:

```text
/task_orchestrator/feedback
```

## Task Records

`msg/TaskRecordV1.msg`

Task records embed the terminal or current `TaskResultV1`, the original
`task_data_json`, caller tags and scheduling fields needed for queued-task
recovery.

```text
string api_version
string task_id
string task_name
string source
int32 priority
string correlation_id
builtin_interfaces/Time created_at
builtin_interfaces/Time started_at
builtin_interfaces/Time finished_at
string status
string error_code
string error_message
string result_json
float64 duration_sec
float64 total_duration_sec
TaskResultV1 result
bool active
string task_data_json
string[] tags
builtin_interfaces/Time scheduled_at
float64 delay_sec
builtin_interfaces/Time deadline_at
float64 timeout_sec
bool queue_on_conflict
string idempotency_key
string metadata_json
string robot_id
string fleet_id
string site_id
string zone_id
string operator_id
string tenant_id
string trace_id
```

## Services

Primary public services:

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

`srv/PauseTasksV1.srv`

```text
string[] task_ids
string source
string correlation_id
---
bool success
string[] paused_task_ids
string[] failed_task_ids
string error_code
string error_message
```

`srv/ResumeTasksV1.srv`

```text
string[] task_ids
string source
string correlation_id
---
bool success
string[] resumed_task_ids
string[] failed_task_ids
string error_code
string error_message
```

`srv/ValidateTaskV1.srv`

```text
string task_id
string task_name
string task_data_json
bool include_schema
---
bool valid
string error_code
string error_message
string normalized_task_data_json
string schema_json
```

Semantics:

- Validates task existence and task payload without starting the task.
- Resolves mission templates before validation.
- Uses the same JSON-to-ROS2 conversion path as execution for action and
  service-backed tasks.
- `schema_json` is a best-effort JSON Schema for the configured payload shape
  when `include_schema` is true.

Additional V1 query/admin services:

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
string status
string source
string correlation_id
string robot_id
string fleet_id
string site_id
string zone_id
string operator_id
string tenant_id
string trace_id
string idempotency_key
uint32 limit
---
TaskEventV1[] events
```

`srv/ListTaskRecordsV1.srv`

```text
string task_name
string status
string source
string correlation_id
string robot_id
string fleet_id
string site_id
string zone_id
string operator_id
string tenant_id
string trace_id
string idempotency_key
uint32 limit
---
TaskRecordV1[] records
```

Both history services return newest records first and support filtering by
task identity, lifecycle fields and fleet-safe context fields. These filters
work against both bounded in-memory history and SQLite-backed history.

## Configuration Example

Storage is disabled by default. Enabling SQLite requires only the Python
standard-library `sqlite3` module and a writable `storage.sqlite_path`.

```yaml
task_orchestrator:
  ros__parameters:
    api_version: "v1beta1"
    enable_compatibility_aliases: false
    enable_debug_task_servers: false
    event_record_limit: 1000
    task_record_limit: 1000
    mission_templates_path: ""
    queue:
      max_size: 100
      poll_interval_sec: 0.05
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
      task_group: "navigation"
      capability_tags: ["localization", "motion"]
      queue_on_conflict_default: false
      tags: ["navigation"]
```

## Versioning

- `v0.x`: API may change, but all changes must be documented.
- `v1.0`: public messages/actions/services become stable.
- Breaking changes after `v1.0` require a new versioned interface name.
