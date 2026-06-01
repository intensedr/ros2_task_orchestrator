from task_orchestrator_core.active_tasks import ActiveTaskEntry, ActiveTaskRegistry, DuplicateActiveTaskError


def _entry(task_id: str) -> ActiveTaskEntry:
    return ActiveTaskEntry(
        api_version="v1alpha1",
        task_id=task_id,
        task_name="system/wait",
        source="test",
        correlation_id="corr-1",
        priority=0,
        task_status="IN_PROGRESS",
        created_at="created",
        started_at="started",
        tags=("system",),
    )


def test_active_task_registry_adds_lists_and_removes_tasks():
    registry = ActiveTaskRegistry()

    registry.add(_entry("task-1"))

    assert len(registry) == 1
    assert registry.get("task-1") is not None
    assert [task.task_id for task in registry.list()] == ["task-1"]
    assert registry.remove("task-1").task_id == "task-1"
    assert len(registry) == 0


def test_active_task_registry_rejects_duplicate_ids():
    registry = ActiveTaskRegistry()
    registry.add(_entry("task-1"))

    try:
        registry.add(_entry("task-1"))
    except DuplicateActiveTaskError as exc:
        assert str(exc) == "task-1"
    else:
        raise AssertionError("expected DuplicateActiveTaskError")
