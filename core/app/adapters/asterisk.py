from __future__ import annotations

from typing import Protocol

import httpx


class AsteriskClient(Protocol):
    """Interface between TCCS call control and Asterisk."""

    async def originate(self, source: str, target: str) -> str: ...

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

    async def originate(self, source: str, target: str) -> str:
        endpoint = target if "/" in target else f"PJSIP/{target}"
        response = await self._request(
            "POST",
            "/channels",
            params={
                "endpoint": endpoint,
                "app": self._app,
                "appArgs": f"outbound,{source},{target}",
            },
        )
        channel_id = response.json()["id"]
        self._participants.setdefault(channel_id, {})[target] = channel_id
        return channel_id

    async def hangup(self, call_id: str) -> None:
        await self._request("DELETE", f"/channels/{call_id}")
        self._participants.pop(call_id, None)

    async def mute(self, call_id: str, participant: str) -> None:
        channel_id = self._channel_for(call_id, participant)
        await self._request("POST", f"/channels/{channel_id}/mute", params={"direction": "both"})

    async def unmute(self, call_id: str, participant: str) -> None:
        channel_id = self._channel_for(call_id, participant)
        await self._request("DELETE", f"/channels/{channel_id}/mute", params={"direction": "both"})

    async def remove_participant(self, call_id: str, participant: str) -> None:
        channel_id = self._channel_for(call_id, participant)
        await self._request("DELETE", f"/channels/{channel_id}")
        self._participants.get(call_id, {}).pop(participant, None)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _channel_for(self, call_id: str, participant: str) -> str:
        try:
            return self._participants[call_id][participant]
        except KeyError as exc:
            raise AsteriskAdapterError(
                f"participant {participant!r} is not mapped to call {call_id!r}"
            ) from exc

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
