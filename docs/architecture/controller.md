# TCCS Controller Architecture

## Scope

Stage 2 develops the controller console independently of the Asterisk communications core.

## Components

```text
Touchscreen Browser
        |
        | HTTPS / WebSocket
        v
FastAPI Controller API
        |
        +---- PostgreSQL
        |
        +---- future ARI/AMI integration
        |
        +---- future TCCS event bus
```

## Design principles

1. The controller UI is data-driven; station numbers and names are not hard-coded into production code.
2. Call-control actions will be mediated by the backend and authorized by role.
3. Real-time station/call state will use WebSocket events rather than UI polling where practical.
4. Asterisk integration is deliberately deferred until the controller domain model and UI are stable.
5. Safety-critical operational deployment requires engineering validation, test evidence, and applicable railway approvals.
