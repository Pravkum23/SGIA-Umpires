from datetime import datetime

from sqlalchemy import text

from .weekly import singapore_now


def close_past_matches(engine, now=None):
    """Complete prior match days in Singapore and retain published history."""
    today = datetime.combine((now or singapore_now()).date(), datetime.min.time())
    with engine.begin() as connection:
        completed = connection.execute(text("""
            UPDATE fixtures SET match_status='COMPLETED', availability_open=false,
                poll_state=CASE WHEN poll_state='PUBLISHED' THEN 'PUBLISHED' ELSE 'CLOSED' END
            WHERE match_status='SCHEDULED' AND starts_at<:today
            RETURNING id, poll_cycle_id
        """), {"today": today}).mappings().all()
        if completed:
            connection.execute(text("""
                INSERT INTO fixture_events(fixture_id,event_type,previous_status,new_status)
                VALUES(:id,'MATCH_COMPLETED','SCHEDULED','COMPLETED')
            """), [{"id": match["id"]} for match in completed])
        for cycle in {match["poll_cycle_id"] for match in completed if match["poll_cycle_id"] is not None}:
            connection.execute(text("""
                UPDATE poll_cycles
                SET status=CASE WHEN EXISTS (
                    SELECT 1 FROM fixtures WHERE poll_cycle_id=:cycle AND poll_state='PUBLISHED'
                ) THEN 'PUBLISHED' ELSE 'CLOSED' END
                WHERE id=:cycle AND status IN ('OPEN','FROZEN') AND NOT EXISTS (
                    SELECT 1 FROM fixtures WHERE poll_cycle_id=:cycle
                    AND match_status='SCHEDULED' AND poll_state IN ('OPEN','FROZEN')
                )
            """), {"cycle": cycle})
        return len(completed)
