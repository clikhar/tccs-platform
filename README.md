# TCCS Platform

A modular Train Control Communication System platform under development, with an Asterisk-based communications core, controller console, recording, NMS, and RDSO-oriented verification.

## Project status

**Stage 1 foundation + Stage 2 TCCS Controller Console**

Stage 1 now has the communication-core architecture, RDSO traceability baseline, initial FastAPI service boundary, domain models, and test skeleton. Stage 2 controller UI/backend development continues in parallel and will integrate through the TCCS Core API rather than editing Asterisk configuration directly.

## Repository layout

- `core` — Stage 1 TCCS communication-core service/API
- `controller/frontend` — controller touchscreen web UI
- `controller/backend` — controller API
- `database` — schema and migrations
- `asterisk` — SIP/call-control configuration
- `recording` — recording subsystem
- `nms` — network management subsystem
- `docs/rdso` — requirements and compliance evidence
- `tests` — functional, integration and RDSO-oriented tests

## Stage 1 documents

- `docs/STAGE1_IMPLEMENTATION_PLAN.md` — phased implementation plan and architecture
- `docs/rdso/STAGE1_TRACEABILITY.md` — RDSO requirements-to-implementation-to-test baseline

## Important

This is a development project. It is not yet an operational railway safety/communications system and must not be deployed for operational use without appropriate engineering validation, testing, approvals and certification.
