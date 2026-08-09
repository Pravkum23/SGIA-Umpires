import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

from sqlalchemy import text


def _token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _pin_digest(pin, salt=None, iterations=310_000):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), bytes.fromhex(salt), iterations).hex()
    return f"pbkdf2_sha256${iterations}${salt}${digest}"


def set_person_pin(engine, person_id, pin, revoke_tokens=True):
    if not (isinstance(pin, str) and len(pin) == 4 and pin.isdigit()):
        raise ValueError("PIN must contain exactly four digits")
    with engine.begin() as connection:
        connection.execute(text("UPDATE people SET pin_hash=:pin WHERE id=:person"), {"pin": _pin_digest(pin), "person": person_id})
        connection.execute(text("DELETE FROM pin_attempts WHERE person_id=:person"), {"person": person_id})
        if revoke_tokens:
            connection.execute(text("DELETE FROM device_tokens WHERE person_id=:person"), {"person": person_id})


def pin_status(engine, person_id):
    with engine.connect() as connection:
        return connection.execute(text("SELECT pin_hash IS NOT NULL FROM people WHERE id=:person"), {"person": person_id}).scalar_one()


def verify_person_pin(engine, person_id, pin, now=None):
    now = now or datetime.now()
    with engine.begin() as connection:
        row = connection.execute(text("""
            SELECT p.pin_hash,a.failed_count,a.lock_until FROM people p
            LEFT JOIN pin_attempts a ON a.person_id=p.id WHERE p.id=:person AND p.active=true
        """), {"person": person_id}).mappings().first()
        if not row or not row.pin_hash:
            return False, "PIN_NOT_SET"
        lock_until = row.lock_until
        if isinstance(lock_until, str):
            lock_until = datetime.fromisoformat(lock_until)
        if lock_until and lock_until > now:
            return False, "LOCKED"
        try:
            algorithm, iterations, salt, expected = row.pin_hash.split("$", 3)
            actual = _pin_digest(str(pin), salt, int(iterations)).split("$", 3)[3]
            valid = algorithm == "pbkdf2_sha256" and hmac.compare_digest(actual, expected)
        except (TypeError, ValueError):
            valid = False
        if valid:
            connection.execute(text("DELETE FROM pin_attempts WHERE person_id=:person"), {"person": person_id})
            return True, "OK"
        failures = int(row.failed_count or 0) + 1
        lock = now + timedelta(minutes=15) if failures >= 5 else None
        connection.execute(text("""
            INSERT INTO pin_attempts(person_id,failed_count,lock_until) VALUES(:person,:failures,:lock)
            ON CONFLICT(person_id) DO UPDATE SET failed_count=:failures,lock_until=:lock
        """), {"person": person_id, "failures": failures, "lock": lock})
        return False, "LOCKED" if lock else "INVALID"


def authenticate_person(engine, person_id, pin):
    valid, result = verify_person_pin(engine, person_id, pin)
    return (remember_person(engine, person_id), "OK") if valid else (None, result)


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
        connection.execute(text("INSERT INTO assignment_events(fixture_id,role,person_id,event_type,reason) VALUES(:fixture,:role,:person,'WITHDRAWN','Volunteer cannot attend')"), {"fixture": fixture_id, "role": role, "person": person_id})
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
        previous = connection.execute(text("SELECT person_id FROM assignments WHERE fixture_id=:fixture AND role=:role AND status='REPLACEMENT_REQUIRED'"), {"fixture": fixture_id, "role": role}).scalar()
        if not previous:
            return None
        connection.execute(text("""
            UPDATE assignments SET person_id=:person,confirmed=false,status='ASSIGNED',reason='Suggested replacement'
            WHERE fixture_id=:fixture AND role=:role AND status='REPLACEMENT_REQUIRED'
        """), {"person": candidate.id, "fixture": fixture_id, "role": role})
        connection.execute(text("""
            INSERT INTO assignment_events(fixture_id,role,person_id,replacement_person_id,event_type,reason)
            VALUES(:fixture,:role,:previous,:replacement,'REPLACED','Suggested replacement')
        """), {"fixture": fixture_id, "role": role, "previous": previous, "replacement": candidate.id})
        return dict(candidate)


def replace_assignment(engine, fixture_id, role, replacement_person_id, reason="Manual replacement"):
    with engine.begin() as connection:
        previous = connection.execute(text("SELECT person_id FROM assignments WHERE fixture_id=:fixture AND role=:role"), {"fixture": fixture_id, "role": role}).scalar_one()
        connection.execute(text("UPDATE assignments SET person_id=:replacement,status='ASSIGNED',confirmed=true,reason=:reason WHERE fixture_id=:fixture AND role=:role"), {"replacement": replacement_person_id, "reason": reason, "fixture": fixture_id, "role": role})
        connection.execute(text("""
            INSERT INTO assignment_events(fixture_id,role,person_id,replacement_person_id,event_type,reason)
            VALUES(:fixture,:role,:previous,:replacement,'REPLACED',:reason)
        """), {"fixture": fixture_id, "role": role, "previous": previous, "replacement": replacement_person_id, "reason": reason})


def confirm_assignment(engine, fixture_id, role, person_id, reason="Admin confirmed"):
    with engine.begin() as connection:
        connection.execute(text("UPDATE assignments SET person_id=:person,confirmed=true,status='ASSIGNED',reason=:reason WHERE fixture_id=:fixture AND role=:role"), {"person": person_id, "reason": reason, "fixture": fixture_id, "role": role})
        connection.execute(text("INSERT INTO assignment_events(fixture_id,role,person_id,event_type,reason) VALUES(:fixture,:role,:person,'CONFIRMED',:reason)"), {"fixture": fixture_id, "role": role, "person": person_id, "reason": reason})


def assignment_history(engine):
    with engine.connect() as connection:
        return [dict(row) for row in connection.execute(text("""
            SELECT e.created_at,f.starts_at,f.home_team,f.away_team,e.role,p.name person,
                   replacement.name replacement,e.event_type action,e.reason
            FROM assignment_events e JOIN fixtures f ON f.id=e.fixture_id
            JOIN people p ON p.id=e.person_id
            LEFT JOIN people replacement ON replacement.id=e.replacement_person_id
            ORDER BY e.created_at DESC,e.id DESC
        """)).mappings()]
