# SIP Verification — Individual Call

**Test ID:** SIP-IND-001  
**Requirement:** RDSO/SPN/TC/99/2023 Ver. 3.0, clause 3.2 (individual-call portion)  
**Environment:** TCCS Core + Asterisk 16.2.1 live integration environment  
**Software under test:** TCCS Core commit `c5d2b7ba19f18020aa64f1898d4dd9db24ca8d21`  
**Date:** 2026-09-08  

## Objective

Verify the live end-to-end individual call path from a way-station endpoint through TCCS Core and Asterisk ARI to the destination way-station, including successful two-way audio.

## Preconditions

- TCCS Core container deployed and healthy.
- Asterisk ARI application `tccs-core` registered successfully.
- Existing Stage 2 Controller / `9999` conference left unchanged.
- Source endpoint `1001` and destination endpoint `1003` available.

## Test Steps

1. Submitted `POST /api/v1/calls` with source `1001` and target `1003`.
2. Received call ID `dc4149f5-93dd-415a-b6e1-77ec1a5e7f4e` with initial state `initiated`.
3. Verified Asterisk channel `PJSIP/1001-00000013` entered `Stasis(tccs-core,source,...)`.
4. Verified Asterisk channel `PJSIP/1003-00000014` entered `Stasis(tccs-core,callee,...)`.
5. Queried the Core call API and observed state `connected`.
6. Answered the destination endpoint and verified audio from `1001` to `1003`.
7. Verified audio from `1003` to `1001`.

## Expected Result

The individual call is established through TCCS Core/Asterisk ARI and provides two-way voice communication between the source and destination endpoints.

## Actual Result

**PASS.** Both SIP legs entered the `tccs-core` ARI application, the Core call state reached `connected`, and two-way audio was confirmed between endpoints `1001` and `1003`.

## Evidence

- Call ID: `dc4149f5-93dd-415a-b6e1-77ec1a5e7f4e`
- Asterisk active channels showed both `PJSIP/1001-00000013` and `PJSIP/1003-00000014` in `Stasis(tccs-core,...)`.
- Core API returned `state: connected`.
- Existing `9999` controller conference remained active and was not modified.

## Scope Note

This record verifies the **individual-call portion** of clause 3.2 only. Group-call, general-call, conference behavior, and other clause 3.2 sub-requirements require separate verification records and must not be inferred from this test.
