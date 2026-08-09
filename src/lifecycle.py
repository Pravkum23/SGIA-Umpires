import hashlib
import secrets

from sqlalchemy import text


def _token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def remember_person(engine, person_id):
    token = secrets.token_urlsafe(32)
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO device_tokens(token_hash,person_id) VALUES(:token,:person)"), {"token": _token_hash(token), "person": person_id})
    return token


def person_for_token(engine, token):
    if not token or len(token) < 32:
        return None
    with engine.begin() as connection:
        person = connection.execute(text("""
            SELECT p.id,p.name FROM device_tokens d JOIN people p ON p.id=d.person_id
            WHERE d.token_hash=:token AND p.active=true
        """), {"token": _token_hash(token)}).mappings().first()
        if person:
            connection.execute(text("UPDATE device_tokens SET last_seen=CURRENT_TIMESTAMP WHERE token_hash=:token"), {"token": _token_hash(token)})
            return dict(person)
    return None


def forget_person(engine, token):
    if not token:
        return
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM device_tokens WHERE token_hash=:token"), {"token": _token_hash(token)})


def availability_for_person(engine, person_id, state="OPEN"):
    with engine.connect() as connection:
        return {row.fixture_id for row in connection.execute(text("""
            SELECT a.fixture_id FROM availability a JOIN fixtures f ON f.id=a.fixture_id
            WHERE a.person_id=:person AND f.poll_state=:state
        """), {"person": person_id, "state": state})}


def published_duties(engine, person_id):
    with engine.connect() as connection:
        return [dict(row) for row in connection.execute(text("""
            SELECT a.fixture_id,a.role,a.status,f.starts_at,f.home_team,f.away_team
            FROM assignments a JOIN fixtures f ON f.id=a.fixture_id
            WHERE a.person_id=:person AND a.confirmed=true AND f.poll_state='PUBLISHED'
            ORDER BY f.starts_at,a.role
        """), {"person": person_id}).mappings()]


def withdraw_assignment(engine, fixture_id, role, person_id):
    with engine.begin() as connection:
        assignment = connection.execute(text("""
            SELECT 1 FROM assignments a JOIN fixtures f ON f.id=a.fixture_id
            WHERE a.fixture_id=:fixture AND a.role=:role AND a.person_id=:person
              AND a.confirmed=true AND a.status='ASSIGNED' AND f.poll_state='PUBLISHED'
        """), {"fixture": fixture_id, "role": role, "person": person_id}).first()
        if not assignment:
            return False
        connection.execute(text("UPDATE assignments SET status='REPLACEMENT_REQUIRED' WHERE fixture_id=:fixture AND role=:role"), {"fixture": fixture_id, "role": role})
        connection.execute(text("INSERT INTO assignment_events(fixture_id,role,person_id,event_type) VALUES(:fixture,:role,:person,'WITHDRAWN')"), {"fixture": fixture_id, "role": role, "person": person_id})
        return True


def suggest_replacement(engine, fixture_id, role):
    capability = "can_score" if role == "scorer" else "can_umpire"
    with engine.begin() as connection:
        candidate = connection.execute(text(f"""
            SELECT p.id,p.name,COUNT(work.fixture_id) workload
            FROM availability av JOIN people p ON p.id=av.person_id
            LEFT JOIN assignments work ON work.person_id=p.id
            WHERE av.fixture_id=:fixture AND p.active=true AND p.{capability}=true
              AND p.id NOT IN (SELECT person_id FROM assignments WHERE fixture_id=:fixture)
            GROUP BY p.id,p.name ORDER BY workload,p.name LIMIT 1
        """), {"fixture": fixture_id}).mappings().first()
        if not candidate:
            return None
        connection.execute(text("""
            UPDATE assignments SET person_id=:person,confirmed=false,status='ASSIGNED',reason='Suggested replacement'
            WHERE fixture_id=:fixture AND role=:role AND status='REPLACEMENT_REQUIRED'
        """), {"person": candidate.id, "fixture": fixture_id, "role": role})
        return dict(candidate)
