from datetime import datetime

from sqlalchemy import text

FIXTURE_COLUMNS = ["Day", "Date", "Time", "TEAM 1", "TEAM 2"]


def _parse_datetime(date_value, time_value):
    value = f"{date_value.strip()} {time_value.strip()}"
    for pattern in ("%d-%b-%Y %I:%M %p", "%d/%m/%Y %I:%M %p", "%Y-%m-%d %H:%M", "%Y-%m-%d %I:%M %p"):
        try:
            return datetime.strptime(value, pattern)
        except ValueError:
            continue
    raise ValueError(f"Unsupported date/time: {value}")


def add_fixture(engine, date_value, time_value, team_1, team_2):
    starts_at = _parse_datetime(date_value, time_value)
    if not team_1.strip() or not team_2.strip():
        raise ValueError("Both teams are required")
    with engine.begin() as connection:
        result = connection.execute(text("""
            INSERT INTO fixtures(starts_at,home_team,away_team,availability_open,poll_state)
            VALUES(:starts,:home,:away,false,'CLOSED') ON CONFLICT(starts_at) DO NOTHING
        """), {"starts": starts_at, "home": team_1.strip(), "away": team_2.strip()})
        return result.rowcount == 1


def edit_fixture(engine, fixture_id, date_value, time_value, team_1, team_2):
    starts_at = _parse_datetime(date_value, time_value)
    if not team_1.strip() or not team_2.strip():
        raise ValueError("Both teams are required")
    with engine.begin() as connection:
        result = connection.execute(text("""
            UPDATE fixtures SET starts_at=:starts,home_team=:home,away_team=:away WHERE id=:fixture
        """), {"starts": starts_at, "home": team_1.strip(), "away": team_2.strip(), "fixture": fixture_id})
        return result.rowcount == 1


def preview_bulk_fixtures(engine, pasted_text):
    with engine.connect() as connection:
        existing = {str(row.starts_at) for row in connection.execute(text("SELECT starts_at FROM fixtures"))}
    preview = []
    for line_number, line in enumerate(pasted_text.splitlines(), 1):
        if not line.strip():
            continue
        parts = [part.strip() for part in line.split("|")]
        if parts and parts[0].lower() == "day":
            continue
        item = {"Line": line_number, "Day": "", "Date": "", "Time": "", "TEAM 1": "", "TEAM 2": "", "Status": "ERROR", "Error": ""}
        try:
            if len(parts) != 5:
                raise ValueError("Expected: Day | Date | Time | TEAM 1 | TEAM 2")
            day, date_value, time_value, team_1, team_2 = parts
            starts_at = _parse_datetime(date_value, time_value)
            item.update({"Day": day or starts_at.strftime("%A"), "Date": starts_at.strftime("%d-%b-%Y"), "Time": starts_at.strftime("%I:%M %p").lstrip("0"), "TEAM 1": team_1, "TEAM 2": team_2, "starts_at": starts_at})
            if not team_1 or not team_2:
                raise ValueError("Both teams are required")
            item["Status"] = "DUPLICATE" if str(starts_at) in existing else "READY"
            existing.add(str(starts_at))
        except ValueError as error:
            item["Error"] = str(error)
        preview.append(item)
    return preview


def import_bulk_fixtures(engine, preview):
    imported = 0
    with engine.begin() as connection:
        for item in preview:
            if item.get("Status") != "READY":
                continue
            result = connection.execute(text("""
                INSERT INTO fixtures(starts_at,home_team,away_team,availability_open,poll_state)
                VALUES(:starts,:home,:away,false,'CLOSED') ON CONFLICT(starts_at) DO NOTHING
            """), {"starts": item["starts_at"], "home": item["TEAM 1"], "away": item["TEAM 2"]})
            imported += max(result.rowcount, 0)
    return imported
