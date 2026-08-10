from pathlib import Path

from sqlalchemy import create_engine, text
from streamlit.testing.v1 import AppTest

from src.allocation import allocation_message, propose
from src.board import BOARD_COLUMNS, allocation_board, allocation_board_png, save_allocation_board
from src.db import POSTGRES_SCHEMA, SQLITE_SCHEMA, initialize, people_seed_params, rows, save_vote, schema_for
from src.fixtures import add_fixture, edit_fixture, import_bulk_fixtures, preview_bulk_fixtures
from src.lifecycle import authenticate_person, availability_for_person, forget_person, person_for_token, remember_person, replace_assignment, set_person_pin, suggest_replacement, verify_person_pin, withdraw_assignment
from src.live_poll import coverage_status, live_poll_monitor
from src.routing import is_admin_request
from src.workload import fixture_history, season_workload, set_match_status


def db():
    engine = create_engine("sqlite:///:memory:")
    initialize(engine)
    return engine


def open_and_vote_all(engine, fixture_ids):
    with engine.begin() as connection:
        for fixture_id in fixture_ids:
            connection.execute(text("UPDATE fixtures SET availability_open=true,poll_state='OPEN' WHERE id=:id"), {"id": fixture_id})
            connection.execute(text("INSERT INTO availability(person_id,fixture_id) SELECT id,:id FROM people"), {"id": fixture_id})


def test_seed_is_idempotent_and_exact():
    engine = db()
    initialize(engine)
    assert rows(engine, "SELECT COUNT(*) n FROM fixtures")[0]["n"] == 29
    assert rows(engine, "SELECT COUNT(*) n FROM people")[0]["n"] == 16


def test_vote_can_change_and_closed_slots_are_not_public():
    engine = db()
    with engine.begin() as connection:
        connection.execute(text("UPDATE fixtures SET availability_open=true,poll_state='OPEN' WHERE id IN (1,2)"))
    save_vote(engine, 1, [1, 2])
    save_vote(engine, 1, [2])
    assert rows(engine, "SELECT fixture_id FROM availability WHERE person_id=1") == [{"fixture_id": 2}]
    with engine.begin() as connection:
        connection.execute(text("UPDATE fixtures SET availability_open=false,poll_state='FROZEN' WHERE id=2"))
    assert rows(engine, "SELECT id FROM fixtures WHERE availability_open=true") == [{"id": 1}]


def test_new_poll_update_preserves_closed_fixture_votes():
    engine = db()
    with engine.begin() as connection:
        connection.execute(text("UPDATE fixtures SET availability_open=true,poll_state='OPEN' WHERE id IN (1,2)"))
    save_vote(engine, 1, [1, 2])
    with engine.begin() as connection:
        connection.execute(text("UPDATE fixtures SET availability_open=false,poll_state='FROZEN' WHERE id IN (1,2)"))
        connection.execute(text("UPDATE fixtures SET availability_open=true,poll_state='OPEN' WHERE id IN (12,13)"))
    save_vote(engine, 1, [12])
    assert rows(engine, "SELECT fixture_id FROM availability WHERE person_id=1 ORDER BY fixture_id") == [
        {"fixture_id": 1}, {"fixture_id": 2}, {"fixture_id": 12}
    ]


def test_editing_open_poll_only_adds_and_removes_open_choices():
    engine = db()
    with engine.begin() as connection:
        connection.execute(text("UPDATE fixtures SET availability_open=true,poll_state='OPEN' WHERE id IN (1,2)"))
    save_vote(engine, 1, [1, 2])
    with engine.begin() as connection:
        connection.execute(text("UPDATE fixtures SET availability_open=false,poll_state='FROZEN' WHERE id=1"))
        connection.execute(text("UPDATE fixtures SET availability_open=true,poll_state='OPEN' WHERE id=3"))
    save_vote(engine, 1, [3])
    assert rows(engine, "SELECT fixture_id FROM availability WHERE person_id=1 ORDER BY fixture_id") == [
        {"fixture_id": 1}, {"fixture_id": 3}
    ]
    events = rows(engine, "SELECT fixture_id,event_type FROM availability_events WHERE person_id=1 ORDER BY id")
    assert events[-2:] == [{"fixture_id": 3, "event_type": "ADDED"}, {"fixture_id": 2, "event_type": "REMOVED"}]


def test_frozen_poll_is_read_only_and_keeps_existing_selection():
    engine = db()
    with engine.begin() as connection:
        connection.execute(text("UPDATE fixtures SET availability_open=true,poll_state='OPEN' WHERE id=1"))
    save_vote(engine, 1, [1])
    with engine.begin() as connection:
        connection.execute(text("UPDATE fixtures SET availability_open=false,poll_state='FROZEN' WHERE id=1"))
    save_vote(engine, 1, [])
    assert rows(engine, "SELECT fixture_id FROM availability WHERE person_id=1") == [{"fixture_id": 1}]
    assert rows(engine, "SELECT event_type FROM availability_events WHERE person_id=1") == [{"event_type": "ADDED"}]


def test_latest_active_availability_drives_allocation():
    engine = db()
    malo = rows(engine, "SELECT id FROM people WHERE name='Malo'")[0]["id"]
    with engine.begin() as connection:
        connection.execute(text("UPDATE fixtures SET availability_open=true,poll_state='OPEN' WHERE id IN (8,9,10)"))
    save_vote(engine, malo, [8, 9, 10])
    save_vote(engine, malo, [8, 10])
    with engine.begin() as connection:
        connection.execute(text("UPDATE fixtures SET availability_open=false,poll_state='FROZEN' WHERE id IN (8,9,10)"))
    propose(engine)
    assert availability_for_person(engine, malo, "FROZEN") == {8, 10}
    assert not rows(engine, "SELECT 1 FROM assignments WHERE fixture_id=9 AND person_id=:person", {"person": malo})


def test_device_token_recognition_and_change_person():
    engine = db()
    malo = rows(engine, "SELECT id FROM people WHERE name='Malo'")[0]["id"]
    token = remember_person(engine, malo)
    assert len(token) >= 32
    assert person_for_token(engine, token) == {"id": malo, "name": "Malo"}
    assert "Malo" not in token
    forget_person(engine, token)
    assert person_for_token(engine, token) is None


def test_wrong_pin_rejected_and_correct_pin_creates_device_token():
    engine = db()
    malo = rows(engine, "SELECT id FROM people WHERE name='Malo'")[0]["id"]
    set_person_pin(engine, malo, "4821")
    token, result = authenticate_person(engine, malo, "1111")
    assert token is None and result == "INVALID"
    token, result = authenticate_person(engine, malo, "4821")
    assert result == "OK" and token
    assert person_for_token(engine, token) == {"id": malo, "name": "Malo"}
    stored = rows(engine, "SELECT pin_hash FROM people WHERE id=:person", {"person": malo})[0]["pin_hash"]
    assert "4821" not in stored and stored.startswith("pbkdf2_sha256$")


def test_pin_reset_revokes_existing_device_token():
    engine = db()
    malo = rows(engine, "SELECT id FROM people WHERE name='Malo'")[0]["id"]
    set_person_pin(engine, malo, "4821")
    token, _ = authenticate_person(engine, malo, "4821")
    set_person_pin(engine, malo, "7319", revoke_tokens=True)
    assert person_for_token(engine, token) is None
    assert verify_person_pin(engine, malo, "7319") == (True, "OK")


def test_returning_volunteer_ui_is_recognized_and_choices_prepopulate(monkeypatch, tmp_path):
    path = tmp_path / "remembered.db"
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    initialize(engine)
    malo = rows(engine, "SELECT id FROM people WHERE name='Malo'")[0]["id"]
    with engine.begin() as connection:
        connection.execute(text("UPDATE fixtures SET availability_open=true,poll_state='OPEN' WHERE id IN (1,2)"))
    save_vote(engine, malo, [1])
    token = remember_person(engine, malo)
    engine.dispose()
    monkeypatch.setenv("SGIA_SQLITE_PATH", str(path))
    page = AppTest.from_file(Path(__file__).parents[1] / "app.py")
    page.query_params["device"] = token
    page.run(timeout=20)
    assert not list(page.exception)
    assert any("Hi Malo" in getattr(item, "value", "") for item in page.markdown)
    choices = {item.label: item.value for item in page.checkbox}
    assert choices["Saturday - 11:00 AM"] is True
    assert choices["Saturday - 3:00 PM"] is False


def test_sqlite_and_postgres_schema_use_native_auto_generated_ids():
    assert schema_for("sqlite") == SQLITE_SCHEMA
    assert "INTEGER PRIMARY KEY AUTOINCREMENT" in SQLITE_SCHEMA
    assert schema_for("postgresql") == POSTGRES_SCHEMA
    assert POSTGRES_SCHEMA.count("GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY") == 5
    assert "AUTOINCREMENT" not in POSTGRES_SCHEMA


def test_people_seed_parameters_use_postgresql_safe_booleans():
    params = people_seed_params()
    assert len(params) == 16
    assert all(type(person["u"]) is bool for person in params)
    assert all(type(person["s"]) is bool for person in params)


def test_closed_poll_votes_can_still_be_allocated():
    engine = db()
    open_and_vote_all(engine, [1])
    with engine.begin() as connection:
        connection.execute(text("UPDATE fixtures SET availability_open=false,poll_state='FROZEN' WHERE id=1"))
    propose(engine)
    assert rows(engine, "SELECT COUNT(*) n FROM assignments WHERE fixture_id=1")[0]["n"] == 3


def test_three_role_staffing_model_is_proposed():
    engine = db()
    open_and_vote_all(engine, [1])
    propose(engine)
    proposed = rows(engine, "SELECT role FROM assignments WHERE fixture_id=1 ORDER BY role")
    assert proposed == [{"role": "scorer"}, {"role": "umpire_1"}, {"role": "umpire_2"}]
    assert rows(engine, "SELECT COUNT(DISTINCT person_id) n FROM assignments WHERE fixture_id=1")[0]["n"] == 3


def test_legacy_umpire_storage_is_migrated_idempotently():
    engine = db()
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO assignments(fixture_id,role,person_id,confirmed,reason) VALUES(1,'umpire',1,true,'Legacy')"))
    initialize(engine)
    initialize(engine)
    assert rows(engine, "SELECT role,person_id,confirmed FROM assignments") == [
        {"role": "umpire_1", "person_id": 1, "confirmed": 1}
    ]


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
        connection.execute(text("UPDATE fixtures SET availability_open=false,poll_state='FROZEN' WHERE id IN (1,2)"))
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
        JOIN assignments second ON second.fixture_id=2 AND second.person_id=first.person_id
        JOIN people p ON p.id=first.person_id
        WHERE first.fixture_id=1
          AND ((first.role='scorer' AND second.role IN ('umpire_1','umpire_2'))
            OR (second.role='scorer' AND first.role IN ('umpire_1','umpire_2')))
    """)
    assert paired
    triple = rows(engine, "SELECT person_id,COUNT(*) n FROM assignments WHERE fixture_id IN (1,2,3) GROUP BY person_id HAVING COUNT(*)>2")
    assert not triple


def test_weekday_opposite_role_balance_when_available():
    engine = db()
    open_and_vote_all(engine, [8, 9])  # Tuesday and Wednesday
    propose(engine)
    balanced = rows(engine, """
        SELECT person_id FROM assignments WHERE fixture_id IN (8,9)
        GROUP BY person_id
        HAVING SUM(CASE WHEN role='scorer' THEN 1 ELSE 0 END)>0
           AND SUM(CASE WHEN role IN ('umpire_1','umpire_2') THEN 1 ELSE 0 END)>0
    """)
    assert balanced


def test_allocation_board_columns_and_manual_save():
    engine = db()
    open_and_vote_all(engine, [1])
    propose(engine)
    board = allocation_board(engine)
    assert list({key: None for key in board[0] if key != "fixture_id"}) == BOARD_COLUMNS
    original = board[0]["Umpire 1"]
    replacement = next(row["name"] for row in rows(engine, "SELECT name FROM people WHERE active=true ORDER BY name") if row["name"] not in {original, board[0]["Umpire 2"], board[0]["Scorer"]})
    board[0]["Umpire 1"] = replacement
    save_allocation_board(engine, board)
    saved = rows(engine, """
        SELECT p.name,a.confirmed,a.reason FROM assignments a JOIN people p ON p.id=a.person_id
        WHERE a.fixture_id=1 AND a.role='umpire_1'
    """)[0]
    assert saved == {"name": replacement, "confirmed": 1, "reason": "Admin allocation board"}
    audit = rows(engine, "SELECT event_type,person_id,replacement_person_id FROM assignment_events WHERE fixture_id=1 AND role='umpire_1'")
    assert audit[-1] == {"event_type": "MANUAL_CHANGE", "person_id": next(row["id"] for row in rows(engine, "SELECT id,name FROM people") if row["name"] == original), "replacement_person_id": next(row["id"] for row in rows(engine, "SELECT id,name FROM people") if row["name"] == replacement)}
    assert rows(engine, "SELECT COUNT(*) n FROM assignment_events WHERE event_type='CONFIRMED'")[0]["n"] == 2


def test_allocation_board_png_export():
    engine = db()
    open_and_vote_all(engine, [1, 2])
    propose(engine)
    image = allocation_board_png(allocation_board(engine), Path(__file__).parents[1] / "assets" / "sgia-logo.png")
    assert image.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(image) > 10_000


def test_public_route_has_no_admin_navigation():
    assert not is_admin_request({})
    assert is_admin_request({"admin": "1"})
    source = Path("app.py").read_text(encoding="utf-8")
    public_body = source.split("def render_public", 1)[1].split("def admin_authenticated", 1)[0]
    assert "Admin" not in public_body
    assert "ADMIN_MODE = is_admin_request(st.query_params)" in source
    assert "if ADMIN_MODE:" in source


def test_public_page_runtime_has_no_admin_tabs(monkeypatch, tmp_path):
    monkeypatch.setenv("SGIA_SQLITE_PATH", str(tmp_path / "public.db"))
    page = AppTest.from_file(Path(__file__).parents[1] / "app.py").run(timeout=20)
    assert not list(page.exception)
    assert len(page.tabs) == 0
    assert not any("Admin" in getattr(item, "value", "") for item in page.markdown)
    rendered = " ".join(getattr(item, "value", "") for item in page.markdown)
    assert "Provide your availability for this weekend" in rendered
    assert "Select your name to view the available slots." in rendered


def test_public_brand_asset_and_mobile_layout_are_required():
    root = Path(__file__).parents[1]
    logo = root / "assets" / "sgia-logo.png"
    assert logo.is_file()
    assert logo.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    source = (root / "app.py").read_text(encoding="utf-8")
    assert "max-width:500px!important" in source
    assert "brand-badge" not in source
    assert 'raise FileNotFoundError("Required SGIA logo is missing' in source


def test_admin_query_route_still_renders_login(monkeypatch, tmp_path):
    monkeypatch.setenv("SGIA_SQLITE_PATH", str(tmp_path / "admin.db"))
    monkeypatch.setenv("SGIA_ADMIN_PIN", "test-only-pin")
    page = AppTest.from_file(Path(__file__).parents[1] / "app.py")
    page.query_params["admin"] = "1"
    page.run(timeout=20)
    assert not list(page.exception)
    assert any(item.label == "Admin PIN" for item in page.text_input)
    next(item for item in page.text_input if item.label == "Admin PIN").set_value("test-only-pin")
    next(item for item in page.button if item.label == "Sign in").click()
    page.run(timeout=20)
    assert not list(page.exception)
    labels = {tab.label for tab in page.tabs}
    assert {"Open Poll", "Fixtures", "Allocations", "Allocation Board", "People", "Season Workload", "History", "Output"} <= labels


def test_confirmed_allocation_message():
    engine = db()
    open_and_vote_all(engine, [1])
    propose(engine)
    with engine.begin() as connection:
        connection.execute(text("UPDATE assignments SET confirmed=true"))
    message = allocation_message(engine)
    assert "SGIA Umpires" in message
    assert "Umpire 1:" in message
    assert "Umpire 2:" in message


def test_published_duty_withdrawal_requires_replacement():
    engine = db()
    open_and_vote_all(engine, [1])
    propose(engine)
    assignment = rows(engine, "SELECT fixture_id,role,person_id FROM assignments WHERE fixture_id=1 ORDER BY role LIMIT 1")[0]
    with engine.begin() as connection:
        connection.execute(text("UPDATE assignments SET confirmed=true WHERE fixture_id=1"))
        connection.execute(text("UPDATE fixtures SET availability_open=false,poll_state='PUBLISHED' WHERE id=1"))
    assert withdraw_assignment(engine, assignment["fixture_id"], assignment["role"], assignment["person_id"])
    assert rows(engine, "SELECT status FROM assignments WHERE fixture_id=:fixture_id AND role=:role", assignment) == [{"status": "REPLACEMENT_REQUIRED"}]
    assert rows(engine, "SELECT event_type,person_id FROM assignment_events") == [{"event_type": "WITHDRAWN", "person_id": assignment["person_id"]}]
    assert not withdraw_assignment(engine, assignment["fixture_id"], assignment["role"], assignment["person_id"])
    replacement = suggest_replacement(engine, assignment["fixture_id"], assignment["role"])
    assert replacement and replacement["id"] != assignment["person_id"]
    assert rows(engine, "SELECT person_id,confirmed,status FROM assignments WHERE fixture_id=:fixture_id AND role=:role", assignment) == [
        {"person_id": replacement["id"], "confirmed": 0, "status": "ASSIGNED"}
    ]
    events = rows(engine, "SELECT event_type,person_id,replacement_person_id FROM assignment_events ORDER BY id")
    assert events[0]["event_type"] == "WITHDRAWN"
    assert events[1] == {"event_type": "REPLACED", "person_id": assignment["person_id"], "replacement_person_id": replacement["id"]}


def test_manual_replacement_records_replaced_event():
    engine = db()
    open_and_vote_all(engine, [1])
    propose(engine)
    assignment = rows(engine, "SELECT fixture_id,role,person_id FROM assignments WHERE fixture_id=1 ORDER BY role LIMIT 1")[0]
    replacement = rows(engine, "SELECT id FROM people WHERE active=true AND id<>:person ORDER BY id LIMIT 1", {"person": assignment["person_id"]})[0]["id"]
    replace_assignment(engine, assignment["fixture_id"], assignment["role"], replacement)
    assert rows(engine, "SELECT event_type,person_id,replacement_person_id FROM assignment_events") == [
        {"event_type": "REPLACED", "person_id": assignment["person_id"], "replacement_person_id": replacement}
    ]


def test_fixture_add_edit_and_duplicate_safe_bulk_import():
    engine = db()
    assert add_fixture(engine, "05-Sep-2026", "11:00 AM", "Team A", "Team B")
    assert not add_fixture(engine, "05-Sep-2026", "11:00 AM", "Duplicate", "Ignored")
    fixture = rows(engine, "SELECT id FROM fixtures WHERE home_team='Team A'")[0]
    assert edit_fixture(engine, fixture["id"], "05-Sep-2026", "12:00 PM", "Team Alpha", "Team Beta")
    edited = rows(engine, "SELECT home_team,away_team FROM fixtures WHERE id=:id", fixture)[0]
    assert edited == {"home_team": "Team Alpha", "away_team": "Team Beta"}
    pasted = """Day | Date | Time | TEAM 1 | TEAM 2
Saturday | 05-Sep-2026 | 12:00 PM | Duplicate | Existing
Sunday | 06-Sep-2026 | 3:00 PM | Team C | Team D
Sunday | 06-Sep-2026 | 3:00 PM | Duplicate In Paste | Team E"""
    preview = preview_bulk_fixtures(engine, pasted)
    assert [item["Status"] for item in preview] == ["DUPLICATE", "READY", "DUPLICATE"]
    assert import_bulk_fixtures(engine, preview) == 1
    assert import_bulk_fixtures(engine, preview) == 0


def test_existing_schema_migration_preserves_data():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE people (id INTEGER PRIMARY KEY, name VARCHAR(80) UNIQUE NOT NULL, can_umpire BOOLEAN NOT NULL, can_score BOOLEAN NOT NULL, preferred_role VARCHAR(10) NOT NULL, active BOOLEAN NOT NULL DEFAULT TRUE)"))
        connection.execute(text("CREATE TABLE fixtures (id INTEGER PRIMARY KEY, starts_at TIMESTAMP UNIQUE NOT NULL, home_team VARCHAR(100) NOT NULL, away_team VARCHAR(100) NOT NULL, availability_open BOOLEAN NOT NULL DEFAULT FALSE)"))
        connection.execute(text("CREATE TABLE availability (person_id INTEGER NOT NULL, fixture_id INTEGER NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(person_id,fixture_id))"))
        connection.execute(text("CREATE TABLE assignments (fixture_id INTEGER NOT NULL, role VARCHAR(10) NOT NULL, person_id INTEGER NOT NULL, confirmed BOOLEAN NOT NULL DEFAULT FALSE, reason TEXT, PRIMARY KEY(fixture_id,role))"))
        connection.execute(text("INSERT INTO people VALUES(100,'Legacy Person',true,true,'Either',true)"))
        connection.execute(text("INSERT INTO fixtures VALUES(100,'2026-09-20 10:00:00','Legacy A','Legacy B',true)"))
        connection.execute(text("INSERT INTO availability(person_id,fixture_id) VALUES(100,100)"))
        connection.execute(text("INSERT INTO assignments VALUES(100,'umpire',100,true,'Legacy assignment')"))
    initialize(engine)
    assert rows(engine, "SELECT name,pin_hash FROM people WHERE id=100") == [{"name": "Legacy Person", "pin_hash": None}]
    assert rows(engine, "SELECT poll_state FROM fixtures WHERE id=100") == [{"poll_state": "OPEN"}]
    assert rows(engine, "SELECT person_id,fixture_id FROM availability WHERE person_id=100") == [{"person_id": 100, "fixture_id": 100}]
    assert rows(engine, "SELECT role,status,person_id FROM assignments WHERE fixture_id=100") == [{"role": "umpire_1", "status": "ASSIGNED", "person_id": 100}]
    assert rows(engine, "SELECT match_status FROM fixtures WHERE id=100") == [{"match_status": "SCHEDULED"}]
    assert rows(engine, "SELECT COUNT(*) n FROM fixture_events")[0]["n"] == 0
    assert rows(engine, "SELECT COUNT(*) n FROM poll_submissions WHERE person_id=100")[0]["n"] == 1


def test_match_status_migration_is_idempotent_and_defaults_scheduled():
    engine = db()
    initialize(engine)
    assert rows(engine, "SELECT DISTINCT match_status FROM fixtures") == [{"match_status": "SCHEDULED"}]
    assert rows(engine, "SELECT COUNT(*) n FROM fixtures")[0]["n"] == 29


def test_completed_workload_counts_distinct_fixture_roles_and_scorer():
    engine = db()
    person = rows(engine, "SELECT id FROM people WHERE name='Malo'")[0]["id"]
    with engine.begin() as connection:
        connection.execute(text("UPDATE fixtures SET poll_state='PUBLISHED' WHERE id=1"))
        connection.execute(text("INSERT INTO assignments(fixture_id,role,person_id,confirmed,status) VALUES(1,'umpire_1',:person,true,'ASSIGNED')"), {"person": person})
        connection.execute(text("INSERT INTO assignments(fixture_id,role,person_id,confirmed,status) VALUES(1,'scorer',:person,true,'ASSIGNED')"), {"person": person})
    set_match_status(engine, 1, "COMPLETED")
    stats = season_workload(engine, person)[0]
    assert stats["games_completed"] == 1
    assert stats["umpire_completed"] == 1
    assert stats["scorer_completed"] == 1
    assert stats["total_assigned"] == 1


def test_cancelled_and_directly_restored_completed_fixture_reverse_workload():
    engine = db()
    person = rows(engine, "SELECT id FROM people WHERE name='Malo'")[0]["id"]
    with engine.begin() as connection:
        connection.execute(text("UPDATE fixtures SET poll_state='PUBLISHED' WHERE id=1"))
        connection.execute(text("INSERT INTO assignments(fixture_id,role,person_id,confirmed,status) VALUES(1,'umpire_2',:person,true,'ASSIGNED')"), {"person": person})
    set_match_status(engine, 1, "COMPLETED")
    assert season_workload(engine, person)[0]["games_completed"] == 1
    assert season_workload(engine, person)[0]["umpire_completed"] == 1
    set_match_status(engine, 1, "SCHEDULED")
    assert season_workload(engine, person)[0]["games_completed"] == 0
    set_match_status(engine, 1, "COMPLETED")
    set_match_status(engine, 1, "CANCELLED")
    assert season_workload(engine, person)[0]["games_completed"] == 0
    assert [event["action"] for event in fixture_history(engine)] == ["MATCH_CANCELLED", "MATCH_COMPLETED", "MATCH_RESTORED", "MATCH_COMPLETED"]


def test_unpublished_fixture_cannot_be_completed():
    engine = db()
    try:
        set_match_status(engine, 1, "COMPLETED")
        assert False, "Expected unpublished completion to be rejected"
    except ValueError as error:
        assert "Publish" in str(error)
    assert rows(engine, "SELECT match_status FROM fixtures WHERE id=1") == [{"match_status": "SCHEDULED"}]


def test_upcoming_requires_future_published_confirmed_active_assignment():
    engine = db()
    people = rows(engine, "SELECT id FROM people ORDER BY id LIMIT 3")
    with engine.begin() as connection:
        connection.execute(text("UPDATE fixtures SET poll_state='PUBLISHED' WHERE id=1"))
        connection.execute(text("INSERT INTO assignments(fixture_id,role,person_id,confirmed,status) VALUES(1,'umpire_1',:person,true,'ASSIGNED')"), {"person": people[0]["id"]})
        connection.execute(text("INSERT INTO assignments(fixture_id,role,person_id,confirmed,status) VALUES(2,'umpire_1',:person,true,'ASSIGNED')"), {"person": people[1]["id"]})
        connection.execute(text("INSERT INTO assignments(fixture_id,role,person_id,confirmed,status) VALUES(3,'umpire_1',:person,false,'ASSIGNED')"), {"person": people[2]["id"]})
    assert season_workload(engine, people[0]["id"])[0]["upcoming_duties"] == 1
    assert season_workload(engine, people[1]["id"])[0]["upcoming_duties"] == 0
    assert season_workload(engine, people[2]["id"])[0]["upcoming_duties"] == 0


def test_withdrawal_does_not_count_and_replacement_credits_current_person():
    engine = db()
    original, replacement = rows(engine, "SELECT id FROM people ORDER BY id LIMIT 2")
    with engine.begin() as connection:
        connection.execute(text("UPDATE fixtures SET poll_state='PUBLISHED' WHERE id=1"))
        connection.execute(text("INSERT INTO assignments(fixture_id,role,person_id,confirmed,status) VALUES(1,'scorer',:person,true,'ASSIGNED')"), {"person": original["id"]})
    assert withdraw_assignment(engine, 1, "scorer", original["id"])
    assert season_workload(engine, original["id"])[0]["upcoming_duties"] == 0
    replace_assignment(engine, 1, "scorer", replacement["id"])
    assert season_workload(engine, replacement["id"])[0]["upcoming_duties"] == 1
    assert season_workload(engine, original["id"])[0]["upcoming_duties"] == 0


def test_workload_orders_least_completed_then_total_then_name():
    engine = db()
    people = season_workload(engine)
    assert [person["name"] for person in people] == sorted(person["name"] for person in people)
    person = people[-1]
    with engine.begin() as connection:
        connection.execute(text("UPDATE fixtures SET poll_state='PUBLISHED' WHERE id=1"))
        connection.execute(text("INSERT INTO assignments(fixture_id,role,person_id,confirmed,status) VALUES(1,'umpire_1',:person,true,'ASSIGNED')"), {"person": person["person_id"]})
    set_match_status(engine, 1, "COMPLETED")
    assert season_workload(engine)[-1]["person_id"] == person["person_id"]


def test_allocator_uses_lower_season_workload_only_as_tiebreaker():
    engine = db()
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO people(name,can_umpire,can_score,preferred_role,active) VALUES('Fair High',true,true,'Either',true),('Fair Low',true,true,'Either',true)"))
        high = connection.execute(text("SELECT id FROM people WHERE name='Fair High'")).scalar_one()
        low = connection.execute(text("SELECT id FROM people WHERE name='Fair Low'")).scalar_one()
        connection.execute(text("UPDATE fixtures SET poll_state='PUBLISHED',match_status='COMPLETED' WHERE id=2"))
        connection.execute(text("INSERT INTO assignments(fixture_id,role,person_id,confirmed,status) VALUES(2,'umpire_1',:high,true,'ASSIGNED')"), {"high": high})
        connection.execute(text("INSERT INTO availability(person_id,fixture_id) VALUES(:high,1),(:low,1)"), {"high": high, "low": low})
    propose(engine)
    chosen = rows(engine, "SELECT person_id,reason FROM assignments WHERE fixture_id=1 AND role='umpire_1'")[0]
    assert chosen["person_id"] == low
    assert "season workload" in chosen["reason"].lower()


def test_live_poll_counts_active_zero_selection_response_and_nonresponders():
    engine = db()
    person = rows(engine, "SELECT id,name FROM people WHERE active=true ORDER BY name LIMIT 1")[0]
    with engine.begin() as connection:
        connection.execute(text("UPDATE fixtures SET poll_state='OPEN',availability_open=true WHERE id IN (1,2)"))
    save_vote(engine, person["id"], [])
    monitor = live_poll_monitor(engine)
    assert monitor["active_volunteers"] == 16
    assert monitor["responded"] == 1
    assert monitor["open_matches"] == 2
    assert person["name"] not in monitor["yet_to_respond"]
    assert len(monitor["yet_to_respond"]) == 15
    assert rows(engine, "SELECT COUNT(*) n FROM availability WHERE person_id=:person", {"person": person["id"]})[0]["n"] == 0


def test_live_poll_updated_submission_remains_one_respondent():
    engine = db()
    person = rows(engine, "SELECT id FROM people WHERE active=true ORDER BY id LIMIT 1")[0]
    with engine.begin() as connection:
        connection.execute(text("UPDATE fixtures SET poll_state='OPEN',availability_open=true WHERE id IN (1,2)"))
    save_vote(engine, person["id"], [1])
    save_vote(engine, person["id"], [2])
    assert live_poll_monitor(engine)["responded"] == 1
    assert rows(engine, "SELECT COUNT(*) n FROM poll_submissions WHERE person_id=:id", person)[0]["n"] == 1


def test_live_poll_day_and_match_unique_availability_capabilities():
    engine = db()
    people = rows(engine, "SELECT id,name,can_umpire,can_score FROM people WHERE active=true ORDER BY id LIMIT 3")
    with engine.begin() as connection:
        connection.execute(text("UPDATE fixtures SET poll_state='OPEN',availability_open=true WHERE id IN (1,2)"))
    save_vote(engine, people[0]["id"], [1, 2])
    save_vote(engine, people[1]["id"], [1])
    save_vote(engine, people[2]["id"], [1])
    monitor = live_poll_monitor(engine)
    assert len(monitor["days"]) == 1
    day = monitor["days"][0]
    assert day["label"] == "Saturday 15 Aug"
    assert day["unique_available"] == 3
    assert len(day["matches"]) == 2
    first, second = day["matches"]
    assert first["available_count"] == 3
    assert second["available_count"] == 1
    assert first["umpire_count"] == len({person["id"] for person in people if person["can_umpire"]})
    assert first["scorer_count"] == len({person["id"] for person in people if person["can_score"]})


def test_live_poll_coverage_requires_three_distinct_role_eligible_people():
    covered = [
        {"id": 1, "can_umpire": True, "can_score": True},
        {"id": 2, "can_umpire": True, "can_score": False},
        {"id": 3, "can_umpire": False, "can_score": True},
    ]
    scorer_shortage = [{"id": value, "can_umpire": True, "can_score": False} for value in range(1, 4)]
    umpire_shortage = [
        {"id": 1, "can_umpire": True, "can_score": True},
        {"id": 2, "can_umpire": False, "can_score": True},
        {"id": 3, "can_umpire": False, "can_score": True},
    ]
    assert coverage_status(covered) == "COVERED"
    assert coverage_status(scorer_shortage) == "SCORER COVERAGE NEEDED"
    assert coverage_status(umpire_shortage) == "UMPIRE COVERAGE NEEDED"
    assert coverage_status(covered[:2]) == "NEED MORE"


def test_live_poll_excludes_non_open_fixtures_and_uses_singapore_wall_clock():
    engine = db()
    with engine.begin() as connection:
        connection.execute(text("UPDATE fixtures SET poll_state='OPEN',availability_open=true WHERE id=1"))
        connection.execute(text("UPDATE fixtures SET poll_state='FROZEN' WHERE id=2"))
        connection.execute(text("UPDATE fixtures SET poll_state='CLOSED' WHERE id=3"))
        connection.execute(text("UPDATE fixtures SET poll_state='PUBLISHED' WHERE id=4"))
    monitor = live_poll_monitor(engine)
    assert monitor["open_matches"] == 1
    assert monitor["days"][0]["label"] == "Saturday 15 Aug"
    assert monitor["days"][0]["matches"][0]["time"] == "11:00 AM"


def test_live_poll_mobile_admin_contract_is_present():
    source = Path("app.py").read_text(encoding="utf-8")
    assert "LIVE POLL" in source
    assert "REFRESH LIVE POLL" in source
    assert "Yet to Respond" in source
    assert "@media(max-width:700px)" in source
    assert "live_poll_monitor(db)" in source
