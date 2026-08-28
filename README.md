# TCCS Platform

A modular Train Control Communication System platform under development, with an Asterisk-based communications core, controller console, recording, NMS, and RDSO-oriented verification.

## Project status

**Stage 2 — TCCS Controller Console**

The controller UI/backend are being developed before integration with the Asterisk communications core.

## Repository layout

- `controller/frontend` — controller touchscreen web UI
- `controller/backend` — FastAPI controller API
- `database` — schema and migrations
- `asterisk` — future SIP/call-control configuration
- `recording` — future recording subsystem
- `nms` — future network management subsystem
- `docs/rdso` — requirements and compliance evidence
- `tests` — functional, integration and RDSO-oriented tests

## Important

This is a development project. It is not yet an operational railway safety/communications system and must not be deployed for operational use without appropriate engineering validation, testing, approvals and certification.
