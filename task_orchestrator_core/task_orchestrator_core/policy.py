"""Admission and active-task policy checks."""

from __future__ import annotations

from dataclasses import dataclass, field

from task_orchestrator_core.active_tasks import ActiveTaskEntry
from task_orchestrator_core.task_models import TaskDefinition
from task_orchestrator_msgs.msg import ErrorCodeV1


@dataclass(frozen=True)
class AdmissionSnapshot:
    """Current admission provider state."""

    battery_percent: float = 100.0
    robot_mode: str = ""
    localization_ok: bool = True
    emergency_stop_active: bool = False
    available_capability_tags: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class PolicyDecision:
    """Start-policy decision with stable public error metadata."""

    allowed: bool = True
    error_code: str = ""
    error_message: str = ""


class TaskPolicyEngine:
    """Evaluates task admission without owning product-specific providers."""

    def evaluate_start(
        self,
        task: TaskDefinition,
        active_tasks: list[ActiveTaskEntry],
        admission: AdmissionSnapshot,
        request_zone_id: str = "",
        ignored_active_task_ids: set[str] | None = None,
        bypass_admission: bool = False,
    ) -> PolicyDecision:
        ignored_active_ids = set(ignored_active_task_ids or set())

        if not bypass_admission:
            admission_error = self._admission_error(task, admission)
            if admission_error:
                return PolicyDecision(
                    allowed=False,
                    error_code=ErrorCodeV1.POLICY_REJECTED,
                    error_message=admission_error,
                )

        if not task.reentrant:
            for active_task in active_tasks:
                if active_task.task_name == task.task_name and active_task.task_id not in ignored_active_ids:
                    return PolicyDecision(
                        allowed=False,
                        error_code=ErrorCodeV1.RESOURCE_CONFLICT,
                        error_message=f"Task {task.task_name} is already active and cannot be replaced.",
                    )

        blocking_tasks = [
            active_task
            for active_task in active_tasks
            if active_task.blocking and active_task.task_id not in ignored_active_ids
        ]
        if blocking_tasks:
            blocking_task_ids = ", ".join(task.task_id for task in blocking_tasks)
            return PolicyDecision(
                allowed=False,
                error_code=ErrorCodeV1.RESOURCE_CONFLICT,
                error_message=f"Active blocking task prevents starting {task.task_name}: {blocking_task_ids}",
            )

        requested_resources = set(task.resources)
        if requested_resources:
            for active_task in active_tasks:
                if active_task.task_id in ignored_active_ids:
                    continue
                shared_resources = requested_resources.intersection(active_task.resources)
                if shared_resources:
                    return PolicyDecision(
                        allowed=False,
                        error_code=ErrorCodeV1.RESOURCE_CONFLICT,
                        error_message=(
                            f"Task {task.task_name} conflicts with active task {active_task.task_id} "
                            f"on resources: {', '.join(sorted(shared_resources))}"
                        ),
                    )

        if task.task_group:
            for active_task in active_tasks:
                if active_task.task_id in ignored_active_ids:
                    continue
                if active_task.task_group == task.task_group:
                    return PolicyDecision(
                        allowed=False,
                        error_code=ErrorCodeV1.RESOURCE_CONFLICT,
                        error_message=(
                            f"Task {task.task_name} conflicts with active task {active_task.task_id} "
                            f"in task group: {task.task_group}"
                        ),
                    )

        if task.zone_locked and request_zone_id:
            for active_task in active_tasks:
                if active_task.task_id in ignored_active_ids:
                    continue
                if active_task.zone_locked and active_task.zone_id == request_zone_id:
                    return PolicyDecision(
                        allowed=False,
                        error_code=ErrorCodeV1.RESOURCE_CONFLICT,
                        error_message=(
                            f"Task {task.task_name} conflicts with active task {active_task.task_id} "
                            f"in zone: {request_zone_id}"
                        ),
                    )

        return PolicyDecision()

    def _admission_error(self, task: TaskDefinition, admission: AdmissionSnapshot) -> str:
        if admission.emergency_stop_active and not task.allow_emergency_stop:
            return f"Emergency stop is active; task {task.task_name} is not admitted."
        if task.min_battery_percent > 0 and admission.battery_percent < task.min_battery_percent:
            return (
                f"Battery level {admission.battery_percent:g}% is below required "
                f"{task.min_battery_percent:g}% for task {task.task_name}."
            )
        if task.allowed_robot_modes and admission.robot_mode not in task.allowed_robot_modes:
            allowed_modes = ", ".join(task.allowed_robot_modes)
            return (
                f"Robot mode {admission.robot_mode or '<unset>'} is not allowed "
                f"for {task.task_name}: {allowed_modes}"
            )
        if task.requires_localization and not admission.localization_ok:
            return f"Localization is not healthy for task {task.task_name}."
        if admission.available_capability_tags:
            missing_tags = sorted(set(task.capability_tags) - set(admission.available_capability_tags))
            if missing_tags:
                return f"Task {task.task_name} requires unavailable capabilities: {', '.join(missing_tags)}"
        return ""
