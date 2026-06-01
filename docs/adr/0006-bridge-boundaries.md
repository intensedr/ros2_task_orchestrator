# ADR 0006: Bridge Boundaries

## Context

The project is used by external ROS2 clients. Protocol-specific bridge logic
belongs outside the core so pure ROS2 deployments stay small.

## Decision

Keep bridges optional and downstream of the ROS2 public API.

Core responsibilities:

- task lifecycle
- task execution
- active tasks
- results
- feedback
- events
- query services

Bridge responsibilities:

- subscribe to public ROS2 topics
- call public ROS2 actions/services
- transform messages into protocol-specific envelopes
- reconnect and buffer outbound events
- handle protocol-specific authentication if needed

External client responsibilities:

- cloud routing
- tenant and user authorization
- WebSocket/Zenoh/MQTT server protocol
- product-specific mission/task JSON
- UI-specific state mapping

The core must not import product-specific client code.

## Consequences

- The orchestrator remains reusable outside any specific external product.
- External clients can evolve independently.
- Bridge packages can be installed only when needed.
- The public ROS2 API becomes the contract and test boundary.
