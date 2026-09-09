from __future__ import annotations

import os

import httpx


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


core_client = TCCSCoreClient()
