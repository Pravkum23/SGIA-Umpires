from datetime import date, timedelta
import pytest
from sqlalchemy import create_engine, text

from src.db import initialize, rows
from src.weekly import close_week, enable_week, reopen_selected, week_bounds
from src.tournaments import create_tournament
from src.fixtures import add_fixture
from src.board import allocation_board, save_allocation_board, allocation_review
from src.allocation import allocation_message, propose
from src.allocation_scope import current_allocation_summary, published_allocation_cycles
from src.live_poll import live_poll_monitor


def setup_week():
    engine = create_engine("sqlite:///:memory:")
    initialize(engine)
    create_tournament(engine, "SHL5")
    start, end = week_bounds(date(2099, 1, 7))
    for offset, team, tournament in [(1, "Tuesday", "SHL5"), (5, "Saturday", "SHL5"), (6, "Sunday", "SHL5"), (2, "Old league", "SHL4")]:
        add_fixture(engine, (start + timedelta(days=offset)).strftime("%d-%b-%Y"), "7:00 PM", team, "Opponent", tournament=tournament)
    fixtures = rows(engine, "SELECT id,home_team FROM fixtures WHERE starts_at>=:start", {"start": start})
    ids = {f["home_team"]: f["id"] for f in fixtures}
    return engine, start, end, ids


def allocate(engine, ids):
    names = [p["name"] for p in rows(engine, "SELECT name FROM people ORDER BY id LIMIT 3")]
    save_allocation_board(engine, [{"fixture_id": fid, "Umpire 1": names[0], "Umpire 2": names[1], "Scorer": names[2]} for fid in ids])


def state(engine, fid):
    return rows(engine, "SELECT poll_state FROM fixtures WHERE id=:id", {"id": fid})[0]["poll_state"]


def test_selected_publication_and_reopening_preserve_other_matches():
    engine, start, end, ids = setup_week()
    enable_week(engine, "SHL5", start, end, [ids["Tuesday"]])
    assert state(engine, ids["Saturday"]) == "CLOSED"
    enable_week(engine, "SHL5", start, end)
    enable_week(engine, "SHL4", start, end)
    allocate(engine, [ids["Tuesday"], ids["Saturday"], ids["Sunday"], ids["Old league"]])
    before = rows(engine, "SELECT * FROM assignments ORDER BY fixture_id,role")
    close_week(engine, "SHL5", start, end, [ids["Tuesday"], ids["Old league"]])
    assert state(engine, ids["Tuesday"]) == "PUBLISHED"
    assert state(engine, ids["Saturday"]) == state(engine, ids["Old league"]) == "OPEN"
    assert {r["fixture_id"] for r in allocation_board(engine, tournament="SHL5")} == {ids["Saturday"], ids["Sunday"]}
    history = published_allocation_cycles(engine, tournament="SHL5")
    assert history[0]["match_count"] == 1
    assert len(allocation_board(engine, cycle_id=history[0]["id"], tournament="SHL5", published_only=True)) == 1
    close_week(engine, "SHL5", start, end, [ids["Saturday"], ids["Sunday"]])
    reopen_selected(engine, "SHL5", start, end, [ids["Saturday"], ids["Sunday"]])
    assert state(engine, ids["Tuesday"]) == "PUBLISHED"
    assert state(engine, ids["Saturday"]) == state(engine, ids["Sunday"]) == "FROZEN"
    assert rows(engine, "SELECT * FROM assignments ORDER BY fixture_id,role") == before
    close_week(engine, "SHL5", start, end, [ids["Saturday"]])
    assert state(engine, ids["Sunday"]) == "FROZEN"
    close_week(engine, "SHL5", start, end, [])
    assert state(engine, ids["Sunday"]) == "FROZEN"


def test_tournament_views_and_suggestions_are_scoped():
    engine, start, end, ids = setup_week()
    enable_week(engine, "SHL4", start, end)
    allocate(engine, [ids["Old league"]])
    assert allocation_board(engine, tournament="SHL5") == []
    assert current_allocation_summary(engine, tournament="SHL5") is None
    enable_week(engine, "SHL5", start, end)
    with engine.begin() as c:
        for fid in ids.values():
            c.execute(text("INSERT INTO availability(person_id,fixture_id) SELECT id,:id FROM people"), {"id": fid})
    propose(engine, week=("SHL5", start, end), fixture_ids=[ids["Tuesday"]])
    assert not rows(engine, "SELECT 1 FROM assignments WHERE fixture_id=:id", {"id": ids["Saturday"]})
    assert {r["fixture_id"] for r in allocation_review(engine, tournament="SHL5")} == {ids["Tuesday"]}
    assert "Old league" not in allocation_message(engine, tournament="SHL5")
    assert live_poll_monitor(engine, tournament="SHL5")["open_matches"] == 3
    assert live_poll_monitor(engine, tournament="SHL4")["open_matches"] == 1


def test_incomplete_selected_allocation_rolls_back():
    engine, start, end, ids = setup_week()
    enable_week(engine, "SHL5", start, end)
    allocate(engine, [ids["Tuesday"]])
    with pytest.raises(ValueError):
        close_week(engine, "SHL5", start, end, [ids["Tuesday"], ids["Saturday"]])
    assert state(engine, ids["Tuesday"]) == "OPEN"
    close_week(engine, "SHL5", start, end, [ids["Tuesday"]])
    assert state(engine, ids["Tuesday"]) == "PUBLISHED"


def test_weekly_admin_selection_and_tournament_switch(monkeypatch, tmp_path):
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    db_path = tmp_path / "weekly-ui.db"
    engine = create_engine(f"sqlite:///{db_path}")
    initialize(engine)
    create_tournament(engine, "SHL5")
    start, end = week_bounds(date.today() + timedelta(days=7))
    add_fixture(engine, (start + timedelta(days=1)).strftime("%d-%b-%Y"), "7:00 PM", "Tuesday", "Opponent", tournament="SHL5")
    add_fixture(engine, (start + timedelta(days=5)).strftime("%d-%b-%Y"), "7:00 PM", "Saturday", "Opponent", tournament="SHL5")
    monkeypatch.setenv("SGIA_SQLITE_PATH", str(db_path))
    monkeypatch.setenv("SGIA_ADMIN_PIN", "test-only")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    page = AppTest.from_file(Path(__file__).parents[1] / "app.py")
    page.query_params["admin"] = "1"
    page.session_state["admin_ok"] = True
    page.run(timeout=30)
    page.selectbox(key="admin_tournament").set_value("SHL5").run(timeout=30)
    page.date_input(key="weekly_date").set_value(start.date()).run(timeout=30)
    next(r for r in page.radio if r.label == "Choose matches").set_value("Tuesday–Friday").run(timeout=30)
    assert not list(page.exception)
    assert len(page.multiselect[0].value) == 1
    next(b for b in page.button if b.label == "Enable selected matches").click().run(timeout=30)
    assert not list(page.exception)
    assert rows(engine, "SELECT home_team FROM fixtures WHERE tournament='SHL5' AND poll_state='OPEN'") == [{"home_team": "Tuesday"}]
    page.radio(key="admin_navigation").set_value("More tools").run(timeout=30)
    next(s for s in page.selectbox if s.label == "Tool").set_value("Quick Admin").run(timeout=30)
    assert not list(page.exception)
    page.selectbox(key="admin_tournament").set_value("SHL4").run(timeout=30)
    assert not list(page.exception)
