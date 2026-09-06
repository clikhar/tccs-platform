import pytest

from app.services.conference import Conference, ParticipantRole


def test_controller_is_always_connected_and_cannot_be_removed() -> None:
    conference = Conference("conf-1", "1001")
    assert conference.participants["1001"].role is ParticipantRole.CONTROLLER
    assert conference.participants["1001"].connected is True
    with pytest.raises(ValueError, match="controller cannot"):
        conference.remove("1001")


def test_way_station_joins_muted_then_can_be_unmuted() -> None:
    conference = Conference("conf-1", "1001")
    conference.add_participant("2001", muted=True)
    conference.connect("2001")
    assert conference.participants["2001"].muted is True
    assert conference.participants["2001"].connected is True
    conference.unmute("2001")
    assert conference.participants["2001"].muted is False


def test_participant_can_be_muted_and_removed() -> None:
    conference = Conference("conf-1", "1001")
    conference.add_participant("2001", muted=False)
    conference.mute("2001")
    assert conference.participants["2001"].muted is True
    removed = conference.remove("2001")
    assert removed.extension == "2001"
    assert "2001" not in conference.participants
