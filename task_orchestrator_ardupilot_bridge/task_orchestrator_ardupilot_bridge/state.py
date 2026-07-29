"""Thread-safe value objects shared by the MAVLink and ROS layers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DroneTelemetrySnapshot:
    connected: bool = False
    heartbeat_age_sec: float = float("inf")
    armed: bool = False
    landed: bool = False
    flight_mode: str = "UNKNOWN"
    system_status: str = "UNKNOWN"
    battery_percent: float = -1.0
    battery_voltage: float = float("nan")
    gps_fix_type: int = 0
    satellites_visible: int = 0
    localization_ok: bool = False
    failsafe: bool = False
    pose_valid: bool = False
    x_enu: float = 0.0
    y_enu: float = 0.0
    z_enu: float = 0.0
    yaw_enu: float = 0.0
    relative_altitude: float = 0.0
    latitude_deg: float = 0.0
    longitude_deg: float = 0.0
    home_latitude_deg: float = float("nan")
    home_longitude_deg: float = float("nan")
    distance_to_home: float = float("nan")
    autopilot_version: str = "unknown"
    status_text: str = ""
    last_heartbeat_monotonic: float = 0.0


@dataclass(frozen=True)
class CommandResult:
    accepted: bool
    result_code: int
    message: str
