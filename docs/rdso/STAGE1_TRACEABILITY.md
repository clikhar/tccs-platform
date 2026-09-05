# TCCS Stage 1 — RDSO Requirements Traceability

**Specification baseline:** RDSO/SPN/TC/99/2023 Ver. 3.0, effective 10.11.2023.

**Status legend:**

- `PLANNED` — requirement identified but implementation not yet demonstrated.
- `IMPLEMENTED` — software/configuration exists; verification evidence still required.
- `VERIFIED` — test evidence recorded and repeatable.
- `N/A-HW` — primarily a physical/hardware qualification requirement; software project records the dependency but does not claim compliance by itself.

## A. Functional requirements that drive Stage 1

| RDSO clause | Requirement summary | Stage 1 component | Planned evidence | Status |
|---|---|---|---|---|
| 3.2 | Controller ↔ way-station communication with individual, group and general call; conference behavior | TCCS Core + Asterisk | SIP integration tests | PLANNED |
| 3.2.2 | Way station joins conference on handset lift | TCCS dialplan/call service | Lift-and-listen test | PLANNED |
| 3.2.3 | Lift-and-listen, muted microphone, star-key toggle, controller conference behavior | TCCS Core + endpoint profile | DTMF/conference test | PLANNED |
| 3.2.4 | On-hook removes participant | Asterisk conference adapter | Participant lifecycle test | PLANNED |
| 3.2.5 | Controller can mute/unmute/disconnect participant | Call-control API + Asterisk | Controller action integration test | PLANNED |
| 3.3 | Multiple control sections and way stations | Section/group model | Multi-section test | PLANNED |
| 3.4.1 | Visual call-status indications until answer and then conference | Event bus/API → Stage 2 | Event sequence test | PLANNED |
| 3.4.2 | Add railway subscriber to ongoing conference | Call-control service | Conference-add test | PLANNED |
| 3.4.3 | Ring ASM phone even when busy/off-hook, optionally via separate ringer | Gateway abstraction + dialplan | Busy/off-hook ringer test | PLANNED |
| 3.5 | Dedicated recording, MP3, time-stamped files, defined segmentation | Recording subsystem | Recording retrieval test | PLANNED |
| 3.6 | Emergency gateway and emergency conference | Emergency service + gateway adapter | Simulated emergency gateway test | PLANNED |
| 3.7 | Emergency conference can join section controller conference | Conference service | Emergency escalation test | PLANNED |
| 3.8 | Flexible programmable call rules | Rule engine/configuration | Rule-change regression suite | PLANNED |

## B. Technical and security requirements

| RDSO clause | Requirement summary | Stage 1 implementation | Evidence | Status |
|---|---|---|---|---|
| 4.2.1 | SIP communication per RFC 3261 and relevant extensions | PJSIP/Asterisk | SIP interoperability tests | PLANNED |
| 4.2.2 | SIP-based recording protocol such as SIPREC | Recording adapter | SIPREC integration test | PLANNED |
| 4.5-4.6 | Correct time handling and common NTP source | OS + Core time policy | Clock/NTP/restart tests | PLANNED |
| 4.7 | 24x7 operation | service supervision + resilience tests | soak/restart tests | PLANNED |
| 4.8 | Central NMS and test-room support | NMS API + test endpoints | monitoring/integration test | PLANNED |
| 4.9.1 | Separate TCCS VLAN | deployment/network design | network configuration evidence | N/A-HW |
| 4.9.2 | Only authorized telephones register/communicate | PJSIP auth + endpoint ACL | negative registration tests | PLANNED |
| 4.9.4 | Password-protected configuration and timestamped logging | API RBAC + audit log | security audit test | PLANNED |
| 4.9.5 | Secure configuration sessions | SSHv2 + HTTPS/TLS | security configuration evidence | PLANNED |
| 4.9.6 | Secure access and intrusion/system logs | syslog + audit events | log verification test | PLANNED |
| 4.9.7 | Communication restricted to trusted hosts | firewall + ACL policy | ACL negative tests | PLANNED |
| 4.10 | Central subscriber profiles and replacement provisioning | database + provisioning service | endpoint replacement test | PLANNED |
| 4.12 | 802.3p/Q and ToS/DiffServ voice marking | endpoint/network policy | packet-capture/QoS test | PLANNED |
| 4.13 | DHCP and fixed addressing support | provisioning/deployment config | DHCP/fixed-IP tests | PLANNED |
| 4.14 | Provisioning restricted to defined network/IP; authenticated status view | API gateway/RBAC | access-control test | PLANNED |
| 4.17 | Automatic recovery after power restoration; configuration retained | persistence + service startup | restart/power simulation test | PLANNED |
| 4.18 | IPv4 and IPv6 | application + PJSIP config | dual-stack test | PLANNED |
| 4.19 | Flexible up-to-8-digit numbering, variable extension digits | numbering/routing service | numbering test suite | PLANNED |
| 4.19.1 | Unique way-station dialing code | numbering database | provisioning test | PLANNED |
| 4.19.2 | General-call code/key | dialplan/call service | general-call test | PLANNED |
| 4.19.3 | Group-call dialing code | group model/dialplan | group-call test | PLANNED |
| 4.20 | Call data reports, time/duration, export, immutable records | CDR/event service + reporting | report and immutability test | PLANNED |

## C. Server, scale, redundancy and gateway requirements

| RDSO clause | Requirement summary | Stage 1 treatment | Evidence | Status |
|---|---|---|---|---|
| 5.1.1 | Communication/recording server hardware baseline | Deployment hardware profile | vendor/system bill of material | N/A-HW |
| 5.1.2 | 1+1 redundancy; active-active/load sharing; full-load individual node; geographical separation; no common failure dependency | HA architecture + failover harness | failover test plan/evidence | PLANNED |
| 5.1.3 | Hardened OS, firewall, reduced services/ports, password management, DoS resistance, dual Ethernet, full-load operation, control circuits, subscribers, conferences | deployment baseline + load tests | hardening checklist and load test | PLANNED |
| 5.1.3 scale | Minimum 100 concurrent conference calls and 25 subscribers per call | Asterisk/TCCS capacity target | load test | PLANNED |
| 5.1.3 | IP/SIP trunking with other TCCSs | trunk adapter/routing | interoperability test | PLANNED |
| 5.1.3 | FXS gateway / distributed 2-wire telephone support | gateway abstraction | gateway integration test | PLANNED |
| 5.1.4 | Record conversations; search; at least 30 days or specified capacity; alarms; GUI/client access; protected records; rollover | recording subsystem | recording test and retention test | PLANNED |
| 5.2 | NMS monitoring/management | NMS API | functional NMS test | PLANNED |
| 5.2.4-5.2.7 | VoIP quality monitoring, fault/event management, configuration management, desktop/maintenance access | NMS subsystem | NMS verification suite | PLANNED |

## D. Later-stage endpoint and hardware clauses

Controller console, test-room console, rugged IP telephone, gateways, climatic tests, EMI/EMC, and electrical-safety requirements remain part of the overall TCCS program. Their software-facing interfaces are included in Stage 1, but physical qualification is not represented as software compliance.

## E. Verification evidence structure

```text
verification/
  requirements/
  sip/
  conference/
  emergency/
  recording/
  security/
  ipv4-ipv6/
  resilience/
  load/
  nms/
  reports/
```

Each test record should contain:

- requirement clause
- test ID
- test environment/version
- preconditions
- exact steps
- expected result
- actual result
- pass/fail
- logs/pcaps/screenshots as applicable
- software/configuration version
- date/time

## F. Important interpretation notes

1. The RDSO specification treats TCCS as a system; passing an application-level test is not the same as proving complete system compliance.
2. Underlying IP network specifications are explicitly outside the scope of this TCCS specification, although required network behavior and interfaces still have to be demonstrated.
3. Hardware/environmental requirements require separate vendor/OEM and type/acceptance evidence.
4. The specification says the 2023 revision is effective from 10.11.2023; later amendments, purchaser-specific requirements, and applicable current instructions must be checked before any final compliance submission.
