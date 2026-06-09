# Roadmap

This roadmap tracks the remaining work for the new-feature scope, external
client integration and example robot packages.

## Current Baseline

`0.4.0` is the current recovery-safe production baseline:

- opt-in queued execution for scheduled or conflict-queued task requests
- scheduling fields on `ExecuteTaskV1`
- task duration, fleet metadata, idempotency and tracing fields
- SQLite persistence and restart recovery for queued tasks
- resource-lock and task-group admission checks
- dry-run task validation through `ValidateTaskV1`
- YAML/JSON mission template resolution
- mission subtask retry backoff and timeout propagation
- history filters for task identity, fleet context, trace and idempotency
- terminal mission subtask snapshots with pending/canceled states
- structured task error payloads in `result_json`
- updated public API, event-envelope and integration documentation

## 0.4.0: MVP-Plus Completion

Goal: turn the `0.3.0` foundation into a recovery-safe production baseline.

Status: completed in `0.4.0`.

Delivered work:

- Persistent scheduled queue:
  - store `QUEUED` tasks in SQLite
  - recover queued tasks after node restart
  - preserve `scheduled_at`, `delay_sec`, `deadline_at` and idempotency data
- Task validation:
  - add `ValidateTaskV1`
  - add dry-run validation for task and mission payloads
  - generate JSON schemas from ROS2 service/action interfaces
- Task and event history:
  - add filters for `robot_id`, `fleet_id`, `zone_id`, `trace_id` and
    `idempotency_key`
  - document late-client and restart recovery flows
- Mission cancellation cascade:
  - record clear active, pending, skipped and canceled subtask states
  - include the final subtask snapshot in terminal mission results
- Structured errors:
  - define a stable error object shape for `result_json`
  - document the public error code registry

## 0.5.0: Mission Executor V2

Goal: make missions graph-capable while preserving the existing linear mission
path.

Planned work:

- Mission graph executor:
  - `depends_on`
  - parallel branches
  - pending, running, completed, failed and skipped subtask states
- Conditional steps:
  - continue
  - skip
  - retry
  - abort
- Retry policy:
  - max attempts
  - fixed backoff
  - exponential backoff
  - retry by selected error codes
- Deadlines and timeouts:
  - mission-level deadline
  - subtask-level deadline
  - explicit timeout events
- Audit replay:
  - rebuild task and mission state transitions from SQLite events

## 0.6.0: Policy, Admission And Control Hooks

Goal: make the orchestrator usable on robots with real safety and resource
constraints.

Planned work:

- Policy module:
  - resource locks
  - task groups
  - capability tags
  - zone locks
- Admission providers:
  - battery state
  - robot mode
  - localization health
  - emergency stop state
- Pause and resume:
  - advertise task control capabilities in task introspection
  - support configurable action/service pause and resume hooks
  - keep returning `UNSUPPORTED` when hooks are not configured
- Event hooks:
  - add an internal hook interface
  - keep product-specific behavior outside `task_orchestrator_core`

## 0.7.0: External Client Integration

Goal: make the external fleet-agent pattern a tested integration boundary.

Planned work:

- Adapter and client documentation:
  - external fleet-agent package structure
  - idempotent submit flow
  - reconnect and recovery flow
- Optional bridge package skeletons:
  - `task_orchestrator_bridge_websocket`
  - `task_orchestrator_bridge_mqtt`
  - `task_orchestrator_bridge_zenoh`
- Bridge-ready event envelope:
  - strict JSON examples
  - event deduplication by `event_id`
  - offline result buffering strategy
- Authorization:
  - optional command authorization hook
  - keep auth policy outside the OSS core

## 0.8.0: Agent-Ready Mission Operation

Goal: prepare the public API for mission-operating agents without embedding an
agent runtime in the core.

Planned work:

- Agent registry:
  - `RegisterAgentV1`
  - `ListAgentsV1`
  - heartbeat state
- Mission ownership:
  - claim and release
  - lease tokens
  - stale-agent protection
- Agent command API:
  - validate mission
  - submit mission
  - cancel, pause, resume and retry mission
  - get mission state
- AI/planner boundary:
  - agents produce structured mission JSON
  - core validates before execution
  - optional human approval remains outside the core

## Examples Track

Example packages should remain optional so the core stays lightweight.

Planned packages:

- `task_orchestrator_test_robots`
- `task_orchestrator_sim_nav2`
- `task_orchestrator_sim_manipulator`
- `task_orchestrator_sim_drone`

### `task_orchestrator_test_robots`

Purpose: fast fake action and service servers for CI and documentation.

Planned servers:

- fake navigation action
- fake docking action
- fake arm action
- fake gripper service
- fake drone takeoff, waypoint and land actions

Coverage:

- action-backed tasks
- service-backed tasks
- cancellation
- timeout
- retry
- resource locks
- task groups
- queued tasks
- mission templates
- idempotency
- event recovery

### `task_orchestrator_sim_nav2`

Purpose: simple box-shaped differential-drive robot with Nav2.

Planned scenarios:

- navigate to pose
- route mission with multiple waypoints
- queued navigation
- cancel active navigation
- stop mission
- mission template with parameters

### `task_orchestrator_sim_manipulator`

Purpose: simple arm and gripper workflows.

Planned scenarios:

- move arm to named pose
- open gripper
- close gripper
- pick-like sequence
- retry failed gripper step
- resource conflicts between arm tasks

### `task_orchestrator_sim_drone`

Purpose: simple drone workflow before any PX4 or ArduPilot-specific adapter.

Planned scenarios:

- takeoff
- go to waypoint
- hover or wait
- land
- emergency cancel
- mission timeout
- zone metadata

## Testing Strategy

Keep test levels separate:

```bash
colcon test --packages-select task_orchestrator_core
colcon test --packages-select task_orchestrator_test_robots
colcon test --packages-select task_orchestrator_sim_nav2
colcon test --packages-select task_orchestrator_sim_manipulator
colcon test --packages-select task_orchestrator_sim_drone
```

Core CI should stay lightweight and run only packages that do not require heavy
simulation dependencies. Simulation packages can run in a separate manual or
nightly profile.
