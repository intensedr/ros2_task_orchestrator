from task_orchestrator_core.active_tasks import ActiveTaskEntry
from task_orchestrator_core.policy import AdmissionSnapshot, TaskPolicyEngine
from task_orchestrator_core.task_models import TaskDefinition
from task_orchestrator_msgs.msg import ErrorCodeV1, TaskStatusV1


def _active_task(task_id="active-task", **kwargs):
    data = {
        "api_version": "v1beta1",
        "task_id": task_id,
        "task_name": "example/active",
        "source": "test",
        "correlation_id": "corr-1",
        "priority": 0,
        "status": TaskStatusV1.IN_PROGRESS,
        "created_at": None,
        "started_at": None,
        "tags": (),
    }
    data.update(kwargs)
    return ActiveTaskEntry(**data)


def test_policy_engine_rejects_zone_lock_conflict():
    engine = TaskPolicyEngine()
    task = TaskDefinition(task_name="example/zone-task", zone_locked=True)
    active = _active_task(zone_locked=True, zone_id="zone-1")

    decision = engine.evaluate_start(
        task=task,
        active_tasks=[active],
        admission=AdmissionSnapshot(),
        request_zone_id="zone-1",
    )

    assert decision.allowed is False
    assert decision.error_code == ErrorCodeV1.RESOURCE_CONFLICT
    assert "zone-1" in decision.error_message


def test_policy_engine_rejects_admission_provider_failures():
    engine = TaskPolicyEngine()

    battery_decision = engine.evaluate_start(
        task=TaskDefinition(task_name="example/battery", min_battery_percent=30.0),
        active_tasks=[],
        admission=AdmissionSnapshot(battery_percent=20.0),
    )
    mode_decision = engine.evaluate_start(
        task=TaskDefinition(task_name="example/mode", allowed_robot_modes=("AUTO",)),
        active_tasks=[],
        admission=AdmissionSnapshot(robot_mode="MANUAL"),
    )
    localization_decision = engine.evaluate_start(
        task=TaskDefinition(task_name="example/localization", requires_localization=True),
        active_tasks=[],
        admission=AdmissionSnapshot(localization_ok=False),
    )
    estop_decision = engine.evaluate_start(
        task=TaskDefinition(task_name="example/estop"),
        active_tasks=[],
        admission=AdmissionSnapshot(emergency_stop_active=True),
    )
    capability_decision = engine.evaluate_start(
        task=TaskDefinition(task_name="example/capability", capability_tags=("navigation", "motion")),
        active_tasks=[],
        admission=AdmissionSnapshot(available_capability_tags=("navigation",)),
    )

    for decision in (
        battery_decision,
        mode_decision,
        localization_decision,
        estop_decision,
        capability_decision,
    ):
        assert decision.allowed is False
        assert decision.error_code == ErrorCodeV1.POLICY_REJECTED
