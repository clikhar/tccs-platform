from types import SimpleNamespace

import pytest

from app.main import controller_groups


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)


class FakeSession:
    def __init__(self, rows):
        self.rows = rows
        self.statement = None
        self.params = None

    async def execute(self, statement, params=None):
        self.statement = statement
        self.params = params
        return FakeResult(self.rows)


def row(**values):
    return SimpleNamespace(_mapping=values)


@pytest.mark.asyncio
async def test_controller_groups_returns_members_for_admin():
    db = FakeSession(
        [
            row(
                id=1,
                code="G1",
                name="Group One",
                section_id=2,
                member_station_id=10,
                station_number="101",
                station_name="Station 101",
                sip_extension="1001",
            ),
            row(
                id=1,
                code="G1",
                name="Group One",
                section_id=2,
                member_station_id=11,
                station_number="102",
                station_name="Station 102",
                sip_extension="1002",
            ),
            row(
                id=2,
                code="G2",
                name="Group Two",
                section_id=3,
                member_station_id=None,
                station_number=None,
                station_name=None,
                sip_extension=None,
            ),
        ]
    )

    result = await controller_groups(db=db, user={"role": "ADMIN"})

    assert result == [
        {
            "id": 1,
            "code": "G1",
            "name": "Group One",
            "section_id": 2,
            "members": [
                {
                    "id": 10,
                    "station_number": "101",
                    "name": "Station 101",
                    "sip_extension": "1001",
                },
                {
                    "id": 11,
                    "station_number": "102",
                    "name": "Station 102",
                    "sip_extension": "1002",
                },
            ],
        },
        {
            "id": 2,
            "code": "G2",
            "name": "Group Two",
            "section_id": 3,
            "members": [],
        },
    ]
    assert db.params == {}
    sql = str(db.statement)
    assert "g.id" in sql
    assert "m.station_group_id = g.id" in sql
    assert "g.station_group_id" not in sql


@pytest.mark.asyncio
async def test_controller_groups_scopes_controller_to_assigned_section():
    db = FakeSession([])

    result = await controller_groups(
        db=db,
        user={"role": "CONTROLLER", "controller_id": 7},
    )

    assert result == []
    assert db.params == {"controller_id": 7}
    sql = str(db.statement)
    assert "g.section_id" in sql
    assert "controllers" in sql
