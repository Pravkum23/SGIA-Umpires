from sqlalchemy import text


MATCH_STATUSES = {"SCHEDULED", "COMPLETED", "CANCELLED"}
EVENT_BY_STATUS = {
    "COMPLETED": "MATCH_COMPLETED",
    "CANCELLED": "MATCH_CANCELLED",
    "SCHEDULED": "MATCH_RESTORED",
}


def season_workload(engine, person_id=None, tournament=None):
    """Return the shared current-season workload view, ordered for fair allocation."""
    if tournament is not None:
        from .weekly import singapore_now
        with engine.connect() as connection:
            return [dict(row) for row in connection.execute(text("""
                WITH duties AS (
                    SELECT a.person_id, f.id, a.role, f.match_status, f.starts_at
                    FROM fixtures f JOIN assignments a ON a.fixture_id=f.id
                    WHERE f.tournament=:tournament AND a.confirmed=true AND a.status='ASSIGNED'
                      AND (f.match_status='COMPLETED' OR (f.poll_state='PUBLISHED' AND f.match_status='SCHEDULED'))
                )
                SELECT p.id person_id, p.name,
                    COUNT(DISTINCT CASE WHEN d.match_status='COMPLETED' THEN d.id END) games_completed,
                    COUNT(DISTINCT CASE WHEN d.match_status='COMPLETED' AND d.role IN ('umpire_1','umpire_2') THEN d.id END) umpire_completed,
                    COUNT(DISTINCT CASE WHEN d.match_status='COMPLETED' AND d.role='scorer' THEN d.id END) scorer_completed,
                    COUNT(DISTINCT CASE WHEN d.match_status='SCHEDULED' AND d.starts_at>:now THEN d.id END) upcoming_duties,
                    COUNT(DISTINCT CASE WHEN d.match_status='SCHEDULED' AND d.starts_at<=:now THEN d.id END) awaiting_completion,
                    COUNT(DISTINCT d.id) total_assigned
                FROM people p LEFT JOIN duties d ON d.person_id=p.id
                WHERE p.active=true AND (CAST(:person AS INTEGER) IS NULL OR p.id=:person)
                GROUP BY p.id,p.name ORDER BY total_assigned DESC,p.name
            """), {"tournament": tournament, "person": person_id, "now": singapore_now()}).mappings()]
    where = " WHERE person_id=:person" if person_id is not None else ""
    with engine.connect() as connection:
        return [dict(row) for row in connection.execute(text(f"""
            SELECT person_id,name,games_completed,umpire_completed,scorer_completed,
                   upcoming_duties,total_assigned
            FROM season_workload{where}
            ORDER BY games_completed,total_assigned,name
        """), {"person": person_id} if person_id is not None else {}).mappings()]


def set_match_status(engine, fixture_id, new_status):
    """Change match status with validation and an append-only audit event."""
    new_status = str(new_status).upper()
    if new_status not in MATCH_STATUSES:
        raise ValueError("Unsupported match status")
    with engine.begin() as connection:
        fixture = connection.execute(text(
            "SELECT match_status,poll_state FROM fixtures WHERE id=:fixture"
        ), {"fixture": fixture_id}).mappings().first()
        if not fixture:
            raise ValueError("Fixture not found")
        previous = fixture.match_status
        if previous == new_status:
            return False
        if new_status == "COMPLETED" and fixture.poll_state != "PUBLISHED":
            raise ValueError("Publish the fixture before marking it completed.")
        connection.execute(text(
            "UPDATE fixtures SET match_status=:status WHERE id=:fixture"
        ), {"status": new_status, "fixture": fixture_id})
        connection.execute(text("""
            INSERT INTO fixture_events(fixture_id,event_type,previous_status,new_status)
            VALUES(:fixture,:event,:previous,:new)
        """), {
            "fixture": fixture_id,
            "event": EVENT_BY_STATUS[new_status],
            "previous": previous,
            "new": new_status,
        })
        return True


def fixture_history(engine):
    with engine.connect() as connection:
        return [dict(row) for row in connection.execute(text("""
            SELECT e.created_at,f.starts_at,f.home_team,f.away_team,e.event_type action,
                   e.previous_status,e.new_status
            FROM fixture_events e JOIN fixtures f ON f.id=e.fixture_id
            ORDER BY e.created_at DESC,e.id DESC
        """)).mappings()]
