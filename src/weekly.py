from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import text

from .poll_audit import _insert_cycle, current_cycle


def singapore_now():
    return datetime.now(ZoneInfo("Asia/Singapore")).replace(tzinfo=None)


def week_bounds(day):
    monday = day - timedelta(days=day.weekday())
    return datetime.combine(monday, datetime.min.time()), datetime.combine(monday + timedelta(days=7), datetime.min.time())


WEEK_FILTER = """
    tournament=:tournament AND starts_at>=:start AND starts_at<:end
    AND match_status='SCHEDULED'
"""


def selected_filter(fixture_ids, scope):
    if fixture_ids is None:
        return ""
    ids = sorted({int(fid) for fid in fixture_ids})
    if not ids:
        return " AND 1=0"
    for index, fid in enumerate(ids):
        scope[f"selected_{index}"] = fid
    return " AND id IN (" + ",".join(f":selected_{i}" for i in range(len(ids))) + ")"


def close_week(engine, tournament, start, end, fixture_ids=None):
    scope = {"tournament": tournament, "start": start, "end": end}
    selected = selected_filter(fixture_ids, scope)
    with engine.begin() as connection:
        lock = " FOR UPDATE" if engine.dialect.name == "postgresql" else ""
        fixtures = connection.execute(text(f"""
            SELECT id,poll_cycle_id FROM fixtures WHERE {WEEK_FILTER}
            AND poll_state IN ('OPEN','FROZEN'){selected} ORDER BY id{lock}
        """), scope).mappings().all()
        for fixture in fixtures:
            ready = connection.execute(text("""
                SELECT COUNT(DISTINCT role) FROM assignments WHERE fixture_id=:id
                AND confirmed=true AND status='ASSIGNED' AND role IN ('umpire_1','umpire_2','scorer')
            """), {"id": fixture["id"]}).scalar_one()
            if ready != 3:
                raise ValueError("Save both umpires and the scorer for each selected match first.")
        for fixture in fixtures:
            connection.execute(text("""
                UPDATE fixtures SET poll_state='PUBLISHED',availability_open=false WHERE id=:id
            """), {"id": fixture["id"]})
        for cycle in {f["poll_cycle_id"] for f in fixtures if f["poll_cycle_id"] is not None}:
            connection.execute(text("""
                UPDATE poll_cycles SET status='PUBLISHED',published_at=CURRENT_TIMESTAMP
                WHERE id=:cycle AND NOT EXISTS (
                    SELECT 1 FROM fixtures WHERE poll_cycle_id=:cycle AND poll_state IN ('OPEN','FROZEN')
                )
            """), {"cycle": cycle})


def enable_week(engine, tournament, start, end, fixture_ids=None):
    scope = {"tournament": tournament, "start": start, "end": end, "now": singapore_now()}
    selected = selected_filter(fixture_ids, scope)
    with engine.begin() as connection:
        if engine.dialect.name == "postgresql":
            connection.execute(text("LOCK TABLE poll_cycles IN SHARE ROW EXCLUSIVE MODE"))
        eligible = WEEK_FILTER + " AND starts_at>:now AND poll_state='CLOSED'" + selected
        count = connection.execute(text(f"SELECT COUNT(*) FROM fixtures WHERE {eligible}"), scope).scalar_one()
        if not count:
            return
        cycle = current_cycle(connection, ("OPEN",))
        scope["cycle"] = cycle.id if cycle else _insert_cycle(connection)
        result = connection.execute(text(f"""
            UPDATE fixtures SET poll_state='OPEN',availability_open=true,poll_cycle_id=:cycle
            WHERE {eligible}
        """), scope)
        if cycle and result.rowcount:
            connection.execute(text("UPDATE poll_cycles SET revision=revision+1 WHERE id=:cycle"), scope)


def reopen_selected(engine, tournament, start, end, fixture_ids):
    """Withdraw future published duties while retaining allocations and responses."""
    scope = {"tournament": tournament, "start": start, "end": end, "now": singapore_now()}
    selected = selected_filter(fixture_ids, scope)
    with engine.begin() as connection:
        lock = " FOR UPDATE" if engine.dialect.name == "postgresql" else ""
        fixtures = connection.execute(text(f"""
            SELECT id,poll_cycle_id FROM fixtures WHERE {WEEK_FILTER}
            AND starts_at>:now AND poll_state='PUBLISHED'{selected} ORDER BY id{lock}
        """), scope).mappings().all()
        for fixture in fixtures:
            connection.execute(text("UPDATE fixtures SET poll_state='FROZEN',availability_open=false WHERE id=:id"), {"id": fixture["id"]})
        for cycle in {f["poll_cycle_id"] for f in fixtures if f["poll_cycle_id"] is not None}:
            connection.execute(text("""
                UPDATE poll_cycles SET status='FROZEN',frozen_at=CURRENT_TIMESTAMP
                WHERE id=:cycle AND status='PUBLISHED'
            """), {"cycle": cycle})
