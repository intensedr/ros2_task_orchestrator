"""MAVLink connection and ArduPilot state tracking."""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, replace
from typing import Any, Callable

from pymavlink import mavutil

from task_orchestrator_ardupilot_bridge.state import CommandResult, DroneTelemetrySnapshot


@dataclass
class _PendingCommand:
    event: threading.Event
    result_code: int | None = None


class MavlinkConnection:
    """Owns one MAVLink endpoint and exposes confirmed command helpers."""

    def __init__(
        self,
        connection_url: str,
        source_system: int = 245,
        source_component: int = 191,
        heartbeat_timeout_sec: float = 3.0,
        reconnect_delay_sec: float = 1.0,
        logger: Callable[[str], None] | None = None,
    ) -> None:
        self.connection_url = connection_url
        self.source_system = source_system
        self.source_component = source_component
        self.heartbeat_timeout_sec = heartbeat_timeout_sec
        self.reconnect_delay_sec = reconnect_delay_sec
        self._logger = logger
        self._master: Any | None = None
        self._state = DroneTelemetrySnapshot()
        self._state_lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._command_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._pending_commands: dict[int, _PendingCommand] = {}
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._target_system = 0
        self._target_component = 0
        self._streams_configured = False
        self._ekf_seen = False

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._receive_loop, name="ardupilot-mavlink", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop_event.set()
        master = self._master
        self._master = None
        if master is not None:
            try:
                master.close()
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def snapshot(self) -> DroneTelemetrySnapshot:
        now = time.monotonic()
        with self._state_lock:
            snapshot = replace(self._state)
        heartbeat_age = (
            now - snapshot.last_heartbeat_monotonic
            if snapshot.last_heartbeat_monotonic > 0
            else float("inf")
        )
        snapshot.heartbeat_age_sec = heartbeat_age
        snapshot.connected = self._master is not None and heartbeat_age <= self.heartbeat_timeout_sec
        if not snapshot.connected:
            snapshot.localization_ok = False
        return snapshot

    def wait_connected(self, timeout_sec: float) -> bool:
        return self.wait_until(lambda state: state.connected, timeout_sec)

    def wait_until(
        self,
        predicate: Callable[[DroneTelemetrySnapshot], bool],
        timeout_sec: float,
        poll_period_sec: float = 0.05,
    ) -> bool:
        deadline = time.monotonic() + max(0.0, timeout_sec)
        while not self._stop_event.is_set():
            if predicate(self.snapshot()):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(poll_period_sec)
        return False

    def command_long(
        self,
        command: int,
        params: tuple[float, float, float, float, float, float, float],
        timeout_sec: float = 3.0,
        retries: int = 2,
    ) -> CommandResult:
        if not self.wait_connected(timeout_sec):
            return CommandResult(False, -1, "MAVLink heartbeat is unavailable")

        with self._command_lock:
            for attempt in range(max(0, retries) + 1):
                pending = _PendingCommand(event=threading.Event())
                with self._pending_lock:
                    self._pending_commands[command] = pending
                try:
                    master = self._require_master()
                    with self._send_lock:
                        master.mav.command_long_send(
                            self._target_system,
                            self._target_component,
                            command,
                            attempt,
                            *params,
                        )
                    if pending.event.wait(timeout_sec):
                        result_code = int(pending.result_code if pending.result_code is not None else -1)
                        accepted_codes = {
                            mavutil.mavlink.MAV_RESULT_ACCEPTED,
                            mavutil.mavlink.MAV_RESULT_IN_PROGRESS,
                        }
                        return CommandResult(
                            result_code in accepted_codes,
                            result_code,
                            self._command_result_name(result_code),
                        )
                except Exception as exc:
                    if attempt >= retries:
                        return CommandResult(False, -1, f"MAVLink command send failed: {exc}")
                finally:
                    with self._pending_lock:
                        self._pending_commands.pop(command, None)
            return CommandResult(False, -1, f"command {command} acknowledgement timed out")

    def arm(self, arm: bool, force: bool, timeout_sec: float, retries: int) -> CommandResult:
        force_code = 21196.0 if force else 0.0
        return self.command_long(
            mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
            (1.0 if arm else 0.0, force_code, 0.0, 0.0, 0.0, 0.0, 0.0),
            timeout_sec=timeout_sec,
            retries=retries,
        )

    def takeoff(self, altitude_m: float, timeout_sec: float, retries: int) -> CommandResult:
        return self.command_long(
            mavutil.mavlink.MAV_CMD_NAV_TAKEOFF,
            (0.0, 0.0, 0.0, float("nan"), 0.0, 0.0, altitude_m),
            timeout_sec=timeout_sec,
            retries=retries,
        )

    def change_speed(self, speed_mps: float, timeout_sec: float, retries: int) -> CommandResult:
        return self.command_long(
            mavutil.mavlink.MAV_CMD_DO_CHANGE_SPEED,
            (1.0, speed_mps, -1.0, 0.0, 0.0, 0.0, 0.0),
            timeout_sec=timeout_sec,
            retries=retries,
        )

    def set_mode(self, mode: str, timeout_sec: float) -> CommandResult:
        if not self.wait_connected(timeout_sec):
            return CommandResult(False, -1, "MAVLink heartbeat is unavailable")
        master = self._require_master()
        mapping = master.mode_mapping() or {}
        normalized_mapping = {str(name).upper(): value for name, value in mapping.items()}
        custom_mode = normalized_mapping.get(mode.upper())
        if custom_mode is None:
            return CommandResult(False, -1, f"flight mode is unsupported: {mode}")
        try:
            with self._send_lock:
                master.mav.set_mode_send(
                    self._target_system,
                    mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
                    int(custom_mode),
                )
        except Exception as exc:
            return CommandResult(False, -1, f"failed to send flight mode {mode}: {exc}")
        confirmed = self.wait_until(
            lambda state: state.connected and state.flight_mode.upper() == mode.upper(),
            timeout_sec,
        )
        if not confirmed:
            return CommandResult(False, -1, f"flight mode was not confirmed: {mode}")
        return CommandResult(True, mavutil.mavlink.MAV_RESULT_ACCEPTED, "ACCEPTED")

    def send_local_position_target(self, x_enu: float, y_enu: float, z_enu: float, yaw_enu: float) -> None:
        master = self._require_master()
        ignore_mask = (
            mavutil.mavlink.POSITION_TARGET_TYPEMASK_VX_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_VY_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_VZ_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_AX_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_AY_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_AZ_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_YAW_RATE_IGNORE
        )
        yaw_ned = _wrap_angle(math.pi / 2.0 - yaw_enu)
        with self._send_lock:
            master.mav.set_position_target_local_ned_send(
                int(time.monotonic() * 1000.0) & 0xFFFFFFFF,
                self._target_system,
                self._target_component,
                mavutil.mavlink.MAV_FRAME_LOCAL_NED,
                ignore_mask,
                y_enu,
                x_enu,
                -z_enu,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                yaw_ned,
                0.0,
            )

    def _receive_loop(self) -> None:
        while not self._stop_event.is_set():
            if self._master is None:
                try:
                    self._open_master()
                except Exception as exc:
                    self._log(f"MAVLink connection failed ({self.connection_url}): {exc}")
                    self._stop_event.wait(self.reconnect_delay_sec)
                    continue
            try:
                message = self._master.recv_match(blocking=True, timeout=0.2)
                if message is not None:
                    self._handle_message(message)
            except Exception as exc:
                if self._stop_event.is_set():
                    break
                self._log(f"MAVLink receive failed; reconnecting: {exc}")
                self._drop_master()
                self._stop_event.wait(self.reconnect_delay_sec)

    def _open_master(self) -> None:
        self._master = mavutil.mavlink_connection(
            self.connection_url,
            source_system=self.source_system,
            source_component=self.source_component,
            autoreconnect=True,
            dialect="ardupilotmega",
        )
        self._target_system = 0
        self._target_component = 0
        self._streams_configured = False
        self._ekf_seen = False
        self._log(f"MAVLink endpoint opened: {self.connection_url}")

    def _drop_master(self) -> None:
        master = self._master
        self._master = None
        if master is not None:
            try:
                master.close()
            except Exception:
                pass

    def _handle_message(self, message: Any) -> None:
        message_type = message.get_type()
        if message_type == "BAD_DATA":
            return
        if message_type == "COMMAND_ACK":
            self._handle_command_ack(message)
            return

        if message_type == "HEARTBEAT":
            if int(message.type) == mavutil.mavlink.MAV_TYPE_GCS:
                return
            if int(message.autopilot) == mavutil.mavlink.MAV_AUTOPILOT_INVALID:
                return
            source_system = int(message.get_srcSystem())
            source_component = int(message.get_srcComponent())
            if self._target_system not in {0, source_system}:
                return
            self._target_system = source_system
            self._target_component = source_component
            with self._state_lock:
                self._state.last_heartbeat_monotonic = time.monotonic()
                self._state.armed = bool(
                    int(message.base_mode) & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED
                )
                self._state.flight_mode = str(mavutil.mode_string_v10(message))
                self._state.system_status = self._system_status_name(int(message.system_status))
                self._state.failsafe = int(message.system_status) in {
                    mavutil.mavlink.MAV_STATE_CRITICAL,
                    mavutil.mavlink.MAV_STATE_EMERGENCY,
                    mavutil.mavlink.MAV_STATE_POWEROFF,
                    mavutil.mavlink.MAV_STATE_FLIGHT_TERMINATION,
                }
            if not self._streams_configured:
                self._configure_streams()
            return

        if self._target_system and int(message.get_srcSystem()) != self._target_system:
            return

        if message_type == "LOCAL_POSITION_NED":
            with self._state_lock:
                self._state.x_enu = float(message.y)
                self._state.y_enu = float(message.x)
                self._state.z_enu = -float(message.z)
                self._state.pose_valid = True
                if self._state.relative_altitude <= 0.0:
                    self._state.relative_altitude = max(0.0, -float(message.z))
        elif message_type == "ATTITUDE":
            with self._state_lock:
                self._state.yaw_enu = _wrap_angle(math.pi / 2.0 - float(message.yaw))
        elif message_type == "GLOBAL_POSITION_INT":
            latitude = float(message.lat) / 1e7
            longitude = float(message.lon) / 1e7
            with self._state_lock:
                self._state.latitude_deg = latitude
                self._state.longitude_deg = longitude
                self._state.relative_altitude = max(0.0, float(message.relative_alt) / 1000.0)
                home_latitude = self._state.home_latitude_deg
                home_longitude = self._state.home_longitude_deg
                if math.isfinite(home_latitude) and math.isfinite(home_longitude):
                    self._state.distance_to_home = _great_circle_distance_m(
                        latitude,
                        longitude,
                        home_latitude,
                        home_longitude,
                    )
        elif message_type == "HOME_POSITION":
            with self._state_lock:
                self._state.home_latitude_deg = float(message.latitude) / 1e7
                self._state.home_longitude_deg = float(message.longitude) / 1e7
        elif message_type == "GPS_RAW_INT":
            with self._state_lock:
                self._state.gps_fix_type = int(message.fix_type)
                self._state.satellites_visible = int(message.satellites_visible)
                if not self._ekf_seen:
                    self._state.localization_ok = int(message.fix_type) >= 3
        elif message_type == "EKF_STATUS_REPORT":
            self._ekf_seen = True
            flags = int(message.flags)
            attitude = getattr(mavutil.mavlink, "EKF_ATTITUDE", 1)
            horizontal_relative = getattr(mavutil.mavlink, "EKF_POS_HORIZ_REL", 8)
            horizontal_absolute = getattr(mavutil.mavlink, "EKF_POS_HORIZ_ABS", 16)
            vertical_absolute = getattr(mavutil.mavlink, "EKF_POS_VERT_ABS", 32)
            localization_ok = bool(flags & attitude) and bool(
                flags & (horizontal_relative | horizontal_absolute)
            ) and bool(flags & vertical_absolute)
            with self._state_lock:
                self._state.localization_ok = localization_ok
        elif message_type == "SYS_STATUS":
            with self._state_lock:
                if int(message.battery_remaining) >= 0:
                    self._state.battery_percent = float(message.battery_remaining)
                if int(message.voltage_battery) not in {0, 65535}:
                    self._state.battery_voltage = float(message.voltage_battery) / 1000.0
        elif message_type == "BATTERY_STATUS":
            with self._state_lock:
                if int(message.battery_remaining) >= 0:
                    self._state.battery_percent = float(message.battery_remaining)
                valid_voltages = [
                    int(value) for value in message.voltages if int(value) not in {0, 65535}
                ]
                if valid_voltages:
                    self._state.battery_voltage = sum(valid_voltages) / 1000.0
        elif message_type == "EXTENDED_SYS_STATE":
            with self._state_lock:
                self._state.landed = int(message.landed_state) == mavutil.mavlink.MAV_LANDED_STATE_ON_GROUND
        elif message_type == "AUTOPILOT_VERSION":
            with self._state_lock:
                self._state.autopilot_version = _decode_flight_sw_version(int(message.flight_sw_version))
        elif message_type == "STATUSTEXT":
            text = str(message.text).rstrip("\x00")
            with self._state_lock:
                self._state.status_text = text

    def _handle_command_ack(self, message: Any) -> None:
        command = int(message.command)
        with self._pending_lock:
            pending = self._pending_commands.get(command)
            if pending is None:
                return
            pending.result_code = int(message.result)
            pending.event.set()

    def _configure_streams(self) -> None:
        self._streams_configured = True
        stream_rates_hz = {
            mavutil.mavlink.MAVLINK_MSG_ID_LOCAL_POSITION_NED: 10,
            mavutil.mavlink.MAVLINK_MSG_ID_ATTITUDE: 10,
            mavutil.mavlink.MAVLINK_MSG_ID_GLOBAL_POSITION_INT: 5,
            mavutil.mavlink.MAVLINK_MSG_ID_GPS_RAW_INT: 2,
            mavutil.mavlink.MAVLINK_MSG_ID_SYS_STATUS: 2,
            mavutil.mavlink.MAVLINK_MSG_ID_EXTENDED_SYS_STATE: 2,
            mavutil.mavlink.MAVLINK_MSG_ID_EKF_STATUS_REPORT: 2,
        }
        for message_id, rate_hz in stream_rates_hz.items():
            self._send_message_interval(message_id, rate_hz)
        self._send_message_interval(mavutil.mavlink.MAVLINK_MSG_ID_AUTOPILOT_VERSION, 0)
        try:
            master = self._require_master()
            with self._send_lock:
                master.mav.command_long_send(
                    self._target_system,
                    self._target_component,
                    mavutil.mavlink.MAV_CMD_REQUEST_MESSAGE,
                    0,
                    mavutil.mavlink.MAVLINK_MSG_ID_AUTOPILOT_VERSION,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                )
        except Exception:
            pass

    def _send_message_interval(self, message_id: int, rate_hz: int) -> None:
        interval_us = -1.0 if rate_hz <= 0 else 1_000_000.0 / rate_hz
        try:
            master = self._require_master()
            with self._send_lock:
                master.mav.command_long_send(
                    self._target_system,
                    self._target_component,
                    mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL,
                    0,
                    float(message_id),
                    interval_us,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                )
        except Exception:
            pass

    def _require_master(self) -> Any:
        master = self._master
        if master is None:
            raise RuntimeError("MAVLink endpoint is not open")
        return master

    def _command_result_name(self, result_code: int) -> str:
        enum_entry = mavutil.mavlink.enums.get("MAV_RESULT", {}).get(result_code)
        return str(enum_entry.name if enum_entry is not None else result_code)

    def _system_status_name(self, status_code: int) -> str:
        enum_entry = mavutil.mavlink.enums.get("MAV_STATE", {}).get(status_code)
        return str(enum_entry.name if enum_entry is not None else status_code)

    def _log(self, message: str) -> None:
        if self._logger is not None:
            self._logger(message)


def _wrap_angle(value: float) -> float:
    return math.atan2(math.sin(value), math.cos(value))


def _decode_flight_sw_version(encoded: int) -> str:
    major = (encoded >> 24) & 0xFF
    minor = (encoded >> 16) & 0xFF
    patch = (encoded >> 8) & 0xFF
    return f"{major}.{minor}.{patch}"


def _great_circle_distance_m(lat_a: float, lon_a: float, lat_b: float, lon_b: float) -> float:
    earth_radius_m = 6_371_000.0
    phi_a = math.radians(lat_a)
    phi_b = math.radians(lat_b)
    delta_phi = math.radians(lat_b - lat_a)
    delta_lambda = math.radians(lon_b - lon_a)
    haversine = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi_a) * math.cos(phi_b) * math.sin(delta_lambda / 2.0) ** 2
    )
    return 2.0 * earth_radius_m * math.atan2(math.sqrt(haversine), math.sqrt(1.0 - haversine))
