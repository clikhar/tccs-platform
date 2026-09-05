# TCCS Stage 1 — Communication Core Implementation Plan

**Baseline:** RDSO/SPN/TC/99/2023 Ver. 3.0 (effective 10.11.2023)

**Repository:** `clikhar/tccs-platform`

**Purpose:** Build the TCCS communication core that provides SIP-based call control, omnibus-style control communication, conferencing, emergency-call integration, recording interfaces, centralized provisioning, monitoring, and a clean API boundary for the Stage 2 controller console.

> This plan is an engineering implementation baseline. It is not a claim of RDSO compliance or operational suitability. Compliance must be demonstrated by the required tests, inspection, documentation, and approvals.

## 1. Stage 1 boundary

### Included

- Asterisk/PJSIP communication core
- SIP endpoint authentication and provisioning
- UDP/TCP/TLS/WSS transport architecture as required by the endpoint class
- TCCS numbering and programmable routing
- Individual, group and general-call workflows
- Omnibus-style controller/way-station conference behavior
- Lift-and-listen / muted participant behavior
- Controller participant control: mute, unmute, disconnect
- Call/event state model exposed to Stage 2
- SIP trunk/inter-TCCS foundation
- FXS/FXO/emergency gateway integration interfaces
- Recording integration and SIPREC design
- Centralized subscriber configuration
- NTP/time handling requirements
- Security, audit logging and trusted-host controls
- System health and NMS integration interfaces
- IPv4/IPv6 support
- Automated functional/integration verification
- Documentation and RDSO traceability

### Not implemented in software alone

- Underlying railway IP infrastructure
- Physical rack, power, EMC, climatic and environmental qualification
- Vendor-specific gateway/telephone hardware certification
- Final redundancy/failover acceptance until the complete hardware/network topology is available

## 2. Target architecture

```text
                          TCCS PLATFORM

       Stage 2 Controller Console
                  |
           REST / WebSocket
                  |
        +---------v----------+
        |     TCCS Core      |
        | FastAPI + services |
        +----+-----------+---+
             |           |
          ARI/AMI      PostgreSQL
             |
        +----v----------------+
        |       Asterisk       |
        | PJSIP / Dialplan /   |
        | Conferences / Media  |
        +---+-----+------+-----+
            |     |      |
           SIP   RTP   SIPREC
            |     |      |
     +------+-----+--+   +----------------+
     | endpoints/gateways|   Recording    |
     | controller/ASM/   |   subsystem     |
     | emergency/FXS/FXO |                |
     +-------------------+                |
                                          v
                                      NMS / audit
```

Asterisk is the real-time SIP/media engine. TCCS Core owns railway communication rules, logical devices, provisioning policy, call state, events, and audit semantics. The Stage 2 controller communicates with TCCS Core rather than editing Asterisk configuration directly.

## 3. Development phases

### S1.1 — Foundation

- Define PostgreSQL schema for sites, control sections, devices, endpoints, extensions, groups, calls, events and audit logs.
- Define configuration model and environment management.
- Establish FastAPI service skeleton and health endpoint.
- Establish structured logging and audit event model.
- Define service accounts and least-privilege boundaries.
- Add initial automated test framework.

**Exit:** Core starts cleanly, connects to PostgreSQL, exposes health/status, and passes database/model tests.

### S1.2 — Asterisk/PJSIP communication core

- Establish PJSIP transport policy.
- Implement endpoint authentication/authorization.
- Implement centralized endpoint templates.
- Implement controller, way-station and gateway endpoint classes.
- Add secure management path and controlled configuration interfaces.
- Implement basic TCCS dialplan entry points.
- Add codec policy and QoS marking hooks.
- Validate IPv4 and IPv6 operation.

**Exit:** Authorized SIP endpoints can register and make controlled point-to-point calls in the test network.

### S1.3 — Omnibus TCCS call engine

Implement the behavior required by clause 3.2 onward:

- controller-to-way-station individual call
- group call
- general call to all stations in a section
- persistent controller conference
- way-station lift-and-listen behavior
- muted way-station participant on join
- star-key or configured DTMF toggle for talk/unmute
- automatic joining after handset lift where applicable
- controller visual-state events
- participant mute/unmute/disconnect
- disconnect on on-hook
- programmable call rules

**Exit:** End-to-end TCCS conference scenarios pass in a repeatable SIP test lab.

### S1.4 — Emergency and gateway integration

- Define gateway abstraction and endpoint inventory model.
- Support station FXS-connected control telephones.
- Provide emergency gateway call flow.
- Support emergency conference joining and controller escalation.
- Provide FXO/SIP trunk abstraction for railway telephone exchange integration.
- Provide inter-TCCS SIP trunk routing foundation.

**Exit:** Simulated emergency socket/gateway and trunk scenarios pass integration tests.

### S1.5 — Recording integration

- Define recording session model and metadata.
- Implement recording event lifecycle.
- Add SIPREC-compatible integration point.
- Implement timestamped recording metadata.
- Define retention/rollover policy.
- Expose authorized search/retrieval API.
- Preserve immutable audit trail for recording access.

**Exit:** Controller conference and configured gateway scenarios generate searchable recording metadata and a test recording stream.

### S1.6 — Provisioning, security and audit

- Central subscriber profile store.
- Authorized endpoint replacement/re-provisioning.
- Password-protected configuration operations.
- Audit every configuration change with timestamp and actor.
- SSHv2 administration.
- HTTPS/TLS management interface.
- Trusted-host controls.
- Syslog integration.
- Unauthorized-access event generation.
- Restrict provisioning endpoints to configured management networks.

**Exit:** Security tests prove unauthorized registration/configuration is rejected and changes are auditable.

### S1.7 — Monitoring and NMS interface

- Endpoint registration status.
- Active calls and call state.
- Asterisk service health.
- Core/database health.
- Recording health.
- Fault/event model.
- Metrics and alarms API.
- SNMP/syslog integration where required.
- Read-only cross-section monitoring interface.

**Exit:** NMS/test-room tools can retrieve health, endpoint, call and event information without modifying protected configuration paths.

### S1.8 — Resilience and verification

- Service restart recovery.
- Configuration persistence across restart.
- Database backup/restore tests.
- Asterisk failure tests.
- Network interruption tests.
- Endpoint fail/re-register tests.
- Redundant-server architecture and failover test harness.
- Load tests targeting at least the specification's stated conference scale.
- Build the FAT/type/acceptance evidence package.

**Exit:** All applicable software requirements have passing automated/manual test evidence and unresolved gaps are explicitly recorded.

## 4. API contract for Stage 2

The Stage 2 controller shall call TCCS Core APIs rather than manipulate Asterisk directly.

### REST

```text
GET    /api/v1/health
GET    /api/v1/system/status
GET    /api/v1/devices
GET    /api/v1/endpoints
GET    /api/v1/sections
GET    /api/v1/groups
GET    /api/v1/calls
POST   /api/v1/calls
POST   /api/v1/calls/{call_id}/answer
POST   /api/v1/calls/{call_id}/hangup
POST   /api/v1/calls/{call_id}/mute
POST   /api/v1/calls/{call_id}/unmute
POST   /api/v1/calls/{call_id}/transfer
POST   /api/v1/general-calls
POST   /api/v1/group-calls
POST   /api/v1/emergency
GET    /api/v1/events
GET    /api/v1/recordings
```

### WebSocket

```text
/ws/v1/events
```

Events should cover at least registration, ringing, answered, joined-conference, muted, unmuted, disconnected, emergency, recording and fault transitions.

## 5. Initial project layout

```text
core/
  api/
  events/
  models/
  services/
  adapters/
  config/

aısterisk/   # existing repository spelling retained if already present
  pjsip/
  dialplan/
  sounds/
  scripts/

database/
  migrations/
  seeds/

recording/
  api/
  adapters/

nms/
  api/
  adapters/

docs/
  rdso/

tests/
  unit/
  integration/
  sip/
  resilience/
```

## 6. Engineering rules

1. Railway business rules belong in TCCS Core, not scattered through Asterisk dialplan.
2. Asterisk remains replaceable behind adapters where practical.
3. Endpoint identity is separate from extension number and IP address.
4. Configuration is centrally persisted and auditable.
5. Every externally visible state transition should have a timestamped event.
6. No requirement is marked compliant merely because code exists; it needs evidence.
7. Hardware-only requirements remain explicitly separated from software verification.
8. Stage 2 consumes stable APIs and events; it does not become the source of call-control truth.

## 7. Stage 1 completion definition

Stage 1 is considered software-complete only when the following are all true:

- Basic SIP communication is operational in the controlled lab.
- Omnibus controller/way-station scenarios pass.
- Group/general calls pass.
- Participant mute/unmute/disconnect behavior passes.
- Emergency and gateway interfaces pass simulated integration tests.
- Recording integration passes.
- Central provisioning and audit pass.
- Security controls pass their negative tests.
- IPv4/IPv6 behavior is demonstrated.
- Monitoring and health APIs work.
- Recovery after controlled service/power interruption is demonstrated.
- Requirements traceability shows the status and evidence for every applicable requirement.

