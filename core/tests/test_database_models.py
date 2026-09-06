import uuid

from app.db_models import (
    AuditLog,
    Call,
    CallEvent,
    CallGroup,
    CallParticipant,
    ControlSection,
    Device,
    Endpoint,
    Extension,
    Site,
)


def test_initial_schema_contains_required_tables() -> None:
    tables = {
        Site.__tablename__,
        ControlSection.__tablename__,
        Device.__tablename__,
        Endpoint.__tablename__,
        Extension.__tablename__,
        CallGroup.__tablename__,
        Call.__tablename__,
        CallParticipant.__tablename__,
        CallEvent.__tablename__,
        AuditLog.__tablename__,
    }
    assert tables == {
        "sites",
        "control_sections",
        "devices",
        "endpoints",
        "extensions",
        "groups",
        "calls",
        "call_participants",
        "call_events",
        "audit_logs",
    }


def test_primary_keys_are_uuid_columns() -> None:
    for model in (
        Site,
        ControlSection,
        Device,
        Endpoint,
        Extension,
        CallGroup,
        Call,
        CallParticipant,
        CallEvent,
        AuditLog,
    ):
        value = model.id.default.arg
        assert isinstance(value(), uuid.UUID)


def test_endpoint_identity_is_distinct_from_extension_number() -> None:
    assert Endpoint.endpoint_identity.name == "endpoint_identity"
    assert Extension.number.name == "number"
    assert Endpoint.endpoint_identity is not Extension.number
