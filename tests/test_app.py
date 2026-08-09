from pathlib import Path

from sqlalchemy import create_engine, text
from streamlit.testing.v1 import AppTest

from src.allocation import allocation_message, propose
from src.db import initialize, rows, save_vote
from src.routing import is_admin_request


def db():
    engine = create_engine("sqlite:///:memory:")
    initialize(engine)
    return engine


def open_and_vote_all(engine, fixture_ids):
    with engine.begin() as connection:
        for fixture_id in fixture_ids:
            connection.execute(text("UPDATE fixtures SET availability_open=true WHERE id=:id"), {"id": fixture_id})
            connection.execute(text("INSERT INTO availability(person_id,fixture_id) SELECT id,:id FROM people"), {"id": fixture_id})


def test_seed_is_idempotent_and_exact():
    engine = db()
    initialize(engine)
    assert rows(engine, "SELECT COUNT(*) n FROM fixtures")[0]["n"] == 29
    assert rows(engine, "SELECT COUNT(*) n FROM people")[0]["n"] == 16


def test_vote_can_change_and_closed_slots_are_not_public():
    engine = db()
    with engine.begin() as connection:
        connection.execute(text("UPDATE fixtures SET availability_open=true WHERE id IN (1,2)"))
    save_vote(engine, 1, [1, 2])
    save_vote(engine, 1, [2])
    assert rows(engine, "SELECT fixture_id FROM availability WHERE person_id=1") == [{"fixture_id": 2}]
    with engine.begin() as connection:
        connection.execute(text("UPDATE fixtures SET availability_open=false WHERE id=2"))
    assert rows(engine, "SELECT id FROM fixtures WHERE availability_open=true") == [{"id": 1}]


def test_closed_poll_votes_can_still_be_allocated():
    engine = db()
    open_and_vote_all(engine, [1])
    with engine.begin() as connection:
        connection.execute(text("UPDATE fixtures SET availability_open=false WHERE id=1"))
    propose(engine)
    assert rows(engine, "SELECT COUNT(*) n FROM assignments WHERE fixture_id=1")[0]["n"] == 2


def test_regenerate_changes_only_unconfirmed_and_preserves_confirmed_exactly():
    engine = db()
    open_and_vote_all(engine, [1, 2])
    propose(engine)
    before = rows(engine, "SELECT fixture_id,role,person_id,confirmed FROM assignments ORDER BY fixture_id,role")
    confirmed = before[0]
    unconfirmed = before[-1]
    with engine.begin() as connection:
        connection.execute(text("UPDATE assignments SET confirmed=true WHERE fixture_id=:fixture_id AND role=:role"), confirmed)
        connection.execute(text("DELETE FROM availability WHERE fixture_id=:fixture AND person_id=:person"), {"fixture": unconfirmed["fixture_id"], "person": unconfirmed["person_id"]})
        connection.execute(text("UPDATE fixtures SET availability_open=false WHERE id IN (1,2)"))
    propose(engine, regenerate_unconfirmed=True)
    kept = rows(engine, "SELECT person_id,confirmed FROM assignments WHERE fixture_id=:fixture_id AND role=:role", confirmed)[0]
    refreshed = rows(engine, "SELECT person_id,confirmed FROM assignments WHERE fixture_id=:fixture_id AND role=:role", unconfirmed)[0]
    assert kept == {"person_id": confirmed["person_id"], "confirmed": 1}
    assert refreshed["person_id"] != unconfirmed["person_id"]
    assert refreshed["confirmed"] == 0


def test_special_role_rules_and_praveen_scorer_preference():
    engine = db()
    open_and_vote_all(engine, [1, 2])
    propose(engine)
    assert not rows(engine, "SELECT 1 FROM assignments a JOIN people p ON p.id=a.person_id WHERE a.role='scorer' AND p.name IN ('Velu','Shree')")
    praveen = rows(engine, "SELECT a.role FROM assignments a JOIN people p ON p.id=a.person_id WHERE p.name='Praveen'")
    assert praveen and all(item["role"] == "scorer" for item in praveen)


def test_weekend_pair_is_back_to_back_opposite_roles_without_third_duty():
    engine = db()
    open_and_vote_all(engine, [1, 2, 3])
    propose(engine)
    paired = rows(engine, """
        SELECT p.name FROM assignments first
        JOIN assignments second ON second.fixture_id=2 AND second.role<>first.role AND second.person_id=first.person_id
        JOIN people p ON p.id=first.person_id WHERE first.fixture_id=1
    """)
    assert paired
    triple = rows(engine, "SELECT person_id,COUNT(*) n FROM assignments WHERE fixture_id IN (1,2,3) GROUP BY person_id HAVING COUNT(*)>2")
    assert not triple


def test_weekday_opposite_role_balance_when_available():
    engine = db()
    open_and_vote_all(engine, [8, 9])  # Tuesday and Wednesday
    propose(engine)
    balanced = rows(engine, "SELECT person_id,COUNT(DISTINCT role) roles FROM assignments WHERE fixture_id IN (8,9) GROUP BY person_id HAVING COUNT(DISTINCT role)=2")
    assert balanced


def test_public_route_has_no_admin_navigation():
    assert not is_admin_request({})
    assert is_admin_request({"admin": "1"})
    source = Path("app.py").read_text(encoding="utf-8")
    public_body = source.split("def render_public", 1)[1].split("def admin_authenticated", 1)[0]
    assert "Admin" not in public_body
    assert 'if is_admin_request(st.query_params)' in source


def test_public_page_runtime_has_no_admin_tabs(monkeypatch, tmp_path):
    monkeypatch.setenv("SGIA_SQLITE_PATH", str(tmp_path / "public.db"))
    page = AppTest.from_file(Path(__file__).parents[1] / "app.py").run(timeout=20)
    assert not list(page.exception)
    assert len(page.tabs) == 0
    assert not any("Admin" in getattr(item, "value", "") for item in page.markdown)


def test_confirmed_allocation_message():
    engine = db()
    open_and_vote_all(engine, [1])
    propose(engine)
    with engine.begin() as connection:
        connection.execute(text("UPDATE assignments SET confirmed=true"))
    assert "SGIA Umpires" in allocation_message(engine)
