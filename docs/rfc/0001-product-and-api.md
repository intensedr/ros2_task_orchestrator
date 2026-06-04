# RFC 0001: Product And Public API

## Summary

Create an open-source ROS2 task orchestration project named **ROS2 Task
Orchestrator**. The project provides a stable ROS2-native layer for starting,
tracking, cancelling and composing robot tasks. External products, including
fleet-management systems, consume it through public ROS2 APIs or optional
bridge packages.

## Decisions

- Project display name: **ROS2 Task Orchestrator**
- Repository name: `ros_task_orchestrator`
- ROS2 package prefix: `task_orchestrator`
- Repository packages:
  - `task_orchestrator_msgs`
  - `task_orchestrator_core`
  - `task_orchestrator_examples`
- Optional bridge packages use the `task_orchestrator_bridge_*` prefix.
- License: Apache-2.0
- Governance: maintainer-led open-source project with DCO sign-off and
  semantic versioning.

## Goals

- Provide feature parity with common ROS2 `task_manager` workflows.
- Expose a stable task lifecycle for ROS2 action and service backed work.
- Support external clients without making the core product-specific.
- Publish current state, terminal results, feedback and lifecycle events.
- Keep bridges optional so pure ROS2 deployments stay small.

## Non-Goals

- Implement any product-specific cloud client inside the core package.
- Build a fleet-wide scheduler inside the core package.
- Replace Nav2, robot drivers or existing task action servers.
- Guarantee pause/resume for tasks that do not support it.

## Product Model

The orchestrator runs on a robot or edge computer. It accepts task requests from
local ROS2 nodes, CLI tools, web bridges, automation rules or a fleet agent. It
then starts existing ROS2 actions or services and publishes a uniform stream of
state changes.

```text
client or bridge
  -> /task_orchestrator/execute_task
    -> task_orchestrator_core
      -> existing ROS2 service/action
```

Task state flows back through:

```text
/task_orchestrator/active_tasks
/task_orchestrator/results
/task_orchestrator/events
/task_orchestrator/feedback
```

## Public API Scope

The public ROS2 actions, topics and services are documented in
[Public API](../api/public-api.md). Field-level message and service contracts are
documented in [Public API Reference](../public_api_reference.md).

Pause and resume services return `UNSUPPORTED` when the requested capability is
unavailable.

## External Client Integration Boundary

Product-specific systems integrate as external clients:

```text
Backend/UI
  -> external fleet agent
    -> ROS2 action client
      -> ROS2 Task Orchestrator
```

The agent is responsible for WebSocket, Zenoh, MQTT, authentication and cloud
protocol details. The orchestrator is responsible for local task execution,
state and policy.
