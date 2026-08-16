from sqlalchemy import text


def current_allocation_cycle(connection):
    """Return the one unpublished cycle the admin is currently preparing.

    A frozen cycle takes priority over an open cycle so opening a later poll
    cannot displace an allocation that is still being reviewed. Unknown future
    workflow states remain eligible after the two explicit states, provided
    they are neither closed nor published.
    """
    return connection.execute(text("""
        SELECT c.id,c.status,c.revision,c.created_at,c.opened_at,c.frozen_at,c.published_at
        FROM poll_cycles c
        WHERE c.status NOT IN ('PUBLISHED','CLOSED')
          AND EXISTS (SELECT 1 FROM fixtures f WHERE f.poll_cycle_id=c.id)
        ORDER BY CASE c.status WHEN 'FROZEN' THEN 1 WHEN 'OPEN' THEN 2 ELSE 3 END,
                 c.id DESC
        LIMIT 1
    """)).mappings().first()


def current_allocation_summary(engine):
    with engine.connect() as connection:
        cycle = current_allocation_cycle(connection)
        if not cycle:
            return None
        summary = connection.execute(text("""
            SELECT MIN(starts_at) starts_at,MAX(starts_at) ends_at,COUNT(*) match_count
            FROM fixtures WHERE poll_cycle_id=:cycle AND poll_state<>'CLOSED'
        """), {"cycle": cycle.id}).mappings().one()
        return {**dict(cycle), **dict(summary)}


def published_allocation_cycles(engine):
    """List immutable published batches without mixing them into working data."""
    with engine.connect() as connection:
        return [dict(row) for row in connection.execute(text("""
            SELECT c.id,c.status,c.published_at,MIN(f.starts_at) starts_at,
                   MAX(f.starts_at) ends_at,COUNT(DISTINCT f.id) match_count
            FROM poll_cycles c JOIN fixtures f ON f.poll_cycle_id=c.id
            WHERE c.status='PUBLISHED' AND f.poll_state='PUBLISHED'
            GROUP BY c.id,c.status,c.published_at,c.created_at
            ORDER BY COALESCE(c.published_at,c.created_at) DESC,c.id DESC
        """)).mappings()]
