from sqlalchemy import create_engine, text
from src.db import initialize, rows, save_vote
from src.allocation import propose, allocation_message

def db():
    e=create_engine("sqlite:///:memory:");initialize(e);return e

def test_seed_is_idempotent_and_exact():
    e=db();initialize(e)
    assert rows(e,"SELECT COUNT(*) n FROM fixtures")[0]["n"]==29
    assert rows(e,"SELECT COUNT(*) n FROM people")[0]["n"]==16

def test_vote_changes_and_closed_slots_hidden():
    e=db()
    with e.begin() as c:c.execute(text("UPDATE fixtures SET availability_open=true WHERE id IN (1,2)"))
    save_vote(e,1,[1,2]);save_vote(e,1,[2])
    assert rows(e,"SELECT fixture_id FROM availability WHERE person_id=1")==[{"fixture_id":2}]
    with e.begin() as c:c.execute(text("UPDATE fixtures SET availability_open=false WHERE id=2"))
    assert rows(e,"SELECT COUNT(*) n FROM fixtures WHERE availability_open=true")[0]["n"]==1

def test_role_rules_proposals_manual_override_and_output():
    e=db()
    with e.begin() as c:
        c.execute(text("UPDATE fixtures SET availability_open=true WHERE id IN (1,2)"))
        c.execute(text("INSERT INTO availability(person_id,fixture_id) SELECT id,1 FROM people"))
        c.execute(text("INSERT INTO availability(person_id,fixture_id) SELECT id,2 FROM people"))
    propose(e)
    bad=rows(e,"SELECT p.name,a.role FROM assignments a JOIN people p ON p.id=a.person_id WHERE a.role='scorer' AND p.name IN ('Velu','Shree')")
    assert not bad
    praveen=rows(e,"SELECT a.role FROM assignments a JOIN people p ON p.id=a.person_id WHERE p.name='Praveen'")
    assert praveen and all(x["role"]=="scorer" for x in praveen)
    paired=rows(e,"SELECT p.name FROM assignments first JOIN assignments second ON second.fixture_id=2 AND second.role<>first.role AND second.person_id=first.person_id JOIN people p ON p.id=first.person_id WHERE first.fixture_id=1")
    assert paired, "weekend adjacent fixtures should share one eligible person in opposite roles"
    with e.begin() as c:c.execute(text("UPDATE assignments SET confirmed=true"))
    assert "SGIA Umpires" in allocation_message(e)
