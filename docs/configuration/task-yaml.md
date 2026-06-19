# Task YAML

Tasks are declared in YAML and loaded through the `tasks_config_path` node
parameter. Each task maps an orchestrator `task_name` to an existing ROS2
service or action.

## Minimal Action Task

```yaml
tasks:
  - task_name: nav2/navigate_to_pose
    topic: /navigate_to_pose
    msg_interface: nav2_msgs/action/NavigateToPose
    task_server_type: action
    blocking: true
    cancel_on_stop: true
    cancel_reported_as_success: false
    reentrant: false
    priority_default: 0
    cancel_timeout: 5.0
    resources: [base, map]
    task_group: navigation
    capability_tags: [localization, motion]
    zone_locked: true
    admission:
      min_battery_percent: 30
      allowed_robot_modes: [AUTO, MANUAL]
      requires_localization: true
      allow_emergency_stop: false
    pause:
      task_server_type: service
      topic: /pause_navigation
      msg_interface: std_srvs/srv/Trigger
      task_data_json: {}
      timeout_sec: 2.0
    resume:
      task_server_type: action
      topic: /resume_navigation
      msg_interface: example_interfaces/action/Fibonacci
      task_data_json:
        order: 1
      timeout_sec: 3.0
    queue_on_conflict_default: false
    tags: [nav2, navigation, motion]
```

## Minimal Service Task

```yaml
tasks:
  - task_name: example/set_bool
    topic: /example/set_bool
    msg_interface: std_srvs/srv/SetBool
    task_server_type: service
    blocking: false
    cancel_on_stop: false
    reentrant: true
```

## Execution Flags

- `blocking`: prevents conflicting tasks from starting while the task is
  active.
- `cancel_on_stop`: allows `/task_orchestrator/stop` to cancel the task.
- `cancel_reported_as_success`: reports canceled action goals as successful
  completion.
- `reentrant`: allows multiple active instances of the same task name.
- `cancel_timeout`: timeout for action goal cancellation.
- `resources`: resource labels for conflict and observability metadata.
- `task_group`: optional single-group lock; active tasks in the same group
  conflict.
- `capability_tags`: robot capability labels exposed through task
  introspection and events. When `admission.available_capability_tags` is set,
  tasks requiring unavailable tags are rejected.
- `zone_locked`: prevents concurrent active zone-locked tasks with the same
  request `zone_id`.
- `admission.min_battery_percent`: minimum battery provider value required to
  admit the task.
- `admission.allowed_robot_modes`: allowed robot-mode provider values.
- `admission.requires_localization`: rejects the task when localization health is
  not OK.
- `admission.allow_emergency_stop`: allows the task to start while emergency stop
  is active; defaults to `false`.
- `pause` and `resume`: optional service/action hooks used by
  `/task_orchestrator/pause_tasks` and `/task_orchestrator/resume_tasks`.
  Each hook accepts `task_server_type`, `topic`, `msg_interface`,
  `task_data_json` and `timeout_sec`.
- `queue_on_conflict_default`: default queue behavior for this task when
  admission policy is blocked.
- `tags`: caller-visible labels copied into task records.

## Admission Provider Parameters

The core evaluates task requirements against these node parameters:

- `admission.battery_percent`
- `admission.robot_mode`
- `admission.localization_ok`
- `admission.emergency_stop_active`
- `admission.available_capability_tags`

External robot-specific code is responsible for updating those values from
actual battery, mode, localization and safety sources.

## Reload Configuration

```bash
ros2 service call /task_orchestrator/reload_config \
  task_orchestrator_msgs/srv/ReloadConfigV1 "{}"
```
