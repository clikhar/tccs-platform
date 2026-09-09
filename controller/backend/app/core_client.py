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

    async def create_call(
        self,
        *,
        source: str,
        target: str,
        section_id: str | None = None,
        mode: str = "individual",
    ) -> dict:
        if not self.enabled:
            raise CoreClientError("TCCS Core integration is not configured")
        payload = {
            "source": str(source).strip(),
            "target": str(target).strip(),
            "mode": str(mode).strip() or "individual",
        }
        if section_id is not None:
            payload["section_id"] = str(section_id)
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(f"{self.base_url}/api/v1/calls", json=payload)
        except httpx.HTTPError as exc:
            raise CoreClientError(f"TCCS Core unavailable: {exc}") from exc
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail")
            except Exception:
                detail = None
            raise CoreClientError(detail or f"TCCS Core returned HTTP {response.status_code}")
        return response.json()


core_client = TCCSCoreClient()
