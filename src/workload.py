from sqlalchemy import text


MATCH_STATUSES = {"SCHEDULED", "COMPLETED", "CANCELLED"}
EVENT_BY_STATUS = {
    "COMPLETED": "MATCH_COMPLETED",
    "CANCELLED": "MATCH_CANCELLED",
    "SCHEDULED": "MATCH_RESTORED",
}


def workload_totals(tournament_filter=False):
    scope = " AND d.tournament=:tournament" if tournament_filter else ""
    return f"""
        SELECT p.id person_id,p.name,
          COUNT(DISTINCT CASE WHEN d.duty_state='completed' THEN d.fixture_id END) games_completed,
          COUNT(DISTINCT CASE WHEN d.duty_state='completed' AND d.role IN ('umpire_1','umpire_2') THEN d.fixture_id END) umpire_completed,
          COUNT(DISTINCT CASE WHEN d.duty_state='completed' AND d.role='scorer' THEN d.fixture_id END) scorer_completed,
          COUNT(DISTINCT CASE WHEN d.duty_state='upcoming' THEN d.fixture_id END) upcoming_duties,
          COUNT(DISTINCT d.fixture_id) total_assigned,
          COUNT(DISTINCT CASE WHEN d.duty_state='started' THEN d.fixture_id END) awaiting_completion
        FROM people p LEFT JOIN workload_duties d ON d.person_id=p.id{scope}
        WHERE p.active=true GROUP BY p.id,p.name
    """


def season_workload(engine, person_id=None, tournament=None):
    source = "season_workload"
    params = {}
    if tournament is not None:
        source = "(" + workload_totals(tournament_filter=True) + ") scoped"
        params["tournament"] = tournament
    where = ""
    if person_id is not None:
        where = " WHERE person_id=:person"
        params["person"] = person_id
    order = "total_assigned DESC,name" if tournament is not None else "games_completed,total_assigned,name"
    with engine.connect() as connection:
        return [dict(row) for row in connection.execute(
            text(f"SELECT * FROM {source}{where} ORDER BY {order}"), params
        ).mappings()]


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
