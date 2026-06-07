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
  introspection and events.
- `queue_on_conflict_default`: default queue behavior for this task when
  admission policy is blocked.
- `tags`: caller-visible labels copied into task records.

## Reload Configuration

```bash
ros2 service call /task_orchestrator/reload_config \
  task_orchestrator_msgs/srv/ReloadConfigV1 "{}"
```
