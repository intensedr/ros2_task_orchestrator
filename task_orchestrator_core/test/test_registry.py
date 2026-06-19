from task_orchestrator_core.registry import TaskRegistry
from task_orchestrator_core.task_models import TaskConfigError


def test_registry_includes_system_tasks_by_default():
    registry = TaskRegistry.with_system_tasks()

    tasks = registry.list(include_system_tasks=True)

    assert [task.task_name for task in tasks] == [
        "system/cancel_task",
        "system/mission",
        "system/stop",
        "system/wait",
    ]
    assert all(task.is_system_task for task in tasks)
    assert registry.list(include_system_tasks=False) == []


def test_registry_loads_yaml_task_config(tmp_path):
    config_path = tmp_path / "tasks.yaml"
    config_path.write_text(
        """
tasks:
  - task_name: nav2/go_to_pose
    topic: /navigate_to_pose
    msg_interface: nav2_msgs/action/NavigateToPose
    task_server_type: action
    blocking: true
    cancel_reported_as_success: true
    reentrant: false
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
    queue_on_conflict_default: true
    tags: [nav2, motion]
""",
        encoding="utf-8",
    )

    registry = TaskRegistry.from_yaml_file(config_path)

    task = registry.get("nav2/go_to_pose")
    assert task is not None
    assert task.topic == "/navigate_to_pose"
    assert task.blocking is True
    assert task.cancel_reported_as_success is True
    assert task.reentrant is False
    assert task.task_group == "navigation"
    assert task.capability_tags == ("localization", "motion")
    assert task.zone_locked is True
    assert task.min_battery_percent == 30.0
    assert task.allowed_robot_modes == ("AUTO", "MANUAL")
    assert task.requires_localization is True
    assert task.allow_emergency_stop is False
    assert task.pause_hook.configured is True
    assert task.pause_hook.task_server_type == "service"
    assert task.pause_hook.topic == "/pause_navigation"
    assert task.pause_hook.task_data_json == "{}"
    assert task.pause_hook.timeout_sec == 2.0
    assert task.resume_hook.configured is True
    assert task.resume_hook.task_server_type == "action"
    assert task.resume_hook.task_data_json == '{"order": 1}'
    assert task.resume_hook.timeout_sec == 3.0
    assert task.queue_on_conflict_default is True
    assert task.tags == ("nav2", "motion")


def test_registry_rejects_duplicate_task_names():
    config = {
        "tasks": [
            {"task_name": "example/task", "task_server_type": "action"},
            {"task_name": "example/task", "task_server_type": "service"},
        ]
    }

    try:
        TaskRegistry.from_config(config, include_system_tasks=False)
    except TaskConfigError as exc:
        assert "duplicate task_name" in str(exc)
    else:
        raise AssertionError("expected TaskConfigError")
