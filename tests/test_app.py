from pathlib import Path

from sqlalchemy import create_engine, text
from streamlit.testing.v1 import AppTest

from src.allocation import allocation_message, propose
from src.board import BOARD_COLUMNS, allocation_board, allocation_board_png, save_allocation_board
from src.db import POSTGRES_SCHEMA, SQLITE_SCHEMA, initialize, people_seed_params, rows, save_vote, schema_for
from src.lifecycle import availability_for_person, forget_person, person_for_token, remember_person, suggest_replacement, withdraw_assignment
from src.routing import is_admin_request


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
    assert POSTGRES_SCHEMA.count("GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY") == 4
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
