import pytest

from app.services.call_engine import CallEngine, CallMode


def test_individual_call_plan() -> None:
    plan = CallEngine().individual("1001", "2001")
    assert plan.mode == CallMode.INDIVIDUAL
    assert plan.targets == ("2001",)
    assert plan.controller_persistent is False


def test_group_call_plan_is_persistent_and_deduplicates_targets() -> None:
    plan = CallEngine().group("1001", ["2001", "2002", "2001"], "conf-1001")
    assert plan.mode == CallMode.GROUP
    assert plan.targets == ("2001", "2002")
    assert plan.conference_id == "conf-1001"
    assert plan.controller_persistent is True


def test_general_call_plan() -> None:
    plan = CallEngine().general("1001", ["2001", "2002"], "general-1001")
    assert plan.mode == CallMode.GENERAL
    assert plan.controller_persistent is True


@pytest.mark.parametrize("source,target", [("", "2001"), ("1001", ""), ("10A1", "2001"), ("1", "2001")])
def test_invalid_extensions_are_rejected(source: str, target: str) -> None:
    with pytest.raises(ValueError):
        CallEngine().individual(source, target)


def test_empty_group_is_rejected() -> None:
    with pytest.raises(ValueError):
        CallEngine().group("1001", [], "conf-1001")
