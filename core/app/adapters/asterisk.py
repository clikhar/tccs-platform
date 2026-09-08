from __future__ import annotations

import asyncio
from typing import Protocol

import httpx


class AsteriskClient(Protocol):
    """Interface between TCCS call control and Asterisk."""

    async def originate(self, source: str, target: str, call_id: str) -> str: ...

    async def originate_participant(self, call_id: str, participant: str) -> str: ...

    async def bridge_call(self, call_id: str, participant: str | None = None) -> None: ...

    async def cleanup_call(self, call_id: str, channel_id: str) -> None: ...

    async def hangup(self, call_id: str) -> None: ...

    async def mute(self, call_id: str, participant: str) -> None: ...

    async def unmute(self, call_id: str, participant: str) -> None: ...

    async def remove_participant(self, call_id: str, participant: str) -> None: ...


class AsteriskAdapterError(RuntimeError):
    """Raised when Asterisk rejects an ARI operation."""


class AsteriskHttpClient:
    """Small async ARI client used by the TCCS adapter boundary."""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        app: str = "tccs-core",
        timeout: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth = (username, password)
        self._app = app
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None
        self._participants: dict[str, dict[str, str]] = {}
        self._bridges: dict[str, str] = {}
        self._bridged_channels: dict[str, set[str]] = {}
        self._bridge_locks: dict[str, asyncio.Lock] = {}

    async def originate(self, source: str, target: str, call_id: str) -> str:
        """Originate the caller leg; targets are originated after the caller enters Stasis."""
        call_key = str(call_id)
        endpoint = source if "/" in source else f"PJSIP/{source}"
        response = await self._request(
            "POST",
            "/channels",
            params={
                "endpoint": endpoint,
                "app": self._app,
                "appArgs": f"source,{call_key},{target}",
            },
        )
        channel_id = response.json()["id"]
        self._participants.setdefault(call_key, {})[source] = channel_id
        return channel_id

    async def originate_participant(self, call_id: str, participant: str) -> str:
        call_key = str(call_id)
        channels = self._participants.get(call_key, {})
        if not channels:
            raise AsteriskAdapterError(f"no caller channel mapped for call {call_key!r}")
        existing = channels.get(participant)
        if existing:
            return existing
        endpoint = participant if "/" in participant else f"PJSIP/{participant}"
        response = await self._request(
            "POST",
            "/channels",
            params={
                "endpoint": endpoint,
                "app": self._app,
                "appArgs": f"callee,{call_key},{participant}",
            },
        )
        channel_id = response.json()["id"]
        self._participants.setdefault(call_key, {})[participant] = channel_id
        return channel_id

    async def bridge_call(self, call_id: str, participant: str | None = None) -> None:
        """Bridge the source and a participant after that participant enters Stasis.

        When participant is provided, the source is added only if it is not already
        in the bridge; later participants are added one at a time. This prevents a
        group call from attempting to add a concurrently originating leg that has
        not entered Stasis yet.
        """
        call_key = str(call_id)
        lock = self._bridge_locks.setdefault(call_key, asyncio.Lock())
        async with lock:
            channels = self._participants.get(call_key, {})
            if len(channels) < 2:
                raise AsteriskAdapterError(f"call {call_key!r} does not have two channels to bridge")

            bridge_id = self._bridges.get(call_key)
            if bridge_id is None:
                bridge_id = f"tccs-{call_key}"
                try:
                    await self._request("POST", "/bridges", params={"type": "mixing", "bridgeId": bridge_id})
                except AsteriskAdapterError as exc:
                    if "HTTP 409" not in str(exc):
                        raise
                self._bridges[call_key] = bridge_id
                self._bridged_channels[call_key] = set()

            try:
                if participant is None:
                    pending = [
                        channel_id
                        for channel_id in channels.values()
                        if channel_id not in self._bridged_channels[call_key]
                    ]
                else:
                    try:
                        participant_channel = channels[participant]
                    except KeyError as exc:
                        raise AsteriskAdapterError(
                            f"participant {participant!r} is not mapped to call {call_key!r}"
                        ) from exc

                    source_channel = next(iter(channels.values()))
                    requested = [source_channel, participant_channel]
                    pending = [
                        channel_id
                        for channel_id in requested
                        if channel_id not in self._bridged_channels[call_key]
                    ]

                if pending:
                    await self._request(
                        "POST",
                        f"/bridges/{bridge_id}/addChannel",
                        params={"channel": ",".join(dict.fromkeys(pending))},
                    )
                    self._bridged_channels[call_key].update(pending)
            except Exception:
                if call_key not in self._bridged_channels or not self._bridged_channels[call_key]:
                    self._bridges.pop(call_key, None)
                    self._bridged_channels.pop(call_key, None)
                    await self._safe_request("DELETE", f"/bridges/{bridge_id}")
                raise

    async def cleanup_call(self, call_id: str, channel_id: str) -> None:
        """Handle one StasisEnd without dropping a surviving conference.

        A conference stays active while at least two mapped channels remain. Only
        when the last conference leg has ended (or an individual call is reduced to
        one leg) do we tear down the bridge and remaining channel state.
        """
        call_key = str(call_id)
        lock = self._bridge_locks.setdefault(call_key, asyncio.Lock())
        async with lock:
            channels = self._participants.get(call_key, {})

            # _participants is keyed by extension/participant name, while StasisEnd
            # gives us the Asterisk channel identity. Remove the matching leg by
            # value, not by dictionary key.
            participant_key = next(
                (participant for participant, mapped_channel in channels.items() if mapped_channel == channel_id),
                None,
            )
            if participant_key is not None:
                channels.pop(participant_key, None)

            self._bridged_channels.get(call_key, set()).discard(channel_id)

            if len(channels) >= 2:
                return

            bridge_id = self._bridges.pop(call_key, None)
            self._bridged_channels.pop(call_key, None)
            remaining_channels = list(channels.values())
            self._participants.pop(call_key, None)
            self._bridge_locks.pop(call_key, None)

        if bridge_id:
            await self._safe_request("DELETE", f"/bridges/{bridge_id}")
        for other_channel_id in remaining_channels:
            await self._safe_request("DELETE", f"/channels/{other_channel_id}")

    async def hangup(self, call_id: str) -> None:
        channel_id = call_id
        await self._request("DELETE", f"/channels/{channel_id}")
        self._participants.pop(str(call_id), None)
        self._bridges.pop(str(call_id), None)
        self._bridged_channels.pop(str(call_id), None)
        self._bridge_locks.pop(str(call_id), None)

    async def mute(self, call_id: str, participant: str) -> None:
        channel_id = self._channel_for(call_id, participant)
        await self._request("POST", f"/channels/{channel_id}/mute", params={"direction": "both"})

    async def unmute(self, call_id: str, participant: str) -> None:
        channel_id = self._channel_for(call_id, participant)
        await self._request("DELETE", f"/channels/{channel_id}/mute", params={"direction": "both"})

    async def remove_participant(self, call_id: str, participant: str) -> None:
        channel_id = self._channel_for(call_id, participant)
        await self._request("DELETE", f"/channels/{channel_id}")
        self._participants.get(str(call_id), {}).pop(participant, None)
        self._bridged_channels.get(str(call_id), set()).discard(channel_id)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _channel_for(self, call_id: str, participant: str) -> str:
        call_key = str(call_id)
        try:
            return self._participants[call_key][participant]
        except KeyError as exc:
            raise AsteriskAdapterError(
                f"participant {participant!r} is not mapped to call {call_key!r}"
            ) from exc

    async def _safe_request(self, method: str, path: str, **kwargs: object) -> None:
        try:
            await self._request(method, path, **kwargs)
        except AsteriskAdapterError as exc:
            if "HTTP 404" not in str(exc):
                raise

    async def _request(self, method: str, path: str, **kwargs: object) -> httpx.Response:
        try:
            response = await self._client.request(
                method,
                f"{self._base_url}{path}",
                auth=self._auth,
                **kwargs,
            )
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:500]
            raise AsteriskAdapterError(
                f"Asterisk ARI {method} {path} failed with HTTP "
                f"{exc.response.status_code}: {detail}"
            ) from exc
        except httpx.HTTPError as exc:
            raise AsteriskAdapterError(f"Asterisk ARI request failed: {exc}") from exc
