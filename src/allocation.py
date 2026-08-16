from collections import defaultdict
from datetime import datetime
from sqlalchemy import text

from src.allocation_scope import current_allocation_cycle
from src.poll_audit import ensure_allocation_cycle


def _dt(value):
    return value if hasattr(value, "date") else datetime.fromisoformat(value)


def propose(engine, regenerate_unconfirmed=False):
    """Create proposals from saved votes; poll open/closed state is intentionally irrelevant."""
    with engine.begin() as c:
        ensure_allocation_cycle(c)
        cycle = current_allocation_cycle(c)
        if not cycle:
            return 0
        if regenerate_unconfirmed:
            c.execute(text("""
                DELETE FROM assignments WHERE confirmed=false AND fixture_id IN (
                    SELECT id FROM fixtures WHERE poll_cycle_id=:cycle
                )
            """), {"cycle": cycle.id})

        fixtures = list(c.execute(text("""
            SELECT f.* FROM fixtures f
            WHERE f.poll_cycle_id=:cycle AND f.poll_state IN ('OPEN','FROZEN')
              AND EXISTS (SELECT 1 FROM availability a WHERE a.fixture_id=f.id)
            ORDER BY f.starts_at
        """), {"cycle": cycle.id}).mappings())
        existing = {(r.fixture_id, r.role): r for r in c.execute(text("SELECT * FROM assignments")).mappings()}
        season = {
            row.person_id: (int(row.games_completed), int(row.total_assigned))
            for row in c.execute(text("SELECT person_id,games_completed,total_assigned FROM season_workload")).mappings()
        }
        totals, roles, schedule = defaultdict(int), defaultdict(lambda: defaultdict(int)), defaultdict(list)
        for (fixture_id, role), assignment in existing.items():
            fixture = next((f for f in fixtures if f.id == fixture_id), None)
            totals[assignment.person_id] += 1
            roles[assignment.person_id]["scorer" if role == "scorer" else "umpire"] += 1
            if fixture: schedule[assignment.person_id].append((_dt(fixture.starts_at), role))

        for index, fixture in enumerate(fixtures):
            candidates = list(c.execute(text("""
                SELECT p.* FROM people p JOIN availability a ON a.person_id=p.id
                WHERE a.fixture_id=:fixture AND p.active=true
            """), {"fixture": fixture.id}).mappings())
            starts = _dt(fixture.starts_at)
            previous = fixtures[index - 1] if index else None
            consecutive_previous = previous and _dt(previous.starts_at).date() == starts.date()

            for role in ("umpire_1", "umpire_2", "scorer"):
                if (fixture.id, role) in existing:
                    continue  # every retained assignment, especially confirmed ones, is immutable here
                capability = "can_score" if role == "scorer" else "can_umpire"
                role_group = "scorer" if role == "scorer" else "umpire"
                other_group = "umpire" if role_group == "scorer" else "scorer"
                assigned_here = {assignment.person_id for (fixture_id, _), assignment in existing.items() if fixture_id == fixture.id}
                eligible = [p for p in candidates if p[capability] and p.id not in assigned_here]
                if not eligible:
                    continue

                def rank(person):
                    back_to_back = 1
                    if starts.weekday() >= 5 and consecutive_previous:
                        prior_people = {
                            assignment.person_id for (fixture_id, prior_role), assignment in existing.items()
                            if fixture_id == previous.id and ((other_group == "scorer" and prior_role == "scorer") or (other_group == "umpire" and prior_role.startswith("umpire_")))
                        }
                        if person.id in prior_people:
                            back_to_back = 0

                    same_day_times = [d for d, _ in schedule[person.id] if d.date() == starts.date()]
                    already_paired = int(len(same_day_times) >= 2)
                    has_gap = int(any(abs((starts - d).total_seconds()) > 5 * 3600 for d in same_day_times))
                    weekday_complement = 1
                    if starts.weekday() < 5 and roles[person.id][other_group] > roles[person.id][role_group]:
                        weekday_complement = 0
                    praveen = 0 if role == "scorer" and person.name == "Praveen" else 1
                    if role_group == "umpire" and person.name == "Praveen":
                        praveen = 3
                    preferred = 0 if person.preferred_role.lower() in (role_group, "either") else 1
                    weekend_priority = (already_paired, back_to_back, has_gap) if starts.weekday() >= 5 else (0, 0, 0)
                    completed, assigned = season.get(person.id, (0, 0))
                    return (*weekend_priority, weekday_complement, praveen, preferred,
                            completed, assigned, totals[person.id], roles[person.id][role_group], person.name)

                chosen = min(eligible, key=rank)
                ranking = rank(chosen)
                if starts.weekday() >= 5 and ranking[1] == 0:
                    reason = "Back-to-back pairing preferred; workload balanced"
                elif starts.weekday() < 5 and roles[chosen.id][other_group] > roles[chosen.id][role_group]:
                    reason = "Weekday umpire/scorer balance; workload balanced"
                elif chosen.name == "Praveen" and role == "scorer":
                    reason = "Preferred scorer; available and eligible"
                else:
                    reason = "Available and balances season workload"
                c.execute(text("""
                    INSERT INTO assignments(fixture_id,role,person_id,confirmed,reason)
                    VALUES(:fixture,:role,:person,false,:reason)
                """), {"fixture": fixture.id, "role": role, "person": chosen.id, "reason": reason})
                assignment = type("Assignment", (), {"person_id": chosen.id, "confirmed": False})()
                existing[(fixture.id, role)] = assignment
                totals[chosen.id] += 1
                roles[chosen.id][role_group] += 1
                schedule[chosen.id].append((starts, role))


def allocation_message(engine, cycle_id=None):
    with engine.connect() as c:
        if cycle_id is None:
            cycle = current_allocation_cycle(c)
            if not cycle:
                return "\U0001f3cf *SGIA Umpires \u2013 Final Allocation*\n"
            cycle_id = cycle.id
        data = list(c.execute(text("""
            SELECT f.starts_at,f.home_team,f.away_team,a.role,p.name
            FROM assignments a JOIN fixtures f ON f.id=a.fixture_id JOIN people p ON p.id=a.person_id
            WHERE a.confirmed=true AND a.status='ASSIGNED' AND f.poll_cycle_id=:cycle
              AND f.poll_state<>'CLOSED'
            ORDER BY f.starts_at,a.role
        """), {"cycle": cycle_id}).mappings())
    grouped = {}
    for row in data:
        dt = _dt(row.starts_at)
        grouped.setdefault((dt, row.home_team, row.away_team), {})[row.role] = row.name
    lines = ["🏏 *SGIA Umpires – Final Allocation*", ""]
    for (dt, home, away), assigned in grouped.items():
        if not all(role in assigned for role in ("umpire_1", "umpire_2", "scorer")):
            continue
        lines += [f"{dt.strftime('%a %d-%b-%Y')} | {dt.strftime('%I:%M %p').lstrip('0')}",
                  f"{home} vs {away}",
                  f"Umpire 1: {assigned.get('umpire_1', 'TBC')}",
                  f"Umpire 2: {assigned.get('umpire_2', 'TBC')}",
                  f"Scorer: {assigned.get('scorer', 'TBC')}", ""]
    return "\n".join(lines)
