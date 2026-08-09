from collections import defaultdict
from datetime import datetime
from sqlalchemy import text

def propose(engine, unconfirmed_only=False):
    """Explainable greedy proposal: eligibility/availability first, then workload and role balance."""
    with engine.begin() as c:
        fixtures = list(c.execute(text("SELECT * FROM fixtures WHERE availability_open=true ORDER BY starts_at")).mappings())
        existing = {(r.fixture_id, r.role): r for r in c.execute(text("SELECT * FROM assignments")).mappings()}
        totals, roles = defaultdict(int), defaultdict(lambda: defaultdict(int))
        for r in existing.values(): totals[r.person_id] += 1; roles[r.person_id][r.role] += 1
        for i, f in enumerate(fixtures):
            candidates = list(c.execute(text("SELECT p.* FROM people p JOIN availability a ON a.person_id=p.id WHERE a.fixture_id=:f AND p.active=true"), {"f":f.id}).mappings())
            for role in ("umpire", "scorer"):
                old = existing.get((f.id, role))
                if old and (old.confirmed or unconfirmed_only): continue
                eligible = [p for p in candidates if p[f"can_{'score' if role == 'scorer' else 'umpire'}"]]
                other = existing.get((f.id, "scorer" if role == "umpire" else "umpire"))
                eligible = [p for p in eligible if not other or p.id != other.person_id]
                if not eligible: continue
                def rank(p):
                    preferred = 0 if p.preferred_role.lower() in (role, "either") else 1
                    praveen = 0 if (role == "scorer" and p.name == "Praveen") else (2 if p.name == "Praveen" else 1)
                    balance = roles[p.id][role]
                    adjacent = 1
                    starts_at = f.starts_at if hasattr(f.starts_at, "weekday") else datetime.fromisoformat(f.starts_at)
                    if starts_at.weekday() >= 5:
                        for near in fixtures[max(0,i-1):i+2]:
                            for rr in ("umpire","scorer"):
                                a = existing.get((near.id,rr))
                                if a and a.person_id == p.id and rr != role: adjacent = 0
                    return (adjacent, praveen, totals[p.id], balance, preferred, p.name)
                chosen = min(eligible, key=rank)
                reason = "available and eligible; lowest practical workload"
                if rank(chosen)[0] == 0: reason = "consecutive weekend pairing; opposite role and available"
                if chosen.name == "Praveen" and role == "scorer": reason = "preferred scorer; available and eligible"
                c.execute(text("INSERT INTO assignments(fixture_id,role,person_id,confirmed,reason) VALUES(:f,:r,:p,false,:why) ON CONFLICT(fixture_id,role) DO UPDATE SET person_id=:p, confirmed=false, reason=:why"), {"f":f.id,"r":role,"p":chosen.id,"why":reason})
                existing[(f.id,role)] = type("A",(),{"person_id":chosen.id,"confirmed":False})()
                totals[chosen.id] += 1; roles[chosen.id][role] += 1

def allocation_message(engine):
    with engine.connect() as c:
        data = list(c.execute(text("SELECT f.starts_at,f.home_team,f.away_team,a.role,p.name FROM assignments a JOIN fixtures f ON f.id=a.fixture_id JOIN people p ON p.id=a.person_id WHERE a.confirmed=true ORDER BY f.starts_at,a.role")).mappings())
    grouped = {}
    for r in data:
        dt = r.starts_at if hasattr(r.starts_at, "date") else datetime.fromisoformat(r.starts_at)
        grouped.setdefault((dt,r.home_team,r.away_team),{})[r.role]=r.name
    lines = ["🏏 *SGIA Umpires – Allocation*", ""]
    last_date = None
    for (dt, home, away), roles in grouped.items():
        if dt.date() != last_date: lines += [f"*{dt.strftime('%A, %d %b')}*", ""]; last_date=dt.date()
        time_label = dt.strftime("%I:%M %p").lstrip("0")
        lines += [time_label, f"{home} vs {away}", f"Umpire: {roles.get('umpire','TBC')}", f"Scorer: {roles.get('scorer','TBC')}", ""]
    return "\n".join(lines)
