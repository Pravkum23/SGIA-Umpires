import hashlib
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import text

from .poll_audit import response_audit


SGIA_TIMEZONE = "Asia/Singapore"


def poll_key(fixture_ids):
    values = ",".join(str(value) for value in sorted(fixture_ids))
    return hashlib.sha256(values.encode("ascii")).hexdigest() if values else None


def _datetime(value):
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return parsed.replace(tzinfo=ZoneInfo(SGIA_TIMEZONE)) if parsed.tzinfo is None else parsed.astimezone(ZoneInfo(SGIA_TIMEZONE))


def coverage_status(people):
    available = {person["id"] for person in people}
    umpires = {person["id"] for person in people if person["can_umpire"]}
    scorers = {person["id"] for person in people if person["can_score"]}
    if len(available) < 3:
        return "NEED MORE"
    if not scorers:
        return "SCORER COVERAGE NEEDED"
    if len(umpires) < 2:
        return "UMPIRE COVERAGE NEEDED"
    if any(len(umpires - {scorer}) >= 2 for scorer in scorers):
        return "COVERED"
    return "DISTINCT ROLE COVERAGE NEEDED"


def live_poll_monitor(engine):
    """Return the current OPEN-poll operational summary for admin only."""
    with engine.connect() as connection:
        fixtures = [dict(row) for row in connection.execute(text("""
            SELECT id,starts_at,home_team,away_team FROM fixtures
            WHERE poll_state='OPEN' ORDER BY starts_at,id
        """)).mappings()]
        active = [dict(row) for row in connection.execute(text(
            "SELECT id,name,can_umpire,can_score FROM people WHERE active=true ORDER BY name"
        )).mappings()]
        availability = [dict(row) for row in connection.execute(text("""
            SELECT a.fixture_id,p.id,p.name,p.can_umpire,p.can_score
            FROM availability a JOIN people p ON p.id=a.person_id JOIN fixtures f ON f.id=a.fixture_id
            WHERE f.poll_state='OPEN' AND p.active=true ORDER BY p.name
        """)).mappings()]

    by_fixture = defaultdict(list)
    for person in availability:
        by_fixture[person["fixture_id"]].append(person)
    by_day = {}
    for fixture in fixtures:
        starts = _datetime(fixture["starts_at"])
        day_key = starts.date().isoformat()
        day = by_day.setdefault(day_key, {
            "date": starts.date(), "label": starts.strftime("%A %d %b"),
            "matches": [], "available_ids": set(),
        })
        people = by_fixture[fixture["id"]]
        day["available_ids"].update(person["id"] for person in people)
        day["matches"].append({
            **fixture,
            "time": starts.strftime("%I:%M %p").lstrip("0"),
            "available": people,
            "available_count": len({person["id"] for person in people}),
            "umpire_count": len({person["id"] for person in people if person["can_umpire"]}),
            "scorer_count": len({person["id"] for person in people if person["can_score"]}),
            "coverage": coverage_status(people),
        })
    days = []
    for day in by_day.values():
        day["unique_available"] = len(day.pop("available_ids"))
        days.append(day)
    audit = response_audit(engine)
    audited_people = audit["people"] if audit["cycle"] and audit["cycle"]["status"] == "OPEN" else []
    responded = [person for person in audited_people if person["Submission Count"] > 0]
    not_responded = [person for person in audited_people if person["Submission Count"] == 0]
    return {
        "poll_key": f"cycle:{audit['cycle']['id']}" if audit["cycle"] else None,
        "poll_cycle_id": audit["cycle"]["id"] if audit["cycle"] else None,
        "poll_revision": audit["cycle"]["revision"] if audit["cycle"] else None,
        "active_volunteers": len(active),
        "responded": len(responded),
        "yet_to_respond": [person["Person"] for person in not_responded],
        "viewed_not_submitted": [person["Person"] for person in not_responded if person["Status"] == "VIEWED — NOT SUBMITTED"],
        "responded_zero": [person["Person"] for person in responded if person["Selected Count"] == 0],
        "needs_review": [person["Person"] for person in responded if person["Needs Review"]],
        "open_matches": len(fixtures),
        "days": days,
    }
