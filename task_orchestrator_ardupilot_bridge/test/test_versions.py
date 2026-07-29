from task_orchestrator_ardupilot_bridge.versions import (
    LOCKED_PYMAVLINK_VERSION,
    _numeric_prefix,
    require_supported_pymavlink,
)


def test_locked_pymavlink_version_is_supported():
    assert LOCKED_PYMAVLINK_VERSION == "2.4.49"
    assert require_supported_pymavlink().startswith("2.4.")


def test_numeric_prefix_handles_release_suffix():
    assert _numeric_prefix("2.4.49.post1") == (2, 4, 49)
