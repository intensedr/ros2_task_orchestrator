from task_orchestrator_ardupilot_bridge.safety import SafetyPolicy
from task_orchestrator_ardupilot_bridge.state import DroneTelemetrySnapshot


def test_preflight_is_fail_closed_without_telemetry():
    errors = SafetyPolicy().preflight_errors(DroneTelemetrySnapshot())

    assert "MAVLink heartbeat is unavailable" in errors
    assert "EKF/GPS localization is not healthy" in errors
    assert "battery percentage is unavailable" in errors


def test_preflight_accepts_healthy_vehicle():
    state = DroneTelemetrySnapshot(
        connected=True,
        localization_ok=True,
        battery_percent=75.0,
        landed=True,
    )

    assert SafetyPolicy().preflight_errors(state) == ()


def test_navigation_requires_armed_vehicle():
    state = DroneTelemetrySnapshot(
        connected=True,
        localization_ok=True,
        battery_percent=75.0,
    )

    assert SafetyPolicy().navigation_errors(state) == ("vehicle is not armed",)


def test_default_cancel_mode_is_brake_for_autonomous_flight():
    assert SafetyPolicy().cancel_mode == "BRAKE"
