"""Flight admission and cancellation policy."""

from __future__ import annotations

from dataclasses import dataclass

from task_orchestrator_ardupilot_bridge.state import DroneTelemetrySnapshot


SAFE_CANCEL_MODES = {"BRAKE", "LAND", "LOITER", "RTL"}


@dataclass(frozen=True)
class SafetyPolicy:
    min_takeoff_battery_percent: float = 20.0
    require_localization: bool = True
    cancel_mode: str = "BRAKE"

    def __post_init__(self) -> None:
        normalized_mode = self.cancel_mode.upper()
        if normalized_mode not in SAFE_CANCEL_MODES:
            raise ValueError(
                f"cancel_mode must be one of {sorted(SAFE_CANCEL_MODES)}, got {self.cancel_mode}"
            )
        object.__setattr__(self, "cancel_mode", normalized_mode)

    def preflight_errors(self, state: DroneTelemetrySnapshot) -> tuple[str, ...]:
        errors: list[str] = []
        if not state.connected:
            errors.append("MAVLink heartbeat is unavailable")
        if state.failsafe:
            errors.append(f"autopilot is in failsafe state: {state.system_status}")
        if self.require_localization and not state.localization_ok:
            errors.append("EKF/GPS localization is not healthy")
        if state.battery_percent < 0:
            errors.append("battery percentage is unavailable")
        elif state.battery_percent < self.min_takeoff_battery_percent:
            errors.append(
                "battery is below takeoff threshold: "
                f"{state.battery_percent:.1f}% < {self.min_takeoff_battery_percent:.1f}%"
            )
        return tuple(errors)

    def navigation_errors(self, state: DroneTelemetrySnapshot) -> tuple[str, ...]:
        errors: list[str] = []
        if not state.connected:
            errors.append("MAVLink heartbeat is unavailable")
        if state.failsafe:
            errors.append(f"autopilot is in failsafe state: {state.system_status}")
        if self.require_localization and not state.localization_ok:
            errors.append("EKF/GPS localization is not healthy")
        if not state.armed:
            errors.append("vehicle is not armed")
        return tuple(errors)
