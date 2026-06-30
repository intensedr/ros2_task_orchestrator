from task_orchestrator_core.agent_registry import AgentRegistry, AgentRegistryError


def test_agent_registry_registers_heartbeat_and_filters_stale_agents():
    registry = AgentRegistry(default_heartbeat_timeout_sec=10.0, default_lease_duration_sec=60.0)

    registration = registry.register_agent(
        agent_id="agent-1",
        display_name="Planner",
        agent_type="planner",
        capabilities=("mission.compose",),
        heartbeat_timeout_sec=2.0,
        metadata_json='{"runtime": "external"}',
        now_ns=1_000_000_000,
    )

    assert registration.agent_id == "agent-1"
    assert registration.metadata_json == '{"runtime": "external"}'
    assert [agent.agent_id for agent in registry.list_agents(now_ns=2_000_000_000, include_stale=False)] == ["agent-1"]
    assert registry.list_agents(now_ns=4_000_000_000, include_stale=False) == []
    assert [agent.agent_id for agent in registry.list_agents(now_ns=4_000_000_000, include_stale=True)] == ["agent-1"]


def test_agent_registry_protects_active_mission_leases_and_allows_stale_takeover():
    registry = AgentRegistry(default_heartbeat_timeout_sec=1.0, default_lease_duration_sec=60.0)
    registry.register_agent(
        agent_id="agent-1",
        display_name="Planner 1",
        agent_type="planner",
        capabilities=(),
        heartbeat_timeout_sec=1.0,
        metadata_json="{}",
        now_ns=1_000_000_000,
    )
    registry.register_agent(
        agent_id="agent-2",
        display_name="Planner 2",
        agent_type="planner",
        capabilities=(),
        heartbeat_timeout_sec=5.0,
        metadata_json="{}",
        now_ns=1_000_000_000,
    )

    lease = registry.claim_mission(
        agent_id="agent-1",
        mission_id="mission-1",
        lease_duration_sec=30.0,
        now_ns=1_000_000_000,
    )

    try:
        registry.claim_mission(
            agent_id="agent-2",
            mission_id="mission-1",
            lease_duration_sec=30.0,
            now_ns=1_500_000_000,
        )
    except AgentRegistryError as exc:
        assert exc.error_code == "RESOURCE_CONFLICT"
    else:
        raise AssertionError("expected active lease conflict")

    takeover = registry.claim_mission(
        agent_id="agent-2",
        mission_id="mission-1",
        lease_duration_sec=30.0,
        now_ns=3_000_000_000,
    )

    assert takeover.agent_id == "agent-2"
    assert takeover.lease_token != lease.lease_token


def test_agent_registry_force_takeover_rotates_lease_token():
    registry = AgentRegistry(default_heartbeat_timeout_sec=10.0, default_lease_duration_sec=60.0)
    registry.register_agent(
        agent_id="agent-1",
        display_name="Planner 1",
        agent_type="planner",
        capabilities=(),
        heartbeat_timeout_sec=10.0,
        metadata_json="{}",
        now_ns=1_000_000_000,
    )
    registry.register_agent(
        agent_id="agent-2",
        display_name="Planner 2",
        agent_type="planner",
        capabilities=(),
        heartbeat_timeout_sec=10.0,
        metadata_json="{}",
        now_ns=1_000_000_000,
    )
    lease = registry.claim_mission(
        agent_id="agent-1",
        mission_id="mission-1",
        lease_duration_sec=30.0,
        now_ns=1_000_000_000,
    )

    takeover = registry.claim_mission(
        agent_id="agent-2",
        mission_id="mission-1",
        lease_duration_sec=30.0,
        force=True,
        now_ns=2_000_000_000,
    )

    assert takeover.agent_id == "agent-2"
    assert takeover.lease_token != lease.lease_token
    assert takeover.claimed_at_ns == 2_000_000_000


def test_agent_registry_requires_matching_lease_token_to_release():
    registry = AgentRegistry(default_heartbeat_timeout_sec=10.0, default_lease_duration_sec=60.0)
    registry.register_agent(
        agent_id="agent-1",
        display_name="Planner",
        agent_type="planner",
        capabilities=(),
        heartbeat_timeout_sec=10.0,
        metadata_json="{}",
        now_ns=1_000_000_000,
    )
    lease = registry.claim_mission(
        agent_id="agent-1",
        mission_id="mission-1",
        lease_duration_sec=30.0,
        now_ns=1_000_000_000,
    )

    try:
        registry.release_mission(
            agent_id="agent-1",
            mission_id="mission-1",
            lease_token="wrong",
            now_ns=2_000_000_000,
        )
    except AgentRegistryError as exc:
        assert exc.error_code == "POLICY_REJECTED"
    else:
        raise AssertionError("expected lease token rejection")

    released = registry.release_mission(
        agent_id="agent-1",
        mission_id="mission-1",
        lease_token=lease.lease_token,
        now_ns=2_000_000_000,
    )

    assert registry.lease_status(released, now_ns=2_000_000_000) == "RELEASED"
