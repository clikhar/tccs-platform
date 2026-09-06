from dataclasses import dataclass

from ..db_models import Endpoint


@dataclass(frozen=True)
class PjsipEndpointConfig:
    """Validated PJSIP configuration derived from a TCCS endpoint."""

    identity: str
    username: str
    endpoint_type: str
    transport: str
    extension: str


def build_pjsip_endpoint(endpoint: Endpoint, extension: str, secure_websocket: bool = True) -> PjsipEndpointConfig:
    """Build safe, deterministic PJSIP provisioning data.

    Credentials are intentionally excluded. Secrets must come from a protected
    deployment secret store and never from generated repository files.
    """
    if not endpoint.enabled:
        raise ValueError("cannot provision a disabled endpoint")
    if not extension.isdigit() or not 2 <= len(extension) <= 8:
        raise ValueError("extension must contain 2-8 digits")

    transport = "transport-wss" if secure_websocket else "transport-tls"
    return PjsipEndpointConfig(
        identity=endpoint.endpoint_identity,
        username=endpoint.sip_username,
        endpoint_type=endpoint.endpoint_type,
        transport=transport,
        extension=extension,
    )


def render_pjsip_endpoint(config: PjsipEndpointConfig) -> str:
    """Render endpoint configuration without embedding a password."""
    return "\n".join(
        [
            f"[{config.identity}]",
            "type=endpoint",
            f"transport={config.transport}",
            f"auth={config.identity}-auth",
            f"aors={config.identity}-aor",
            "context=tccs-endpoints",
            "direct_media=no",
            "disallow=all",
            "allow=opus,alaw,ulaw",
            "dtmf_mode=rfc4733",
            "rtp_symmetric=yes",
            "force_rport=yes",
            "rewrite_contact=yes",
            "",
            f"[{config.identity}-auth]",
            "type=auth",
            "auth_type=userpass",
            f"username={config.username}",
            "password=${TCCS_SIP_PASSWORD}",
            "",
            f"[{config.identity}-aor]",
            "type=aor",
            "max_contacts=1",
            "remove_existing=yes",
        ]
    )
