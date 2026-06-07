# Nav2 Example

The examples package includes a Nav2 action-backed task definition.

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
    tags: [nav2, navigation, motion]
```

Launch the orchestrator with the Nav2 example config:

```bash
ros2 launch task_orchestrator_examples nav2_orchestrator.launch.py
```

The examples package also includes
`task_orchestrator_examples/missions/nav2_wait_mission_template.yaml`, which can
be referenced from a mission request with `template_path` or copied into a
directory configured by `mission_templates_path` and referenced by
`template_id`.
