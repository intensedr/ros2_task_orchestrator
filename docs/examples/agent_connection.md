# Agent Connection

This example shows an external mission-operating agent using the public mission
API. The agent remains outside the core: it registers itself, validates
structured mission JSON, owns a mission lease, submits the mission and polls
state until completion.

## Build

```bash
colcon build --packages-select \
  task_orchestrator_msgs task_orchestrator_core task_orchestrator_examples \
  --symlink-install
source install/setup.bash
```

## Launch Core

```bash
ros2 launch task_orchestrator_core task_orchestrator.launch.py
```

The default demo mission uses the built-in `system/wait` task, so no simulator
or robot action server is required.

## Run The Agent

From another sourced terminal:

```bash
ros2 run task_orchestrator_examples demo_agent_client run
```

The `run` command performs the full happy path:

- `/task_orchestrator/register_agent`
- `/task_orchestrator/validate_mission`
- `/task_orchestrator/claim_mission`
- `/task_orchestrator/submit_mission`
- `/task_orchestrator/get_mission_state`
- `/task_orchestrator/release_mission`

The mission payload is installed from
`task_orchestrator_examples/missions/agent_wait_mission.json`.

## Manual Commands

Keep a heartbeat running when testing commands one by one:

```bash
ros2 run task_orchestrator_examples demo_agent_client heartbeat
```

Then use the same `--agent-id` from another terminal:

```bash
ros2 run task_orchestrator_examples demo_agent_client list
ros2 run task_orchestrator_examples demo_agent_client validate
ros2 run task_orchestrator_examples demo_agent_client claim --mission-id demo-1
ros2 run task_orchestrator_examples demo_agent_client submit --mission-id demo-1
ros2 run task_orchestrator_examples demo_agent_client state --mission-id demo-1
```

Control commands require the active `lease_token` printed by `claim` or
`submit`:

```bash
ros2 run task_orchestrator_examples demo_agent_client cancel \
  --mission-id demo-1 --lease-token TOKEN

ros2 run task_orchestrator_examples demo_agent_client pause \
  --mission-id demo-1 --lease-token TOKEN

ros2 run task_orchestrator_examples demo_agent_client resume \
  --mission-id demo-1 --lease-token TOKEN

ros2 run task_orchestrator_examples demo_agent_client retry \
  --mission-id demo-1 --lease-token TOKEN

ros2 run task_orchestrator_examples demo_agent_client release \
  --mission-id demo-1 --lease-token TOKEN
```

Planner or AI code should produce only mission JSON for this boundary. The
core validates the JSON and executes known tasks; human approval and planner
runtime stay outside the core.
