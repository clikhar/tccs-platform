from app.db_models import Endpoint
from app.services.provisioning import build_pjsip_endpoint, render_pjsip_endpoint


def endpoint(enabled: bool = True) -> Endpoint:
    return Endpoint(
        device_id=__import__("uuid").uuid4(),
        endpoint_identity="controller-1001",
        endpoint_type="controller",
        sip_username="1001",
        enabled=enabled,
    )


def test_provisioning_defaults_to_wss() -> None:
    config = build_pjsip_endpoint(endpoint(), "1001")
    assert config.transport == "transport-wss"
    rendered = render_pjsip_endpoint(config)
    assert "transport=transport-wss" in rendered
    assert "password=${TCCS_SIP_PASSWORD}" in rendered
    assert "CHANGE_ME" not in rendered


def test_provisioning_can_use_tls_for_non_browser_endpoints() -> None:
    config = build_pjsip_endpoint(endpoint(), "1001", secure_websocket=False)
    assert config.transport == "transport-tls"


def test_disabled_endpoint_cannot_be_provisioned() -> None:
    try:
        build_pjsip_endpoint(endpoint(enabled=False), "1001")
    except ValueError as exc:
        assert str(exc) == "cannot provision a disabled endpoint"
    else:
        raise AssertionError("disabled endpoint was provisioned")
