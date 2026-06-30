"""In-memory registry for mission-operating agents and mission leases."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, replace


class AgentRegistryError(ValueError):
    """Raised when an agent registry operation is invalid."""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


@dataclass(frozen=True)
class AgentRegistration:
    """Heartbeat-backed agent registration state."""

    agent_id: str
    display_name: str
    agent_type: str
    capabilities: tuple[str, ...]
    heartbeat_timeout_sec: float
    metadata_json: str
    registered_at_ns: int
    last_heartbeat_at_ns: int


@dataclass(frozen=True)
class MissionLease:
    """Mission ownership lease state."""

    mission_id: str
    agent_id: str
    lease_token: str
    claimed_at_ns: int
    renewed_at_ns: int
    lease_expires_at_ns: int
    metadata_json: str = "{}"
    task_id: str = ""
    released: bool = False


class AgentRegistry:
    """Tracks live agents and mission ownership leases."""

    def __init__(self, default_heartbeat_timeout_sec: float, default_lease_duration_sec: float) -> None:
        self.default_heartbeat_timeout_sec = max(0.001, float(default_heartbeat_timeout_sec))
        self.default_lease_duration_sec = max(0.001, float(default_lease_duration_sec))
        self._agents: dict[str, AgentRegistration] = {}
        self._mission_leases: dict[str, MissionLease] = {}

    def register_agent(
        self,
        *,
        agent_id: str,
        display_name: str,
        agent_type: str,
        capabilities: tuple[str, ...],
        heartbeat_timeout_sec: float,
        metadata_json: str,
        now_ns: int,
    ) -> AgentRegistration:
        agent_id = agent_id.strip()
        if not agent_id:
            raise AgentRegistryError("TASK_DATA_PARSING_FAILED", "agent_id must be a non-empty string")
        normalized_metadata = _normalize_json_object(metadata_json, field_name="metadata_json")
        timeout_sec = float(heartbeat_timeout_sec) if heartbeat_timeout_sec > 0 else self.default_heartbeat_timeout_sec
        if timeout_sec <= 0:
            raise AgentRegistryError("TASK_DATA_PARSING_FAILED", "heartbeat_timeout_sec must be positive")

        existing = self._agents.get(agent_id)
        registered_at_ns = existing.registered_at_ns if existing is not None else now_ns
        registration = AgentRegistration(
            agent_id=agent_id,
            display_name=display_name,
            agent_type=agent_type,
            capabilities=tuple(capabilities),
            heartbeat_timeout_sec=timeout_sec,
            metadata_json=normalized_metadata,
            registered_at_ns=registered_at_ns,
            last_heartbeat_at_ns=now_ns,
        )
        self._agents[agent_id] = registration
        return registration

    def list_agents(self, *, now_ns: int, include_stale: bool, agent_id: str = "") -> list[AgentRegistration]:
        registrations = [
            registration
            for registration in self._agents.values()
            if (not agent_id or registration.agent_id == agent_id)
            and (include_stale or not self.is_agent_stale(registration.agent_id, now_ns=now_ns))
        ]
        return sorted(registrations, key=lambda registration: registration.agent_id)

    def get_agent(self, agent_id: str) -> AgentRegistration | None:
        return self._agents.get(agent_id)

    def is_agent_stale(self, agent_id: str, *, now_ns: int) -> bool:
        registration = self._agents.get(agent_id)
        if registration is None:
            return True
        return now_ns >= self.agent_stale_at_ns(registration)

    def agent_stale_at_ns(self, registration: AgentRegistration) -> int:
        return registration.last_heartbeat_at_ns + _sec_to_ns(registration.heartbeat_timeout_sec)

    def active_mission_id_for_agent(self, agent_id: str, *, now_ns: int) -> str:
        for lease in self._mission_leases.values():
            if lease.agent_id == agent_id and self.lease_status(lease, now_ns=now_ns) == "ACTIVE":
                return lease.mission_id
        return ""

    def claim_mission(
        self,
        *,
        agent_id: str,
        mission_id: str,
        lease_duration_sec: float,
        lease_token: str = "",
        force: bool = False,
        metadata_json: str = "{}",
        now_ns: int,
    ) -> MissionLease:
        self.require_live_agent(agent_id, now_ns=now_ns)
        mission_id = mission_id.strip()
        if not mission_id:
            raise AgentRegistryError("TASK_DATA_PARSING_FAILED", "mission_id must be a non-empty string")
        normalized_metadata = _normalize_json_object(metadata_json, field_name="metadata_json")
        duration_sec = float(lease_duration_sec) if lease_duration_sec > 0 else self.default_lease_duration_sec
        if duration_sec <= 0:
            raise AgentRegistryError("TASK_DATA_PARSING_FAILED", "lease_duration_sec must be positive")

        existing = self._mission_leases.get(mission_id)
        if existing is not None and self.lease_status(existing, now_ns=now_ns) == "ACTIVE":
            if existing.agent_id != agent_id:
                if not force:
                    raise AgentRegistryError(
                        "RESOURCE_CONFLICT",
                        f"Mission {mission_id} is already leased by agent {existing.agent_id}.",
                    )
                token = str(uuid.uuid4())
                claimed_at_ns = now_ns
            else:
                if lease_token and lease_token != existing.lease_token:
                    raise AgentRegistryError("POLICY_REJECTED", "lease_token does not match the active mission lease")
                token = existing.lease_token
                claimed_at_ns = existing.claimed_at_ns
            task_id = existing.task_id
        else:
            token = str(uuid.uuid4())
            claimed_at_ns = now_ns
            task_id = existing.task_id if existing is not None else ""

        lease = MissionLease(
            mission_id=mission_id,
            agent_id=agent_id,
            lease_token=token,
            claimed_at_ns=claimed_at_ns,
            renewed_at_ns=now_ns,
            lease_expires_at_ns=now_ns + _sec_to_ns(duration_sec),
            metadata_json=normalized_metadata,
            task_id=task_id,
            released=False,
        )
        self._mission_leases[mission_id] = lease
        return lease

    def release_mission(self, *, agent_id: str, mission_id: str, lease_token: str, now_ns: int) -> MissionLease:
        self.verify_lease(agent_id=agent_id, mission_id=mission_id, lease_token=lease_token, now_ns=now_ns)
        lease = self._mission_leases[mission_id]
        released_lease = replace(
            lease,
            released=True,
            renewed_at_ns=now_ns,
            lease_expires_at_ns=now_ns,
        )
        self._mission_leases[mission_id] = released_lease
        return released_lease

    def verify_lease(self, *, agent_id: str, mission_id: str, lease_token: str, now_ns: int) -> MissionLease:
        self.require_live_agent(agent_id, now_ns=now_ns)
        lease = self._mission_leases.get(mission_id)
        if lease is None:
            raise AgentRegistryError("POLICY_REJECTED", f"Mission {mission_id} is not leased.")
        status = self.lease_status(lease, now_ns=now_ns)
        if status != "ACTIVE":
            raise AgentRegistryError("POLICY_REJECTED", f"Mission lease is not active: {status}")
        if lease.agent_id != agent_id:
            raise AgentRegistryError(
                "RESOURCE_CONFLICT",
                f"Mission {mission_id} is leased by agent {lease.agent_id}.",
            )
        if not lease_token:
            raise AgentRegistryError("POLICY_REJECTED", "lease_token is required")
        if lease.lease_token != lease_token:
            raise AgentRegistryError("POLICY_REJECTED", "lease_token does not match the active mission lease")
        return lease

    def get_mission_lease(self, mission_id: str) -> MissionLease | None:
        return self._mission_leases.get(mission_id)

    def set_mission_task_id(self, mission_id: str, task_id: str) -> MissionLease | None:
        lease = self._mission_leases.get(mission_id)
        if lease is None:
            return None
        updated_lease = replace(lease, task_id=task_id)
        self._mission_leases[mission_id] = updated_lease
        return updated_lease

    def lease_status(self, lease: MissionLease, *, now_ns: int) -> str:
        if lease.released:
            return "RELEASED"
        if self.is_agent_stale(lease.agent_id, now_ns=now_ns):
            return "STALE_AGENT"
        if now_ns >= lease.lease_expires_at_ns:
            return "EXPIRED"
        return "ACTIVE"

    def require_live_agent(self, agent_id: str, *, now_ns: int) -> AgentRegistration:
        registration = self._agents.get(agent_id)
        if registration is None:
            raise AgentRegistryError("POLICY_REJECTED", f"Agent is not registered: {agent_id}")
        if self.is_agent_stale(agent_id, now_ns=now_ns):
            raise AgentRegistryError("POLICY_REJECTED", f"Agent heartbeat is stale: {agent_id}")
        return registration


def _normalize_json_object(value: str, *, field_name: str) -> str:
    payload_text = value or "{}"
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        raise AgentRegistryError("TASK_DATA_PARSING_FAILED", f"{field_name} is not valid JSON: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise AgentRegistryError("TASK_DATA_PARSING_FAILED", f"{field_name} must decode to an object")
    return json.dumps(payload, sort_keys=True)


def _sec_to_ns(value: float) -> int:
    return int(float(value) * 1_000_000_000)
