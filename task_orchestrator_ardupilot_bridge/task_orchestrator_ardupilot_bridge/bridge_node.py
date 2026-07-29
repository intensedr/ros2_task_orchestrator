"""ROS2 action server backed by ArduPilot MAVLink commands and telemetry."""

from __future__ import annotations

import math
import threading
import time
from typing import Any

from geometry_msgs.msg import PoseStamped, TransformStamped
import rclpy
from rclpy.action import ActionServer, CancelResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.parameter import Parameter
from rclpy.parameter_client import AsyncParameterClient
from sensor_msgs.msg import BatteryState
from tf2_ros import TransformBroadcaster

from task_orchestrator_ardupilot_bridge.connection import MavlinkConnection
from task_orchestrator_ardupilot_bridge.safety import SafetyPolicy
from task_orchestrator_ardupilot_bridge.state import DroneTelemetrySnapshot
from task_orchestrator_ardupilot_bridge.versions import require_supported_pymavlink
from task_orchestrator_drone_msgs.action import (
    Arm,
    GoToWaypoint,
    Hover,
    Land,
    ReturnToLaunch,
    SetFlightMode,
    Takeoff,
)
from task_orchestrator_drone_msgs.msg import DroneStateV1


class ArduPilotBridgeNode(Node):
    """Maps backend-neutral drone actions to ArduPilot state machines."""

    def __init__(self) -> None:
        super().__init__("ardupilot_bridge")
        self._declare_parameters()

        pymavlink_version = require_supported_pymavlink()
        self.get_logger().info(f"Using pymavlink {pymavlink_version}")

        self._frame_id = str(self.get_parameter("frame_id").value)
        self._base_frame_id = str(self.get_parameter("base_frame_id").value)
        self._command_timeout_sec = float(self.get_parameter("command_timeout_sec").value)
        self._command_retries = int(self.get_parameter("command_retries").value)
        self._position_tolerance_m = float(self.get_parameter("position_tolerance_m").value)
        self._altitude_tolerance_m = float(self.get_parameter("altitude_tolerance_m").value)
        self._takeoff_timeout_sec = float(self.get_parameter("takeoff_timeout_sec").value)
        self._waypoint_timeout_sec = float(self.get_parameter("waypoint_timeout_sec").value)
        self._land_timeout_sec = float(self.get_parameter("land_timeout_sec").value)
        self._rtl_timeout_sec = float(self.get_parameter("rtl_timeout_sec").value)
        self._default_hover_sec = float(self.get_parameter("default_hover_sec").value)
        update_rate_hz = max(1.0, float(self.get_parameter("action_update_rate_hz").value))
        self._action_period_sec = 1.0 / update_rate_hz
        self._setpoint_period_sec = 1.0 / max(
            1.0,
            float(self.get_parameter("setpoint_rate_hz").value),
        )

        self._guided_mode = str(self.get_parameter("guided_mode").value).upper()
        self._hover_mode = str(self.get_parameter("hover_mode").value).upper()
        self._land_mode = str(self.get_parameter("land_mode").value).upper()
        self._rtl_mode = str(self.get_parameter("rtl_mode").value).upper()
        self._safety = SafetyPolicy(
            min_takeoff_battery_percent=float(
                self.get_parameter("min_takeoff_battery_percent").value
            ),
            require_localization=bool(self.get_parameter("require_localization").value),
            cancel_mode=str(self.get_parameter("cancel_mode").value),
        )

        self._callback_group = ReentrantCallbackGroup()
        self._flight_lock = threading.Lock()
        self._connection = MavlinkConnection(
            connection_url=str(self.get_parameter("connection_url").value),
            source_system=int(self.get_parameter("source_system").value),
            source_component=int(self.get_parameter("source_component").value),
            heartbeat_timeout_sec=float(self.get_parameter("heartbeat_timeout_sec").value),
            reconnect_delay_sec=float(self.get_parameter("reconnect_delay_sec").value),
            logger=lambda message: self.get_logger().info(message),
        )

        self._pose_pub = self.create_publisher(PoseStamped, "/drone/pose", 10)
        self._battery_pub = self.create_publisher(BatteryState, "/drone/battery", 10)
        self._state_pub = self.create_publisher(DroneStateV1, "/drone/state", 10)
        self._tf_broadcaster = TransformBroadcaster(self)
        self._parameter_client = AsyncParameterClient(
            self,
            str(self.get_parameter("orchestrator_node_name").value),
            callback_group=self._callback_group,
        )
        self._last_admission_values: tuple[Any, ...] | None = None
        self._admission_future: Any | None = None

        self._action_servers = [
            ActionServer(
                self,
                Arm,
                "/drone/arm",
                execute_callback=self._execute_arm,
                cancel_callback=self._cancel_callback,
                callback_group=self._callback_group,
            ),
            ActionServer(
                self,
                Takeoff,
                "/drone/takeoff",
                execute_callback=self._execute_takeoff,
                cancel_callback=self._cancel_callback,
                callback_group=self._callback_group,
            ),
            ActionServer(
                self,
                GoToWaypoint,
                "/drone/go_to_waypoint",
                execute_callback=self._execute_waypoint,
                cancel_callback=self._cancel_callback,
                callback_group=self._callback_group,
            ),
            ActionServer(
                self,
                Hover,
                "/drone/hover",
                execute_callback=self._execute_hover,
                cancel_callback=self._cancel_callback,
                callback_group=self._callback_group,
            ),
            ActionServer(
                self,
                Land,
                "/drone/land",
                execute_callback=self._execute_land,
                cancel_callback=self._cancel_callback,
                callback_group=self._callback_group,
            ),
            ActionServer(
                self,
                ReturnToLaunch,
                "/drone/rtl",
                execute_callback=self._execute_rtl,
                cancel_callback=self._cancel_callback,
                callback_group=self._callback_group,
            ),
            ActionServer(
                self,
                SetFlightMode,
                "/drone/set_mode",
                execute_callback=self._execute_set_mode,
                cancel_callback=self._cancel_callback,
                callback_group=self._callback_group,
            ),
        ]

        telemetry_rate_hz = max(1.0, float(self.get_parameter("telemetry_rate_hz").value))
        self.create_timer(1.0 / telemetry_rate_hz, self._publish_telemetry)
        self.create_timer(1.0, self._update_orchestrator_admission)
        self._connection.start()

    def destroy_node(self) -> bool:
        self._connection.close()
        for server in self._action_servers:
            server.destroy()
        return super().destroy_node()

    def _declare_parameters(self) -> None:
        self.declare_parameter("connection_url", "tcp:127.0.0.1:5760")
        self.declare_parameter("source_system", 245)
        self.declare_parameter("source_component", 191)
        self.declare_parameter("frame_id", "map")
        self.declare_parameter("base_frame_id", "base_link")
        self.declare_parameter("heartbeat_timeout_sec", 3.0)
        self.declare_parameter("reconnect_delay_sec", 1.0)
        self.declare_parameter("command_timeout_sec", 3.0)
        self.declare_parameter("command_retries", 2)
        self.declare_parameter("telemetry_rate_hz", 10.0)
        self.declare_parameter("action_update_rate_hz", 10.0)
        self.declare_parameter("setpoint_rate_hz", 3.0)
        self.declare_parameter("position_tolerance_m", 0.75)
        self.declare_parameter("altitude_tolerance_m", 0.35)
        self.declare_parameter("takeoff_timeout_sec", 45.0)
        self.declare_parameter("waypoint_timeout_sec", 120.0)
        self.declare_parameter("land_timeout_sec", 90.0)
        self.declare_parameter("rtl_timeout_sec", 180.0)
        self.declare_parameter("default_hover_sec", 5.0)
        self.declare_parameter("min_takeoff_battery_percent", 20.0)
        self.declare_parameter("require_localization", True)
        self.declare_parameter("guided_mode", "GUIDED")
        self.declare_parameter("hover_mode", "GUIDED")
        self.declare_parameter("land_mode", "LAND")
        self.declare_parameter("rtl_mode", "RTL")
        self.declare_parameter("cancel_mode", "BRAKE")
        self.declare_parameter("update_orchestrator_admission", True)
        self.declare_parameter("orchestrator_node_name", "/task_orchestrator")

    def _cancel_callback(self, _goal_handle: Any) -> CancelResponse:
        return CancelResponse.ACCEPT

    def _execute_arm(self, goal_handle: Any) -> Arm.Result:
        result = Arm.Result()
        if not self._acquire_flight(goal_handle, result):
            return result
        try:
            request = goal_handle.request
            state = self._connection.snapshot()
            if bool(request.arm):
                errors = self._safety.preflight_errors(state)
                if errors:
                    return self._abort(goal_handle, result, "; ".join(errors))
            elif not state.landed and not bool(request.force):
                return self._abort(goal_handle, result, "refusing to disarm while vehicle is airborne")

            command = self._connection.arm(
                bool(request.arm),
                bool(request.force),
                self._command_timeout_sec,
                self._command_retries,
            )
            if not command.accepted:
                return self._abort(goal_handle, result, f"arm command rejected: {command.message}")
            desired_armed = bool(request.arm)
            confirmed = self._connection.wait_until(
                lambda current: current.connected and current.armed == desired_armed,
                self._command_timeout_sec * 2.0,
            )
            if not confirmed:
                return self._abort(goal_handle, result, "armed state was not confirmed")
            feedback = Arm.Feedback()
            feedback.armed = desired_armed
            feedback.state = "armed" if desired_armed else "disarmed"
            goal_handle.publish_feedback(feedback)
            result.success = True
            result.armed = desired_armed
            goal_handle.succeed()
            return result
        finally:
            self._flight_lock.release()

    def _execute_takeoff(self, goal_handle: Any) -> Takeoff.Result:
        result = Takeoff.Result()
        if not self._acquire_flight(goal_handle, result):
            return result
        try:
            request = goal_handle.request
            target_altitude = float(request.target_altitude)
            if target_altitude <= 0.0:
                return self._abort(goal_handle, result, "target_altitude must be positive")
            if request.frame_id and request.frame_id not in {self._frame_id, "map"}:
                return self._abort(goal_handle, result, f"unsupported frame_id: {request.frame_id}")
            errors = self._safety.preflight_errors(self._connection.snapshot())
            if errors:
                return self._abort(goal_handle, result, "; ".join(errors))
            mode_error = self._set_mode(self._guided_mode)
            if mode_error:
                return self._abort(goal_handle, result, mode_error)
            if not self._connection.snapshot().armed:
                arm_result = self._connection.arm(
                    True,
                    False,
                    self._command_timeout_sec,
                    self._command_retries,
                )
                if not arm_result.accepted:
                    return self._abort(goal_handle, result, f"arm rejected: {arm_result.message}")
                if not self._connection.wait_until(lambda state: state.armed, self._command_timeout_sec * 2.0):
                    return self._abort(goal_handle, result, "armed state was not confirmed")
            takeoff = self._connection.takeoff(
                target_altitude,
                self._command_timeout_sec,
                self._command_retries,
            )
            if not takeoff.accepted:
                return self._abort(goal_handle, result, f"takeoff rejected: {takeoff.message}")

            deadline = time.monotonic() + max(
                self._takeoff_timeout_sec,
                float(request.duration_sec),
            )
            while rclpy.ok() and time.monotonic() < deadline:
                state = self._connection.snapshot()
                result.final_altitude = state.relative_altitude
                canceled = self._cancel_if_requested(goal_handle, result)
                if canceled is not None:
                    return canceled
                if not state.connected:
                    return self._abort(goal_handle, result, "MAVLink heartbeat lost during takeoff")
                feedback = Takeoff.Feedback()
                feedback.current_altitude = state.relative_altitude
                feedback.progress = min(1.0, max(0.0, state.relative_altitude / target_altitude))
                feedback.state = "takeoff"
                goal_handle.publish_feedback(feedback)
                if state.relative_altitude >= target_altitude - self._altitude_tolerance_m:
                    result.success = True
                    goal_handle.succeed()
                    return result
                time.sleep(self._action_period_sec)
            return self._abort(goal_handle, result, "takeoff timed out")
        finally:
            self._flight_lock.release()

    def _execute_waypoint(self, goal_handle: Any) -> GoToWaypoint.Result:
        result = GoToWaypoint.Result()
        if not self._acquire_flight(goal_handle, result):
            return result
        try:
            request = goal_handle.request
            target_pose = request.target_pose
            if target_pose.header.frame_id and target_pose.header.frame_id not in {
                self._frame_id,
                "map",
                "odom",
            }:
                return self._abort(
                    goal_handle,
                    result,
                    f"unsupported frame_id: {target_pose.header.frame_id}",
                )
            errors = self._safety.navigation_errors(self._connection.snapshot())
            if errors:
                return self._abort(goal_handle, result, "; ".join(errors))
            mode_error = self._set_mode(self._guided_mode)
            if mode_error:
                return self._abort(goal_handle, result, mode_error)

            speed_mps = float(request.speed_mps)
            if speed_mps > 0.0:
                speed_result = self._connection.change_speed(
                    speed_mps,
                    self._command_timeout_sec,
                    self._command_retries,
                )
                if not speed_result.accepted:
                    self.get_logger().warning(f"Speed command was not accepted: {speed_result.message}")

            target_x = float(target_pose.pose.position.x)
            target_y = float(target_pose.pose.position.y)
            target_z = float(target_pose.pose.position.z)
            yaw = _yaw_from_quaternion(target_pose.pose.orientation, self._connection.snapshot().yaw_enu)
            initial_distance = max(
                self._position_tolerance_m,
                _distance_to_target(self._connection.snapshot(), target_x, target_y, target_z),
            )
            deadline = time.monotonic() + self._waypoint_timeout_sec
            next_setpoint_at = 0.0
            while rclpy.ok() and time.monotonic() < deadline:
                state = self._connection.snapshot()
                result.final_pose = self._pose_message(state)
                canceled = self._cancel_if_requested(goal_handle, result)
                if canceled is not None:
                    return canceled
                if not state.connected:
                    return self._abort(goal_handle, result, "MAVLink heartbeat lost during navigation")
                if not state.localization_ok:
                    return self._abort(goal_handle, result, "localization became unhealthy during navigation")
                now = time.monotonic()
                if now >= next_setpoint_at:
                    self._connection.send_local_position_target(target_x, target_y, target_z, yaw)
                    next_setpoint_at = now + self._setpoint_period_sec
                distance = _distance_to_target(state, target_x, target_y, target_z)
                feedback = GoToWaypoint.Feedback()
                feedback.current_pose = result.final_pose
                feedback.distance_remaining = distance
                feedback.progress = min(1.0, max(0.0, 1.0 - distance / initial_distance))
                feedback.state = "go_to_waypoint"
                goal_handle.publish_feedback(feedback)
                if state.pose_valid and distance <= self._position_tolerance_m:
                    result.success = True
                    goal_handle.succeed()
                    return result
                time.sleep(self._action_period_sec)
            return self._abort(goal_handle, result, "waypoint navigation timed out")
        finally:
            self._flight_lock.release()

    def _execute_hover(self, goal_handle: Any) -> Hover.Result:
        result = Hover.Result()
        if not self._acquire_flight(goal_handle, result):
            return result
        try:
            errors = self._safety.navigation_errors(self._connection.snapshot())
            if errors:
                return self._abort(goal_handle, result, "; ".join(errors))
            mode_error = self._set_mode(self._hover_mode)
            if mode_error:
                return self._abort(goal_handle, result, mode_error)
            hold_state = self._connection.snapshot()
            if not hold_state.pose_valid:
                return self._abort(goal_handle, result, "local position is unavailable")
            hold_x = hold_state.x_enu
            hold_y = hold_state.y_enu
            hold_z = hold_state.z_enu
            hold_yaw = hold_state.yaw_enu
            duration_sec = max(
                self._action_period_sec,
                float(goal_handle.request.duration_sec) or self._default_hover_sec,
            )
            started_at = time.monotonic()
            next_setpoint_at = 0.0
            while rclpy.ok():
                elapsed = time.monotonic() - started_at
                result.elapsed_sec = min(elapsed, duration_sec)
                canceled = self._cancel_if_requested(goal_handle, result)
                if canceled is not None:
                    return canceled
                state = self._connection.snapshot()
                if not state.connected:
                    return self._abort(goal_handle, result, "MAVLink heartbeat lost during hover")
                if not state.localization_ok:
                    return self._abort(goal_handle, result, "localization became unhealthy during hover")
                now = time.monotonic()
                if now >= next_setpoint_at:
                    self._connection.send_local_position_target(
                        hold_x,
                        hold_y,
                        hold_z,
                        hold_yaw,
                    )
                    next_setpoint_at = now + self._setpoint_period_sec
                feedback = Hover.Feedback()
                feedback.elapsed_sec = min(elapsed, duration_sec)
                feedback.progress = min(1.0, elapsed / duration_sec)
                feedback.state = "hover"
                goal_handle.publish_feedback(feedback)
                if elapsed >= duration_sec:
                    result.success = True
                    goal_handle.succeed()
                    return result
                time.sleep(self._action_period_sec)
        finally:
            self._flight_lock.release()

    def _execute_land(self, goal_handle: Any) -> Land.Result:
        result = Land.Result()
        if not self._acquire_flight(goal_handle, result):
            return result
        try:
            if not self._connection.snapshot().connected:
                return self._abort(goal_handle, result, "MAVLink heartbeat is unavailable")
            mode_error = self._set_mode(self._land_mode)
            if mode_error:
                return self._abort(goal_handle, result, mode_error)
            deadline = time.monotonic() + max(
                self._land_timeout_sec,
                float(goal_handle.request.duration_sec),
            )
            initial_altitude = max(0.1, self._connection.snapshot().relative_altitude)
            while rclpy.ok() and time.monotonic() < deadline:
                state = self._connection.snapshot()
                result.final_altitude = state.relative_altitude
                canceled = self._cancel_if_requested(goal_handle, result)
                if canceled is not None:
                    return canceled
                if not state.connected:
                    return self._abort(goal_handle, result, "MAVLink heartbeat lost during landing")
                feedback = Land.Feedback()
                feedback.current_altitude = state.relative_altitude
                feedback.progress = min(1.0, max(0.0, 1.0 - state.relative_altitude / initial_altitude))
                feedback.state = "land"
                goal_handle.publish_feedback(feedback)
                if state.landed or (not state.armed and state.relative_altitude <= self._altitude_tolerance_m):
                    result.success = True
                    goal_handle.succeed()
                    return result
                time.sleep(self._action_period_sec)
            return self._abort(goal_handle, result, "landing timed out")
        finally:
            self._flight_lock.release()

    def _execute_rtl(self, goal_handle: Any) -> ReturnToLaunch.Result:
        result = ReturnToLaunch.Result()
        if not self._acquire_flight(goal_handle, result):
            return result
        try:
            if not self._connection.snapshot().connected:
                return self._abort(goal_handle, result, "MAVLink heartbeat is unavailable")
            mode_error = self._set_mode(self._rtl_mode)
            if mode_error:
                return self._abort(goal_handle, result, mode_error)
            if not bool(goal_handle.request.wait_for_landing):
                result.success = True
                result.landed = self._connection.snapshot().landed
                goal_handle.succeed()
                return result
            deadline = time.monotonic() + self._rtl_timeout_sec
            while rclpy.ok() and time.monotonic() < deadline:
                state = self._connection.snapshot()
                result.landed = state.landed
                canceled = self._cancel_if_requested(goal_handle, result)
                if canceled is not None:
                    return canceled
                if not state.connected:
                    return self._abort(goal_handle, result, "MAVLink heartbeat lost during RTL")
                feedback = ReturnToLaunch.Feedback()
                feedback.state = "rtl"
                feedback.distance_to_home = state.distance_to_home
                feedback.current_altitude = state.relative_altitude
                goal_handle.publish_feedback(feedback)
                if state.landed:
                    result.success = True
                    goal_handle.succeed()
                    return result
                time.sleep(self._action_period_sec)
            return self._abort(goal_handle, result, "RTL timed out")
        finally:
            self._flight_lock.release()

    def _execute_set_mode(self, goal_handle: Any) -> SetFlightMode.Result:
        result = SetFlightMode.Result()
        if not self._acquire_flight(goal_handle, result):
            return result
        try:
            mode = str(goal_handle.request.mode).strip().upper()
            if not mode:
                return self._abort(goal_handle, result, "mode must be non-empty")
            mode_error = self._set_mode(mode)
            result.current_mode = self._connection.snapshot().flight_mode
            if mode_error:
                return self._abort(goal_handle, result, mode_error)
            feedback = SetFlightMode.Feedback()
            feedback.current_mode = result.current_mode
            feedback.state = "mode_confirmed"
            goal_handle.publish_feedback(feedback)
            result.success = True
            goal_handle.succeed()
            return result
        finally:
            self._flight_lock.release()

    def _set_mode(self, mode: str) -> str:
        result = self._connection.set_mode(mode, self._command_timeout_sec * 2.0)
        return "" if result.accepted else result.message

    def _acquire_flight(self, goal_handle: Any, result: Any) -> bool:
        if self._flight_lock.acquire(blocking=False):
            return True
        self._abort(goal_handle, result, "another flight action is active")
        return False

    def _abort(self, goal_handle: Any, result: Any, message: str) -> Any:
        result.success = False
        result.error_message = message
        goal_handle.abort()
        return result

    def _cancel_if_requested(self, goal_handle: Any, result: Any) -> Any | None:
        if not bool(getattr(goal_handle, "is_cancel_requested", False)):
            return None
        cancel_result = self._connection.set_mode(self._safety.cancel_mode, self._command_timeout_sec)
        result.success = False
        result.error_message = (
            f"action canceled; safe mode {self._safety.cancel_mode} confirmed"
            if cancel_result.accepted
            else f"action canceled; safe mode failed: {cancel_result.message}"
        )
        goal_handle.canceled()
        return result

    def _publish_telemetry(self) -> None:
        state = self._connection.snapshot()
        now = self.get_clock().now().to_msg()

        state_message = DroneStateV1()
        state_message.stamp = now
        state_message.connected = state.connected
        state_message.armed = state.armed
        state_message.landed = state.landed
        state_message.flight_mode = state.flight_mode
        state_message.system_status = state.system_status
        state_message.battery_percent = state.battery_percent
        state_message.battery_voltage = state.battery_voltage
        state_message.gps_fix_type = state.gps_fix_type
        state_message.satellites_visible = state.satellites_visible
        state_message.localization_ok = state.localization_ok
        state_message.failsafe = state.failsafe
        state_message.relative_altitude = state.relative_altitude
        state_message.heartbeat_age_sec = state.heartbeat_age_sec
        state_message.autopilot_version = state.autopilot_version
        self._state_pub.publish(state_message)

        battery = BatteryState()
        battery.header.stamp = now
        battery.header.frame_id = self._base_frame_id
        battery.voltage = state.battery_voltage
        battery.percentage = state.battery_percent / 100.0 if state.battery_percent >= 0.0 else float("nan")
        self._battery_pub.publish(battery)

        if state.pose_valid:
            pose = self._pose_message(state, stamp=now)
            self._pose_pub.publish(pose)
            transform = TransformStamped()
            transform.header = pose.header
            transform.child_frame_id = self._base_frame_id
            transform.transform.translation.x = state.x_enu
            transform.transform.translation.y = state.y_enu
            transform.transform.translation.z = state.z_enu
            transform.transform.rotation = pose.pose.orientation
            self._tf_broadcaster.sendTransform(transform)

    def _update_orchestrator_admission(self) -> None:
        if not bool(self.get_parameter("update_orchestrator_admission").value):
            return
        if not self._parameter_client.services_are_ready():
            return
        if self._admission_future is not None and not self._admission_future.done():
            return
        state = self._connection.snapshot()
        capabilities: list[str] = []
        if state.connected:
            capabilities.append("flight")
        if state.connected and state.localization_ok:
            capabilities.append("localization")
        values = (
            state.battery_percent if state.connected and state.battery_percent >= 0.0 else 0.0,
            state.flight_mode if state.connected else "DISCONNECTED",
            state.connected and state.localization_ok,
            (not state.connected) or state.failsafe,
            tuple(capabilities),
        )
        if values == self._last_admission_values:
            return
        self._last_admission_values = values
        parameters = [
            Parameter("admission.battery_percent", value=float(values[0])),
            Parameter("admission.robot_mode", value=str(values[1])),
            Parameter("admission.localization_ok", value=bool(values[2])),
            Parameter("admission.emergency_stop_active", value=bool(values[3])),
            Parameter(
                "admission.available_capability_tags",
                Parameter.Type.STRING_ARRAY,
                list(values[4]),
            ),
        ]
        self._admission_future = self._parameter_client.set_parameters(
            parameters,
            callback=self._admission_update_completed,
        )

    def _admission_update_completed(self, future: Any) -> None:
        try:
            response = future.result()
            rejected = [result.reason for result in response.results if not result.successful]
            if rejected:
                self.get_logger().warning(
                    "Orchestrator rejected admission update: " + "; ".join(rejected)
                )
        except Exception as exc:
            self.get_logger().warning(f"Failed to update orchestrator admission: {exc}")

    def _pose_message(self, state: DroneTelemetrySnapshot, stamp: Any | None = None) -> PoseStamped:
        message = PoseStamped()
        message.header.stamp = stamp if stamp is not None else self.get_clock().now().to_msg()
        message.header.frame_id = self._frame_id
        message.pose.position.x = state.x_enu
        message.pose.position.y = state.y_enu
        message.pose.position.z = state.z_enu
        message.pose.orientation.z = math.sin(state.yaw_enu / 2.0)
        message.pose.orientation.w = math.cos(state.yaw_enu / 2.0)
        return message


def _distance_to_target(state: DroneTelemetrySnapshot, target_x: float, target_y: float, target_z: float) -> float:
    return math.sqrt(
        (state.x_enu - target_x) ** 2
        + (state.y_enu - target_y) ** 2
        + (state.z_enu - target_z) ** 2
    )


def _yaw_from_quaternion(quaternion: Any, default: float) -> float:
    norm = math.sqrt(
        float(quaternion.x) ** 2
        + float(quaternion.y) ** 2
        + float(quaternion.z) ** 2
        + float(quaternion.w) ** 2
    )
    if norm < 1e-9:
        return default
    sin_yaw = 2.0 * (float(quaternion.w) * float(quaternion.z) + float(quaternion.x) * float(quaternion.y))
    cos_yaw = 1.0 - 2.0 * (float(quaternion.y) ** 2 + float(quaternion.z) ** 2)
    return math.atan2(sin_yaw, cos_yaw)


def main(args: list[str] | None = None) -> None:
    rclpy.init(args=args)
    node = ArduPilotBridgeNode()
    executor = MultiThreadedExecutor(num_threads=8)
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        executor.shutdown()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
