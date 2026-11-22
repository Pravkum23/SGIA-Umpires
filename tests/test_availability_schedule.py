from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, text

from src.availability_schedule import open_due_availability
from src.db import initialize, rows
from src.fixtures import add_fixture
from src.tournaments import create_tournament


def database(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    initialize(engine)
    create_tournament(engine, "SHL5")
    create_tournament(engine, "Other")
    monkeypatch.setattr("src.weekly.singapore_now", lambda: datetime(2026, 11, 20, 20))
    for day, league in [(24, "SHL5"), (27, "SHL5"), (28, "SHL5"), (29, "SHL5"), (23, "SHL5"), (24, "Other")]:
        add_fixture(engine, f"{day}-Nov-2026", "7:00 PM", f"Team {day}", "Opponent", tournament=league)
    return engine


def test_opening_boundaries_and_repeat_runs(monkeypatch):
    engine = database(monkeypatch)
    assert open_due_availability(engine, datetime(2026, 11, 20, 20, 59)) == 0
    assert open_due_availability(engine, datetime(2026, 11, 20, 21)) == 2
    assert open_due_availability(engine, datetime(2026, 11, 20, 21)) == 0
    assert open_due_availability(engine, datetime(2026, 11, 23, 17, 59)) == 0
    assert open_due_availability(engine, datetime(2026, 11, 23, 10, tzinfo=timezone.utc)) == 2
    assert len(rows(engine, "SELECT id FROM fixtures WHERE poll_state='OPEN'")) == 4


def test_closed_frozen_published_and_late_imports(monkeypatch):
    engine = database(monkeypatch)
    assert open_due_availability(engine, datetime(2026, 11, 23, 18)) == 4
    with engine.begin() as c:
        for day, state in [(24, "CLOSED"), (27, "FROZEN"), (28, "PUBLISHED")]:
            c.execute(text("UPDATE fixtures SET poll_state=:state,availability_open=false WHERE home_team=:team AND tournament='SHL5'"),
                      {"state": state, "team": f"Team {day}"})
    assert open_due_availability(engine, datetime(2026, 11, 23, 19)) == 0
    add_fixture(engine, "25-Nov-2026", "7:00 PM", "Late import", "Opponent", tournament="SHL5")
    assert open_due_availability(engine, datetime(2026, 11, 23, 19)) == 1
    assert rows(engine, "SELECT poll_state FROM fixtures WHERE home_team='Team 24' AND tournament='SHL5'")[0]["poll_state"] == "CLOSED"


def test_catchup_skips_past_and_cancelled_matches(monkeypatch):
    engine = database(monkeypatch)
    with engine.begin() as c:
        c.execute(text("UPDATE fixtures SET match_status='CANCELLED' WHERE home_team='Team 28'"))
    assert open_due_availability(engine, datetime(2026, 11, 28, 20)) == 1
    assert open_due_availability(engine, datetime(2026, 11, 30, 9)) == 0
