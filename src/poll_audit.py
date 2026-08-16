import csv
import hashlib
import json
from datetime import datetime, timezone
from io import StringIO
from zoneinfo import ZoneInfo

from sqlalchemy import text


SGIA_TIMEZONE = "Asia/Singapore"
AUDIT_COLUMNS = [
    "Person", "Status", "First Viewed SGT", "First Submitted SGT", "Last Updated SGT",
    "Submission Count", "Selected Count", "Current Selections", "Needs Review",
    "Device", "Browser", "Evidence Source",
]


def coarse_client(user_agent):
    """Reduce a user agent to approved, non-identifying categories only."""
    value = (user_agent or "").lower()
    if "ipad" in value or "tablet" in value or ("android" in value and "mobile" not in value):
        device = "Tablet"
    elif any(token in value for token in ("mobile", "iphone", "ipod", "android")):
        device = "Mobile"
    elif any(token in value for token in ("windows", "macintosh", "x11", "linux")):
        device = "Desktop"
    else:
        device = "Unknown"
    if "edg/" in value or "edge/" in value:
        browser = "Edge"
    elif "firefox/" in value or "fxios/" in value:
        browser = "Firefox"
    elif "chrome/" in value or "crios/" in value:
        browser = "Chrome"
    elif "safari/" in value:
        browser = "Safari"
    else:
        browser = "Other"
    return device, browser


def _json_ids(values):
    return json.dumps(sorted({int(value) for value in values}), separators=(",", ":"))


def _parse_ids(value):
    try:
        parsed = json.loads(value or "[]")
        return [int(item) for item in parsed if isinstance(item, int) or str(item).isdigit()]
    except (TypeError, ValueError, json.JSONDecodeError):
        return []


def _insert_cycle(connection, status="OPEN"):
    timestamp_column = {"OPEN": "opened_at", "FROZEN": "frozen_at", "PUBLISHED": "published_at"}.get(status)
    columns = ",opened_at" if timestamp_column == "opened_at" else ",frozen_at" if timestamp_column == "frozen_at" else ",published_at" if timestamp_column == "published_at" else ""
    values = ",CURRENT_TIMESTAMP" if columns else ""
    return connection.execute(text(
        f"INSERT INTO poll_cycles(status,revision{columns}) VALUES(:status,1{values}) RETURNING id"
    ), {"status": status}).scalar_one()


def current_cycle(connection, statuses=("OPEN", "FROZEN", "PUBLISHED")):
    if not statuses:
        return None
    placeholders = ",".join(f":state_{index}" for index in range(len(statuses)))
    params = {f"state_{index}": status for index, status in enumerate(statuses)}
    return connection.execute(text(f"""
        SELECT id,status,revision,created_at,opened_at,frozen_at,published_at
        FROM poll_cycles WHERE status IN ({placeholders})
        ORDER BY CASE status WHEN 'OPEN' THEN 1 WHEN 'FROZEN' THEN 2 WHEN 'PUBLISHED' THEN 3 ELSE 4 END,id DESC
        LIMIT 1
    """), params).mappings().first()


def ensure_open_cycle(connection):
    """Recover safely when an older integration opened fixtures directly."""
    cycle = current_cycle(connection, ("OPEN",))
    open_ids = [row.id for row in connection.execute(text(
        "SELECT id FROM fixtures WHERE poll_state='OPEN' ORDER BY id"
    ))]
    if not open_ids:
        return None
    if not cycle:
        cycle_id = _insert_cycle(connection)
        cycle = connection.execute(text("SELECT id,status,revision FROM poll_cycles WHERE id=:cycle"), {
            "cycle": cycle_id,
        }).mappings().first()
    for fixture_id in open_ids:
        connection.execute(text("""
            UPDATE fixtures SET poll_cycle_id=:cycle WHERE id=:fixture AND poll_cycle_id IS NULL
        """), {"cycle": cycle.id, "fixture": fixture_id})
    return cycle


def ensure_allocation_cycle(connection):
    """Attach legacy unscoped availability to one working allocation cycle.

    New data is always scoped when the poll opens. This fallback exists only
    for databases/integrations created before poll_cycle_id was introduced.
    It runs only when there is no current unpublished cycle.
    """
    from src.allocation_scope import current_allocation_cycle

    cycle = current_allocation_cycle(connection)
    if cycle:
        return cycle
    open_cycle = ensure_open_cycle(connection)
    if open_cycle:
        return open_cycle
    candidates = list(connection.execute(text("""
        SELECT f.id,f.poll_state FROM fixtures f
        WHERE f.poll_cycle_id IS NULL
          AND f.poll_state IN ('FROZEN','CLOSED')
          AND EXISTS (SELECT 1 FROM availability a WHERE a.fixture_id=f.id)
          AND NOT EXISTS (SELECT 1 FROM assignments x WHERE x.fixture_id=f.id)
        ORDER BY CASE f.poll_state WHEN 'FROZEN' THEN 1 ELSE 2 END,
                 f.starts_at
    """)).mappings())
    if not candidates:
        return None
    preferred_state = candidates[0].poll_state
    selected = [row.id for row in candidates if row.poll_state == preferred_state]
    cycle_id = _insert_cycle(connection, "FROZEN")
    for fixture_id in selected:
        connection.execute(text("""
            UPDATE fixtures SET poll_cycle_id=:cycle,poll_state='FROZEN',availability_open=false
            WHERE id=:fixture
        """), {
            "cycle": cycle_id, "fixture": fixture_id,
        })
    return connection.execute(text("SELECT id,status,revision FROM poll_cycles WHERE id=:cycle"), {
        "cycle": cycle_id,
    }).mappings().first()


def update_open_slots(engine, fixture_ids):
    """Replace the OPEN fixture set without changing the cycle identity."""
    desired_input = {int(value) for value in fixture_ids}
    with engine.begin() as connection:
        valid = {row.id for row in connection.execute(text(
            "SELECT id FROM fixtures WHERE poll_state NOT IN ('FROZEN','PUBLISHED')"
        ))}
        desired = desired_input & valid
        before = {row.id for row in connection.execute(text("SELECT id FROM fixtures WHERE poll_state='OPEN'"))}
        cycle = current_cycle(connection, ("OPEN",))
        created = False
        if not cycle and desired:
            cycle_id = _insert_cycle(connection)
            created = True
        elif cycle:
            cycle_id = cycle.id
        else:
            return None
        changed = before != desired
        if before - desired:
            connection.execute(text("UPDATE fixtures SET poll_state='CLOSED',availability_open=false WHERE poll_state='OPEN'"))
        for fixture_id in desired:
            connection.execute(text("""
                UPDATE fixtures SET poll_state='OPEN',availability_open=true,poll_cycle_id=:cycle WHERE id=:fixture
            """), {"cycle": cycle_id, "fixture": fixture_id})
        if changed and not created:
            connection.execute(text("UPDATE poll_cycles SET revision=revision+1 WHERE id=:cycle"), {"cycle": cycle_id})
        if not desired:
            connection.execute(text("UPDATE poll_cycles SET status='CLOSED' WHERE id=:cycle"), {"cycle": cycle_id})
        return cycle_id


def set_fixture_poll_state(engine, fixture_id, new_state):
    if new_state not in {"OPEN", "FROZEN", "PUBLISHED", "CLOSED"}:
        raise ValueError("Unsupported poll state")
    with engine.begin() as connection:
        fixture = connection.execute(text(
            "SELECT id,poll_state,poll_cycle_id FROM fixtures WHERE id=:fixture"
        ), {"fixture": fixture_id}).mappings().first()
        if not fixture:
            raise ValueError("Fixture not found")
        cycle = current_cycle(connection, ("OPEN",))
        created = False
        cycle_id = fixture.poll_cycle_id
        if new_state == "OPEN":
            if not cycle:
                cycle_id = _insert_cycle(connection)
                created = True
            else:
                cycle_id = cycle.id
            connection.execute(text("""
                UPDATE fixtures SET poll_state='OPEN',availability_open=true,poll_cycle_id=:cycle WHERE id=:fixture
            """), {"cycle": cycle_id, "fixture": fixture_id})
            if fixture.poll_state != "OPEN" and not created:
                connection.execute(text("UPDATE poll_cycles SET revision=revision+1 WHERE id=:cycle"), {"cycle": cycle_id})
            return cycle_id
        connection.execute(text("UPDATE fixtures SET poll_state=:state,availability_open=false WHERE id=:fixture"), {
            "state": new_state, "fixture": fixture_id,
        })
        if fixture.poll_state == "OPEN" and cycle_id:
            connection.execute(text("UPDATE poll_cycles SET revision=revision+1 WHERE id=:cycle"), {"cycle": cycle_id})
            remaining = connection.execute(text(
                "SELECT COUNT(*) FROM fixtures WHERE poll_cycle_id=:cycle AND poll_state='OPEN'"
            ), {"cycle": cycle_id}).scalar_one()
            if not remaining:
                column = "frozen_at" if new_state == "FROZEN" else "published_at" if new_state == "PUBLISHED" else None
                if column:
                    connection.execute(text(f"UPDATE poll_cycles SET status=:state,{column}=CURRENT_TIMESTAMP WHERE id=:cycle"), {
                        "state": new_state, "cycle": cycle_id,
                    })
                else:
                    connection.execute(text("UPDATE poll_cycles SET status='CLOSED' WHERE id=:cycle"), {"cycle": cycle_id})
        elif fixture.poll_state == "FROZEN" and new_state == "PUBLISHED" and cycle_id:
            remaining = connection.execute(text(
                "SELECT COUNT(*) FROM fixtures WHERE poll_cycle_id=:cycle AND poll_state='FROZEN'"
            ), {"cycle": cycle_id}).scalar_one()
            if not remaining:
                connection.execute(text("UPDATE poll_cycles SET status='PUBLISHED',published_at=CURRENT_TIMESTAMP WHERE id=:cycle"), {"cycle": cycle_id})
        return cycle_id


def transition_poll_state(engine, source, target):
    if (source, target) not in {("OPEN", "FROZEN"), ("FROZEN", "PUBLISHED")}:
        raise ValueError("Unsupported poll transition")
    with engine.begin() as connection:
        cycle = current_cycle(connection, (source,))
        if cycle:
            connection.execute(text("""
                UPDATE fixtures SET poll_state=:target,availability_open=false
                WHERE poll_state=:source AND poll_cycle_id=:cycle
            """), {"source": source, "target": target, "cycle": cycle.id})
            timestamp = "frozen_at" if target == "FROZEN" else "published_at"
            connection.execute(text(f"UPDATE poll_cycles SET status=:target,{timestamp}=CURRENT_TIMESTAMP WHERE id=:cycle"), {
                "target": target, "cycle": cycle.id,
            })
            return cycle.id
        return None


def _cycle_for_fixture_state(connection, fixture_state):
    return connection.execute(text("""
        SELECT c.id,c.revision,c.status FROM poll_cycles c
        WHERE EXISTS (SELECT 1 FROM fixtures f WHERE f.poll_cycle_id=c.id AND f.poll_state=:state)
        ORDER BY c.id DESC LIMIT 1
    """), {"state": fixture_state}).mappings().first()


def record_poll_view(engine, person_id, fixture_state="OPEN", device="Unknown", browser="Other"):
    with engine.begin() as connection:
        cycle = _cycle_for_fixture_state(connection, fixture_state)
        if not cycle:
            return None
        existing = connection.execute(text("""
            SELECT first_viewed_at,evidence_source FROM poll_participation
            WHERE poll_cycle_id=:cycle AND person_id=:person
        """), {"cycle": cycle.id, "person": person_id}).mappings().first()
        source = existing.evidence_source if existing and existing.evidence_source == "LEGACY_BACKFILL" else "LIVE"
        connection.execute(text("""
            INSERT INTO poll_participation(
                poll_cycle_id,person_id,first_viewed_at,last_viewed_at,selected_count,
                selected_fixture_ids,last_poll_revision,device_type,browser,evidence_source
            ) VALUES(:cycle,:person,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,0,'[]',:revision,:device,:browser,:source)
            ON CONFLICT(poll_cycle_id,person_id) DO UPDATE SET
                first_viewed_at=COALESCE(poll_participation.first_viewed_at,CURRENT_TIMESTAMP),
                last_viewed_at=CURRENT_TIMESTAMP,device_type=:device,browser=:browser
        """), {"cycle": cycle.id, "person": person_id, "revision": cycle.revision, "device": device, "browser": browser, "source": source})
        connection.execute(text("""
            INSERT INTO poll_activity_events(
                poll_cycle_id,person_id,event_type,selected_count,selected_fixture_ids,
                poll_revision,device_type,browser
            ) VALUES(:cycle,:person,'VIEWED',0,'[]',:revision,:device,:browser)
            ON CONFLICT DO NOTHING
        """), {"cycle": cycle.id, "person": person_id, "revision": cycle.revision, "device": device, "browser": browser})
        return cycle.id


def record_submission(connection, person_id, selected_fixture_ids, device="Unknown", browser="Other"):
    cycle = ensure_open_cycle(connection)
    if not cycle:
        return None
    selected = sorted({int(value) for value in selected_fixture_ids})
    snapshot = _json_ids(selected)
    current = connection.execute(text("""
        SELECT submission_count FROM poll_participation WHERE poll_cycle_id=:cycle AND person_id=:person
    """), {"cycle": cycle.id, "person": person_id}).mappings().first()
    event_type = "UPDATED" if current and int(current.submission_count or 0) > 0 else "SUBMITTED"
    connection.execute(text("""
        INSERT INTO poll_participation(
            poll_cycle_id,person_id,first_submitted_at,last_submitted_at,submission_count,
            selected_count,selected_fixture_ids,last_poll_revision,device_type,browser,evidence_source
        ) VALUES(:cycle,:person,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,1,:count,:selected,:revision,:device,:browser,'LIVE')
        ON CONFLICT(poll_cycle_id,person_id) DO UPDATE SET
            first_submitted_at=COALESCE(poll_participation.first_submitted_at,CURRENT_TIMESTAMP),
            last_submitted_at=CURRENT_TIMESTAMP,submission_count=poll_participation.submission_count+1,
            selected_count=:count,selected_fixture_ids=:selected,last_poll_revision=:revision,
            device_type=:device,browser=:browser,evidence_source='LIVE'
    """), {"cycle": cycle.id, "person": person_id, "count": len(selected), "selected": snapshot,
             "revision": cycle.revision, "device": device, "browser": browser})
    connection.execute(text("""
        INSERT INTO poll_activity_events(
            poll_cycle_id,person_id,event_type,selected_count,selected_fixture_ids,
            poll_revision,device_type,browser
        ) VALUES(:cycle,:person,:event,:count,:selected,:revision,:device,:browser)
    """), {"cycle": cycle.id, "person": person_id, "event": event_type, "count": len(selected),
             "selected": snapshot, "revision": cycle.revision, "device": device, "browser": browser})
    key = hashlib.sha256(f"cycle:{cycle.id}".encode("ascii")).hexdigest()
    connection.execute(text("""
        INSERT INTO poll_submissions(person_id,poll_key) VALUES(:person,:key)
        ON CONFLICT(person_id,poll_key) DO UPDATE SET updated_at=CURRENT_TIMESTAMP
    """), {"person": person_id, "key": key})
    return event_type


def _sgt(value, pattern="%d-%b-%Y · %I:%M %p SGT"):
    if not value:
        return ""
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(ZoneInfo(SGIA_TIMEZONE)).strftime(pattern).replace(" 0", " ")


def _fixture_labels(connection, fixture_ids):
    if not fixture_ids:
        return []
    placeholders = ",".join(f":fixture_{index}" for index in range(len(fixture_ids)))
    params = {f"fixture_{index}": fixture_id for index, fixture_id in enumerate(fixture_ids)}
    records = connection.execute(text(f"""
        SELECT id,starts_at,home_team,away_team FROM fixtures WHERE id IN ({placeholders}) ORDER BY starts_at,id
    """), params).mappings()
    result = []
    for record in records:
        starts = record.starts_at if isinstance(record.starts_at, datetime) else datetime.fromisoformat(str(record.starts_at))
        result.append({
            "id": record.id,
            "when": starts.strftime("%d-%b · %I:%M %p").replace(" 0", " "),
            "match": f"{record.home_team} vs {record.away_team}",
        })
    return result


def response_audit(engine, cycle_id=None):
    with engine.connect() as connection:
        cycle = connection.execute(text("SELECT * FROM poll_cycles WHERE id=:cycle"), {"cycle": cycle_id}).mappings().first() if cycle_id else current_cycle(connection)
        if not cycle:
            return {"cycle": None, "people": []}
        people = list(connection.execute(text("""
            SELECT p.id,p.name,x.first_viewed_at,x.last_viewed_at,x.first_submitted_at,
                   x.last_submitted_at,x.submission_count,x.selected_count,x.selected_fixture_ids,
                   x.last_poll_revision,x.device_type,x.browser,x.evidence_source
            FROM people p LEFT JOIN poll_participation x
              ON x.person_id=p.id AND x.poll_cycle_id=:cycle
            WHERE p.active=true ORDER BY p.name
        """), {"cycle": cycle.id}).mappings())
        result = []
        for person in people:
            submissions = int(person.submission_count or 0)
            selected_ids = _parse_ids(person.selected_fixture_ids)
            if not submissions and not person.first_viewed_at:
                status = "NOT OPENED"
            elif not submissions:
                status = "VIEWED — NOT SUBMITTED"
            elif int(person.selected_count or 0) == 0:
                status = "RESPONDED — NOT AVAILABLE"
            elif submissions > 1:
                status = "UPDATED"
            else:
                status = "RESPONDED"
            needs_review = submissions > 0 and int(person.last_poll_revision or 0) < int(cycle.revision)
            result.append({
                "person_id": person.id, "Person": person.name, "Status": status,
                "First Viewed SGT": _sgt(person.first_viewed_at),
                "First Submitted SGT": _sgt(person.first_submitted_at),
                "Last Updated SGT": _sgt(person.last_submitted_at),
                "Submission Count": submissions, "Selected Count": int(person.selected_count or 0),
                "Current Selections": _fixture_labels(connection, selected_ids),
                "Needs Review": needs_review, "Device": person.device_type or "Unknown",
                "Browser": person.browser or "Other", "Evidence Source": person.evidence_source or "",
            })
        return {"cycle": dict(cycle), "people": result}


def activity_history(engine, poll_cycle_id, person_id):
    with engine.connect() as connection:
        events = list(connection.execute(text("""
            SELECT event_type,selected_count,selected_fixture_ids,poll_revision,device_type,browser,created_at
            FROM poll_activity_events WHERE poll_cycle_id=:cycle AND person_id=:person
            ORDER BY created_at,id
        """), {"cycle": poll_cycle_id, "person": person_id}).mappings())
        return [{
            "event_type": event.event_type, "selected_count": int(event.selected_count or 0),
            "poll_revision": event.poll_revision, "device": event.device_type, "browser": event.browser,
            "timestamp": _sgt(event.created_at, "%d-%b-%Y · %I:%M %p SGT"),
            "selections": _fixture_labels(connection, _parse_ids(event.selected_fixture_ids)),
        } for event in events]


def response_audit_csv(audit):
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=AUDIT_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for person in audit.get("people", []):
        record = {column: person.get(column, "") for column in AUDIT_COLUMNS}
        record["Current Selections"] = " | ".join(
            f"{item['when']} — {item['match']}" for item in person["Current Selections"]
        )
        record["Needs Review"] = "YES" if person["Needs Review"] else "NO"
        writer.writerow(record)
    return output.getvalue()


def migrate_legacy_evidence(connection):
    """Conservatively import legacy submissions; never infer or fabricate a view."""
    cycle = current_cycle(connection)
    legacy_fixtures = list(connection.execute(text("""
        SELECT id,poll_state FROM fixtures
        WHERE poll_state IN ('OPEN','FROZEN') AND poll_cycle_id IS NULL ORDER BY id
    """)).mappings())
    if legacy_fixtures and not cycle:
        status = "OPEN" if any(row.poll_state == "OPEN" for row in legacy_fixtures) else "FROZEN"
        cycle_id = _insert_cycle(connection, status)
        cycle = connection.execute(text("SELECT * FROM poll_cycles WHERE id=:cycle"), {"cycle": cycle_id}).mappings().first()
    if legacy_fixtures and cycle:
        for fixture in legacy_fixtures:
            connection.execute(text("UPDATE fixtures SET poll_cycle_id=:cycle WHERE id=:fixture"), {
                "cycle": cycle.id, "fixture": fixture.id,
            })
    if not cycle:
        return
    identity_ids = [row.id for row in connection.execute(text("""
        SELECT id FROM fixtures WHERE poll_cycle_id=:cycle AND poll_state='OPEN' ORDER BY id
    """), {"cycle": cycle.id})]
    if not identity_ids:
        identity_ids = [row.id for row in connection.execute(text("""
            SELECT id FROM fixtures WHERE poll_cycle_id=:cycle ORDER BY id
        """), {"cycle": cycle.id})]
    if not identity_ids:
        return
    legacy_key = hashlib.sha256(",".join(map(str, identity_ids)).encode("ascii")).hexdigest()
    submissions = list(connection.execute(text("""
        SELECT person_id,submitted_at,updated_at FROM poll_submissions WHERE poll_key=:key
    """), {"key": legacy_key}).mappings())
    for submission in submissions:
        selected = [row.fixture_id for row in connection.execute(text("""
            SELECT a.fixture_id FROM availability a JOIN fixtures f ON f.id=a.fixture_id
            WHERE a.person_id=:person AND f.poll_cycle_id=:cycle ORDER BY a.fixture_id
        """), {"person": submission.person_id, "cycle": cycle.id})]
        result = connection.execute(text("""
            INSERT INTO poll_participation(
                poll_cycle_id,person_id,first_submitted_at,last_submitted_at,submission_count,
                selected_count,selected_fixture_ids,last_poll_revision,device_type,browser,evidence_source
            ) VALUES(:cycle,:person,:submitted,:updated,1,:count,:selected,:revision,'Unknown','Other','LEGACY_BACKFILL')
            ON CONFLICT(poll_cycle_id,person_id) DO NOTHING
        """), {"cycle": cycle.id, "person": submission.person_id, "submitted": submission.submitted_at,
                 "updated": submission.updated_at, "count": len(selected), "selected": _json_ids(selected),
                 "revision": cycle.revision})
        if result.rowcount:
            connection.execute(text("""
                INSERT INTO poll_activity_events(
                    poll_cycle_id,person_id,event_type,selected_count,selected_fixture_ids,
                    poll_revision,device_type,browser,created_at
                ) VALUES(:cycle,:person,'LEGACY_IMPORTED',:count,:selected,:revision,'Unknown','Other',:created)
            """), {"cycle": cycle.id, "person": submission.person_id, "count": len(selected),
                     "selected": _json_ids(selected), "revision": cycle.revision, "created": submission.updated_at})
