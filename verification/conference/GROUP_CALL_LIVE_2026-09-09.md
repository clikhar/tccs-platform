# TCCS Group Call — Live Verification Record

**Test date:** 2026-09-09  
**Environment:** `asteriskprimary`  
**Core commit:** `dc9edd145d30c0636fc147b514d16e574e3053a5`  
**CI:** TCCS Core CI run #128 — success  
**Tested endpoints:** 1001, 1002, 1003

## Objective

Verify TCCS Core group-call establishment, three-party audio, participant removal, final cleanup, and independence of destination legs when one target is unreachable.

## Preconditions

- TCCS Core container healthy.
- PostgreSQL healthy.
- Asterisk had no active channels before the clean test.
- Core health endpoint returned HTTP 200.
- Existing 0-channel stale Asterisk bridge objects were present; they contained no channels and did not participate in the test call.

## Test cases and results

### GC-01 — Three-party group call

**Action**

Created a group call from 1001 to 1002 and 1003 using:

```text
POST /api/v1/group-calls
{"source":"1001","targets":["1002","1003"]}
```

**Observed**

- 1001, 1002 and 1003 entered `Stasis`.
- All three channels were members of the same TCCS bridge.
- Asterisk reported the active bridge with 3 channels and `softmix` technology.
- Three-way audio was confirmed operational between all participants.

**Result:** PASS

### GC-02 — Remove one participant

**Action**

1002 was hung up while 1001 and 1003 remained connected.

**Observed**

- 1002 channel was removed.
- 1001 and 1003 remained `Up`.
- Both remaining channels stayed in the same TCCS bridge.
- The bridge contained 2 channels and Asterisk selected `native_rtp` technology.
- No Core exception, traceback, error or failure was observed.

**Result:** PASS

### GC-03 — Final participant cleanup

**Action**

The remaining group-call participant was hung up.

**Observed**

- `core show channels concise` returned no active channels for the test call.
- The test bridge no longer appeared in `bridge show all`.
- No Core exception, traceback, error or failure was observed.

**Result:** PASS

### GC-04 — One unreachable target does not block another

**Action**

1002 was made unreachable while 1003 remained reachable. A group call was initiated from 1001 to 1002 and 1003.

**Observed**

- 1002 remained down/unreachable.
- 1003 was successfully originated and entered `Stasis`.
- 1003 received the call.
- No Core exception or failure was observed.

**Result:** PASS

### GC-05 — Reverse unreachable-target test

**Action**

1003 was made unreachable while 1002 remained reachable. A group call was initiated from 1001 to 1002 and 1003.

**Observed**

- 1003 remained down/unreachable.
- 1002 was successfully originated and received the call.
- No Core exception or failure was observed.

**Result:** PASS

## Verification conclusion

The tested TCCS group-call path passed live functional verification for:

- multi-target call establishment;
- three-party bridge formation;
- three-way audio;
- independent handling of an unreachable target;
- participant removal while preserving the remaining conference;
- final call and bridge cleanup; and
- absence of observed Core runtime errors during the tested scenarios.

This record verifies the tested software behavior only. It does **not** constitute complete RDSO system compliance, hardware qualification, capacity/load validation, or operational railway approval.

## Evidence

The live verification was performed against Core commit `dc9edd145d30c0636fc147b514d16e574e3053a5`, for which TCCS Core CI run #128 completed successfully, including Python compilation, database migrations, tests, and Core container build.
