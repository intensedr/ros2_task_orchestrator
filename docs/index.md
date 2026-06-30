# ROS2 Task Orchestrator

ROS2 Task Orchestrator is a ROS2-native edge task layer for starting,
tracking, canceling and composing robot tasks through stable public interfaces.

Existing ROS2 actions and services keep implementing robot behavior. The
orchestrator provides a common entry point, task state, terminal results,
feedback, structured events and optional SQLite durability.

## Packages

- `task_orchestrator_msgs`: public messages, services and actions.
- `task_orchestrator_core`: orchestrator node, task registry and lifecycle
  logic.
- `task_orchestrator_examples`: example task configs, launch files and
  payloads.
- `task_orchestrator_sim_nav2`: optional Nav2 TurtleBot simulation scenarios.
- `task_orchestrator_sim_drone`: optional fake drone simulation scenarios.

## Runtime Surface

- Action: `/task_orchestrator/execute_task`
- Live topics: `/active_tasks`, `/results`, `/events`, `/feedback`
- Query services: `/get_task`, `/list_task_records`, `/list_events`
- Control services: `/cancel_tasks`, `/stop`, `/reload_config`
- Agent mission services: `/register_agent`, `/claim_mission`,
  `/submit_mission`, `/get_mission_state`

## Defaults

- Storage is disabled by default.
- SQLite durability is optional and enabled only with `storage.enabled`.
- Humble is the minimum supported ROS2 distribution.
- Jazzy is the current tested target.
- Lyrical support is planned after build and CI validation.
- Foxy support is planned as best-effort legacy source compatibility.

## Next Reading

- [Getting Started](getting-started.md)
- [Roadmap](roadmap.md)
- [Task YAML](configuration/task-yaml.md)
- [Public API](api/public-api.md)
- [Agent Connection](examples/agent_connection.md)
- [Observability](concepts/observability.md)
- [SQLite Storage](operations/sqlite-storage.md)
