from collections import defaultdict
from datetime import datetime
from sqlalchemy import text


def _dt(value):
    return value if hasattr(value, "date") else datetime.fromisoformat(value)


def propose(engine, regenerate_unconfirmed=False):
    """Create proposals from saved votes; poll open/closed state is intentionally irrelevant."""
    with engine.begin() as c:
        if regenerate_unconfirmed:
            c.execute(text("DELETE FROM assignments WHERE confirmed=false"))

        fixtures = list(c.execute(text("""
            SELECT f.* FROM fixtures f
            WHERE EXISTS (SELECT 1 FROM availability a WHERE a.fixture_id=f.id)
            ORDER BY f.starts_at
        """)).mappings())
        existing = {(r.fixture_id, r.role): r for r in c.execute(text("SELECT * FROM assignments")).mappings()}
        totals, roles, schedule = defaultdict(int), defaultdict(lambda: defaultdict(int)), defaultdict(list)
        for (fixture_id, role), assignment in existing.items():
            fixture = next((f for f in fixtures if f.id == fixture_id), None)
            totals[assignment.person_id] += 1
            roles[assignment.person_id][role] += 1
            if fixture: schedule[assignment.person_id].append((_dt(fixture.starts_at), role))

        for index, fixture in enumerate(fixtures):
            candidates = list(c.execute(text("""
                SELECT p.* FROM people p JOIN availability a ON a.person_id=p.id
                WHERE a.fixture_id=:fixture AND p.active=true
            """), {"fixture": fixture.id}).mappings())
            starts = _dt(fixture.starts_at)
            previous = fixtures[index - 1] if index else None
            consecutive_previous = previous and _dt(previous.starts_at).date() == starts.date()

            for role in ("umpire", "scorer"):
                if (fixture.id, role) in existing:
                    continue  # every retained assignment, especially confirmed ones, is immutable here
                capability = "can_score" if role == "scorer" else "can_umpire"
                other_role = "scorer" if role == "umpire" else "umpire"
                other_here = existing.get((fixture.id, other_role))
                eligible = [p for p in candidates if p[capability] and (not other_here or p.id != other_here.person_id)]
                if not eligible:
                    continue

                def rank(person):
                    back_to_back = 1
                    if starts.weekday() >= 5 and consecutive_previous:
                        prior = existing.get((previous.id, other_role))
                        if prior and prior.person_id == person.id:
                            back_to_back = 0

                    same_day_times = [d for d, _ in schedule[person.id] if d.date() == starts.date()]
                    already_paired = int(len(same_day_times) >= 2)
                    has_gap = int(any(abs((starts - d).total_seconds()) > 5 * 3600 for d in same_day_times))
                    weekday_complement = 1
                    if starts.weekday() < 5 and roles[person.id][other_role] > roles[person.id][role]:
                        weekday_complement = 0
                    praveen = 0 if role == "scorer" and person.name == "Praveen" else 1
                    if role == "umpire" and person.name == "Praveen":
                        praveen = 3
                    preferred = 0 if person.preferred_role.lower() in (role, "either") else 1
                    weekend_priority = (already_paired, back_to_back, has_gap) if starts.weekday() >= 5 else (0, 0, 0)
                    return (*weekend_priority, weekday_complement, praveen, totals[person.id], roles[person.id][role], preferred, person.name)

                chosen = min(eligible, key=rank)
                ranking = rank(chosen)
                if starts.weekday() >= 5 and ranking[1] == 0:
                    reason = "back-to-back weekend pairing; opposite role; one ground visit"
                elif starts.weekday() < 5 and roles[chosen.id][other_role] > roles[chosen.id][role]:
                    reason = "weekday umpire/scorer balance; available and eligible"
                elif chosen.name == "Praveen" and role == "scorer":
                    reason = "preferred scorer; available and eligible"
                else:
                    reason = "available and eligible; lowest practical workload"
                c.execute(text("""
                    INSERT INTO assignments(fixture_id,role,person_id,confirmed,reason)
                    VALUES(:fixture,:role,:person,false,:reason)
                """), {"fixture": fixture.id, "role": role, "person": chosen.id, "reason": reason})
                assignment = type("Assignment", (), {"person_id": chosen.id, "confirmed": False})()
                existing[(fixture.id, role)] = assignment
                totals[chosen.id] += 1
                roles[chosen.id][role] += 1
                schedule[chosen.id].append((starts, role))


def allocation_message(engine):
    with engine.connect() as c:
        data = list(c.execute(text("""
            SELECT f.starts_at,f.home_team,f.away_team,a.role,p.name
            FROM assignments a JOIN fixtures f ON f.id=a.fixture_id JOIN people p ON p.id=a.person_id
            WHERE a.confirmed=true ORDER BY f.starts_at,a.role
        """)).mappings())
    grouped = {}
    for row in data:
        dt = _dt(row.starts_at)
        grouped.setdefault((dt, row.home_team, row.away_team), {})[row.role] = row.name
    lines = ["🏏 *SGIA Umpires – Allocation*", ""]
    last_date = None
    for (dt, home, away), assigned in grouped.items():
        if dt.date() != last_date:
            lines += [f"*{dt.strftime('%A, %d %b')}*", ""]
            last_date = dt.date()
        lines += [dt.strftime("%I:%M %p").lstrip("0"), f"{home} vs {away}",
                  f"Umpire: {assigned.get('umpire', 'TBC')}", f"Scorer: {assigned.get('scorer', 'TBC')}", ""]
    return "\n".join(lines)
