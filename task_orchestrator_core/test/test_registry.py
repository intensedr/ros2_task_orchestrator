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
