import math

from pymavlink import mavutil

from task_orchestrator_ardupilot_bridge.connection import MavlinkConnection


class FakeMessage:
    def __init__(self, message_type, source_system=1, source_component=1, **fields):
        self._message_type = message_type
        self._source_system = source_system
        self._source_component = source_component
        for name, value in fields.items():
            setattr(self, name, value)

    def get_type(self):
        return self._message_type

    def get_srcSystem(self):
        return self._source_system

    def get_srcComponent(self):
        return self._source_component


def test_connection_converts_ned_pose_to_ros_enu():
    connection = MavlinkConnection("udp:127.0.0.1:14550")
    connection._master = object()
    connection._target_system = 1
    connection._handle_message(FakeMessage("LOCAL_POSITION_NED", x=3.0, y=4.0, z=-5.0))
    connection._handle_message(FakeMessage("ATTITUDE", yaw=0.0))

    state = connection.snapshot()

    assert state.pose_valid is True
    assert state.x_enu == 4.0
    assert state.y_enu == 3.0
    assert state.z_enu == 5.0
    assert math.isclose(state.yaw_enu, math.pi / 2.0)


def test_connection_uses_ekf_flags_for_localization_health():
    connection = MavlinkConnection("udp:127.0.0.1:14550")
    connection._master = object()
    connection._handle_message(
        FakeMessage(
            "HEARTBEAT",
            type=mavutil.mavlink.MAV_TYPE_QUADROTOR,
            autopilot=mavutil.mavlink.MAV_AUTOPILOT_ARDUPILOTMEGA,
            base_mode=0,
            custom_mode=0,
            system_status=mavutil.mavlink.MAV_STATE_STANDBY,
        )
    )
    healthy_flags = (
        mavutil.mavlink.EKF_ATTITUDE
        | mavutil.mavlink.EKF_POS_HORIZ_REL
        | mavutil.mavlink.EKF_POS_VERT_ABS
    )

    connection._handle_message(FakeMessage("EKF_STATUS_REPORT", flags=healthy_flags))
    assert connection.snapshot().localization_ok is True

    connection._handle_message(FakeMessage("EKF_STATUS_REPORT", flags=mavutil.mavlink.EKF_ATTITUDE))
    assert connection.snapshot().localization_ok is False


def test_connection_computes_distance_to_home():
    connection = MavlinkConnection("udp:127.0.0.1:14550")
    connection._master = object()
    connection._target_system = 1
    connection._handle_message(
        FakeMessage("HOME_POSITION", latitude=int(43.0 * 1e7), longitude=int(76.0 * 1e7))
    )
    connection._handle_message(
        FakeMessage(
            "GLOBAL_POSITION_INT",
            lat=int(43.0001 * 1e7),
            lon=int(76.0 * 1e7),
            relative_alt=5000,
        )
    )

    state = connection.snapshot()

    assert 10.0 < state.distance_to_home < 12.5
    assert state.relative_altitude == 5.0
