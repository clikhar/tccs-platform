# Asterisk live integration

This stage connects TCCS Core's call orchestration to a running Asterisk instance through ARI.

## Architecture

For an outbound TCCS call, TCCS Core calls Asterisk ARI `POST /ari/channels` with:

- `endpoint=PJSIP/<target>` for a normal PJSIP subscriber
- `app=tccs-core`
- `appArgs=outbound,<source>,<target>`

Asterisk passes the answered channel to the `tccs-core` Stasis application. The Core ARI WebSocket consumes `StasisStart`, `ChannelStateChange`, and `StasisEnd` events and persists their effects.

The normal `[tccs-endpoints]` dialplan is intentionally not used by this ARI originate path. The ARI `app` parameter is mutually exclusive with dialplan context/extension/priority parameters and hands the channel to the named Stasis application when it is answered.

## Asterisk configuration

1. Enable ARI and create the `tccs` ARI user using `asterisk/ari.conf.example` as the template. Use a site-specific password and keep the production file outside Git.
2. Keep the existing HTTP/TLS listener in `asterisk/http.conf` enabled.
3. Keep the `transport-wss` transport in `asterisk/tccs-pjsip.conf` for secure WebSocket SIP clients. It is independent of the ARI HTTP/WebSocket control interface.
4. Ensure the target subscriber has a registered PJSIP endpoint matching the number used in the API request, for example `PJSIP/2001`.
5. Set the TCCS Core runtime configuration:

```text
TCCS_ASTERISK_ARI_ENABLED=true
TCCS_ASTERISK_ARI_URL=http://<asterisk-host>:8088/ari
TCCS_ASTERISK_ARI_USERNAME=tccs
TCCS_ASTERISK_ARI_PASSWORD=<site-secret>
TCCS_ASTERISK_ARI_APP=tccs-core
```

Use HTTPS for the ARI URL when the deployment exposes ARI through TLS.

## Reload sequence

After installing or changing Asterisk configuration, validate and reload the affected modules using the Asterisk CLI appropriate to the deployed version. At minimum verify:

```text
http show status
pjsip show transports
pjsip show endpoints
ari show status
```

Then confirm the TCCS Core ARI WebSocket is connected before originating a test call.

## Live validation

Use a registered test subscriber as the target. First verify Core readiness with ARI enabled. Then create an individual call through the Core API.

Expected sequence:

1. Core persists the call and participants.
2. Core sends the ARI originate request.
3. Asterisk creates the PJSIP channel.
4. When answered, Asterisk places the channel in `tccs-core` Stasis and emits `StasisStart`.
5. Core correlates the event using `outbound,<source>,<target>`, binds the Asterisk channel ID to the persistent participant, and moves the call to `RINGING`/`CONNECTED` as subsequent events arrive.
6. When the target hangs up, Asterisk emits `StasisEnd` and Core persists the terminal `ENDED` state.

If ARI is enabled but the WebSocket application is not registered, do not treat a successful HTTP originate response as a complete TCCS call. The ARI event stream is part of the control path and must be verified.

## Important distinction: WSS vs ARI WebSocket

`transport-wss` in `tccs-pjsip.conf` is the SIP-over-WebSocket transport for browser/WebRTC SIP clients. ARI's event WebSocket is a separate Asterisk control interface under `/ari/events`. Both may use TLS, but they serve different protocols and must not be conflated.

## Rollback

Set `TCCS_ASTERISK_ARI_ENABLED=false` to return Core API calls to persistence-only mode. Do not remove `transport-wss` or the existing HTTP/TLS configuration when rolling back the ARI call adapter.
