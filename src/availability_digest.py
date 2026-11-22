"""Scheduled SHL5 availability summaries sent through WhatsApp Cloud API."""
import json
import os
import sys
from datetime import datetime, timedelta
from urllib.request import Request, urlopen

from sqlalchemy import text

from .db import get_engine, rows
from .weekly import singapore_now, week_bounds
from .live_poll import coverage_status


def build_digest(engine, period, now=None):
    now = now or singapore_now()
    monday, _ = week_bounds(now.date())
    offsets = {"weekdays": (1, 5), "weekend": (5, 7)}
    first, last = offsets[period]
    start, end = monday + timedelta(days=first), monday + timedelta(days=last)
    scope = {"start": start, "end": end}
    fixtures = rows(engine, """
        SELECT id,starts_at,home_team,away_team FROM fixtures
        WHERE tournament='SHL5' AND starts_at>=:start AND starts_at<:end
        AND match_status='SCHEDULED' ORDER BY starts_at,id
    """, scope)
    people = rows(engine, """
        SELECT a.fixture_id,p.id,p.name,p.can_umpire,p.can_score
        FROM availability a JOIN people p ON p.id=a.person_id
        JOIN fixtures f ON f.id=a.fixture_id
        WHERE f.tournament='SHL5' AND f.starts_at>=:start AND f.starts_at<:end
        AND f.match_status='SCHEDULED' AND p.active=true ORDER BY p.name
    """, scope)
    available_ids = {p["id"] for p in people}
    roster = rows(engine, "SELECT id,name FROM people WHERE active=true ORDER BY name")
    names = lambda records: ", ".join(p["name"] for p in records) or "None"
    lines = [
        f"SHL5 availability {start:%d %b}–{end - timedelta(days=1):%d %b %Y}",
        f"Available for at least one match: {names([p for p in roster if p['id'] in available_ids])}",
        f"No availability selected for these matches: {names([p for p in roster if p['id'] not in available_ids])}",
    ]
    for f in fixtures:
        when = f["starts_at"]
        when = datetime.fromisoformat(when) if isinstance(when, str) else when
        available = [p for p in people if p["fixture_id"] == f["id"]]
        lines.append(f"{when:%a %d %b %I:%M %p}: {f['home_team']} vs {f['away_team']}. "
                     f"Umpires: {names([p for p in available if p['can_umpire']])}. "
                     f"Scorers: {names([p for p in available if p['can_score']])}. "
                     f"Coverage: {coverage_status(available)}.")
    if not fixtures:
        lines.append("No scheduled matches.")
    # Template parameters must be single-line strings.
    summary = " | ".join(" ".join(line.split()) for line in lines)
    return f"{monday.date()}:{period}", summary


def send_whatsapp(summary):
    payload = {
        "messaging_product": "whatsapp",
        "to": os.environ["WHATSAPP_TO"],
        "type": "template",
        "template": {
            "name": os.environ["WHATSAPP_TEMPLATE"],
            "language": {"code": os.getenv("WHATSAPP_LANGUAGE", "en_US")},
            "components": [{"type": "body", "parameters": [{"type": "text", "text": summary}]}],
        },
    }
    request = Request(
        f"https://graph.facebook.com/v27.0/{os.environ['WHATSAPP_PHONE_NUMBER_ID']}/messages",
        data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + os.environ["WHATSAPP_ACCESS_TOKEN"],
                 "Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=30) as response:
        result = json.load(response)
    return result["messages"][0]["id"]


def main():
    required = ("DATABASE_URL", "WHATSAPP_TO", "WHATSAPP_TEMPLATE",
                "WHATSAPP_PHONE_NUMBER_ID", "WHATSAPP_ACCESS_TOKEN")
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        sys.exit("WhatsApp setup incomplete: " + ", ".join(missing))
    period = os.environ["DIGEST_PERIOD"]
    engine = get_engine()
    try:
        key, summary = build_digest(engine, period)
        if len(summary) > 30000:
            raise ValueError("Summary exceeds WhatsApp template length.")
        # Reserve before sending. An interrupted or ambiguous send is reviewed
        # manually, so rerunning a workflow cannot send the same report twice.
        with engine.begin() as c:
            c.execute(text("""CREATE TABLE IF NOT EXISTS availability_digest_delivery (
                digest_key VARCHAR(80) PRIMARY KEY,
                status VARCHAR(16) NOT NULL,
                message_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""))
            claimed = c.execute(text("""
                INSERT INTO availability_digest_delivery(digest_key,status)
                VALUES(:key,'PENDING') ON CONFLICT(digest_key) DO NOTHING
                RETURNING digest_key
            """), {"key": key}).scalar()
        if not claimed:
            print("This report was already sent or has a pending send. No duplicate sent.")
            return
        message_id = send_whatsapp(summary)
        with engine.begin() as c:
            c.execute(text("""UPDATE availability_digest_delivery
                SET status='ACCEPTED',message_id=:message WHERE digest_key=:key"""),
                {"message": message_id, "key": key})
        print("WhatsApp accepted the availability summary.")
    except Exception:
        sys.exit("Summary failed. Check WhatsApp configuration and delivery status before retrying. Private data withheld.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
