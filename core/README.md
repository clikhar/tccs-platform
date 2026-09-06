# TCCS Core

Stage 1 application boundary between the controller console and the Asterisk communication engine.

## Responsibilities

- TCCS logical device/endpoint identity
- Sections, groups and programmable call rules
- Call lifecycle and normalized events
- Asterisk ARI/AMI integration adapters
- Central provisioning and audit
- Health/status API for Stage 2 and NMS

Asterisk remains the real-time SIP/media engine. The controller must not edit Asterisk configuration directly.
