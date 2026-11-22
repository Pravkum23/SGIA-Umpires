"""Open SHL5 availability at the agreed Singapore times."""
import os
import sys
from datetime import timedelta
from zoneinfo import ZoneInfo

from .db import get_engine
from .weekly import enable_week, singapore_now, week_bounds


def open_due_availability(engine, now=None):
    now = now or singapore_now()
    if now.tzinfo is not None:
        now = now.astimezone(ZoneInfo("Asia/Singapore")).replace(tzinfo=None)
    monday, _ = week_bounds(now.date())
    opened = 0
    for week in (monday, monday + timedelta(days=7)):
        windows = (
            (week - timedelta(days=3) + timedelta(hours=21),
             week + timedelta(days=1), week + timedelta(days=5)),
            (week + timedelta(hours=18),
             week + timedelta(days=5), week + timedelta(days=7)),
        )
        for due, start, end in windows:
            if due <= now < end:
                opened += enable_week(engine, "SHL5", max(start, now), end, unopened_only=True)
    return opened


def main():
    if not (os.getenv("DATABASE_URL") or os.getenv("SUPABASE_DB_URL")):
        sys.exit("Set the GitHub Actions DATABASE_URL secret to the app's database connection.")
    engine = get_engine()
    try:
        count = open_due_availability(engine)
        print(f"Opened {count} SHL5 matches for availability.")
    except Exception:
        sys.exit("Availability job failed. Check database access and configuration. Connection details are withheld.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
