from __future__ import annotations

import os
from pathlib import Path

import httpx
from dotenv import load_dotenv


# Load the Controller-local environment file when the service is started
# directly (for example with `uvicorn`). Deployment environments may still
# provide TCCS_CORE_URL through the process environment; those values take
# precedence over .env values.
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)


class CoreClientError(RuntimeError):
    """Raised when TCCS Core rejects or cannot process a request."""


class TCCSCoreClient:
    """HTTP client for the Stage 1 TCCS Core service."""

    def __init__(self, base_url: str | None = None, timeout: float = 10.0) -> None:
        self.base_url = (base_url or os.getenv("TCCS_CORE_URL") or "").rstrip("/")
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.base_url)

    async def _post(self, path: str, payload: dict) -> dict:
        if not self.enabled:
            raise CoreClientError("TCCS Core integration is not configured")
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(f"{self.base_url}{path}", json=payload)
        except httpx.HTTPError as exc:
            raise CoreClientError(f"TCCS Core unavailable: {exc}") from exc
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail")
            except Exception:
                detail = None
            raise CoreClientError(detail or f"TCCS Core returned HTTP {response.status_code}")
        return response.json()

    async def create_call(
        self,
        *,
        source: str,
        target: str,
        section_id: str | None = None,
        mode: str = "individual",
    ) -> dict:
        payload = {
            "source": str(source).strip(),
            "target": str(target).strip(),
            "mode": str(mode).strip() or "individual",
        }
        if section_id is not None:
            payload["section_id"] = str(section_id)
        return await self._post("/api/v1/calls", payload)

    async def create_conference(
        self,
        *,
        source: str,
        targets: list[str],
        section_id: str | None = None,
        mode: str = "group",
    ) -> dict:
        payload = {
            "source": str(source).strip(),
            "targets": [str(target).strip() for target in targets],
            "mode": str(mode).strip() or "group",
        }
        if section_id is not None:
            payload["section_id"] = str(section_id)
        endpoint = "/api/v1/general-calls" if str(mode).strip().lower() == "general" else "/api/v1/group-calls"
        return await self._post(endpoint, payload)

    async def active_call_for_participant(self, extension: str) -> str:
        if not self.enabled:
            raise CoreClientError("TCCS Core integration is not configured")
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    f"{self.base_url}/api/v1/active-calls/participant/{str(extension).strip()}"
                )
        except httpx.HTTPError as exc:
            raise CoreClientError(f"TCCS Core unavailable: {exc}") from exc
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail")
            except Exception:
                detail = None
            raise CoreClientError(detail or f"TCCS Core returned HTTP {response.status_code}")
        return str(response.json()["call_id"])

    async def active_call_for_source(self, extension: str) -> tuple[str, str | None]:
        if not self.enabled:
            raise CoreClientError("TCCS Core integration is not configured")
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    f"{self.base_url}/api/v1/active-calls/source/{str(extension).strip()}"
                )
        except httpx.HTTPError as exc:
            raise CoreClientError(f"TCCS Core unavailable: {exc}") from exc
        if response.status_code == 404:
            raise CoreClientError("no active call")
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail")
            except Exception:
                detail = None
            raise CoreClientError(detail or f"TCCS Core returned HTTP {response.status_code}")
        payload = response.json()
        return str(payload["call_id"]), payload.get("conference_id")

    async def active_conference_for_source(self, extension: str) -> str:
        if not self.enabled:
            raise CoreClientError("TCCS Core integration is not configured")
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    f"{self.base_url}/api/v1/active-conferences/source/{str(extension).strip()}"
                )
        except httpx.HTTPError as exc:
            raise CoreClientError(f"TCCS Core unavailable: {exc}") from exc
        if response.status_code == 404:
            raise CoreClientError("no active conference")
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail")
            except Exception:
                detail = None
            raise CoreClientError(detail or f"TCCS Core returned HTTP {response.status_code}")
        return str(response.json()["call_id"])

    async def participant_action(
        self,
        *,
        call_id: str,
        extension: str,
        action: str,
        actor: str,
    ) -> dict:
        action_name = str(action).strip().lower()
        if action_name not in {"connect", "mute", "unmute", "disconnect"}:
            raise ValueError(f"Unsupported participant action: {action}")
        endpoint_action = "disconnect" if action_name == "disconnect" else action_name
        return await self._post(
            f"/api/v1/calls/{str(call_id).strip()}/participants/{str(extension).strip()}/{endpoint_action}",
            {"actor": str(actor).strip()},
        )


core_client = TCCSCoreClient()
