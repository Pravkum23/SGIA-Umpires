import csv
from datetime import datetime
from io import BytesIO, StringIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import text

BOARD_COLUMNS = ["Day", "Date", "Time", "TEAM 1", "TEAM 2", "Umpire 1", "Umpire 2", "Scorer"]
ROLE_COLUMNS = {"umpire_1": "Umpire 1", "umpire_2": "Umpire 2", "scorer": "Scorer"}
REVIEW_COLUMNS = ["Date", "Time", "Team 1", "Team 2", "Umpire 1", "Umpire 2", "Scorer", "Reason", "Status", "Actions"]


def _dt(value):
    return value if hasattr(value, "date") else datetime.fromisoformat(value)


def allocation_board(engine, confirmed_only=False):
    assignment_filter = " AND a.confirmed=true AND a.status='ASSIGNED'" if confirmed_only else ""
    exists_filter = " AND x.confirmed=true AND x.status='ASSIGNED'" if confirmed_only else ""
    with engine.connect() as connection:
        data = list(connection.execute(text(f"""
            SELECT f.id fixture_id,f.starts_at,f.home_team,f.away_team,a.role,p.name
            FROM fixtures f
            LEFT JOIN assignments a ON a.fixture_id=f.id{assignment_filter}
            LEFT JOIN people p ON p.id=a.person_id
            WHERE EXISTS (SELECT 1 FROM assignments x WHERE x.fixture_id=f.id{exists_filter})
            ORDER BY f.starts_at,a.role
        """)).mappings())
    board = {}
    for row in data:
        starts = _dt(row.starts_at)
        item = board.setdefault(row.fixture_id, {
            "fixture_id": row.fixture_id,
            "Day": starts.strftime("%A"),
            "Date": starts.strftime("%d-%b-%Y"),
            "Time": starts.strftime("%I:%M %p").lstrip("0"),
            "TEAM 1": row.home_team,
            "TEAM 2": row.away_team,
            "Umpire 1": "",
            "Umpire 2": "",
            "Scorer": "",
        })
        if row.role in ROLE_COLUMNS:
            item[ROLE_COLUMNS[row.role]] = row.name or ""
    return list(board.values())


def allocation_review(engine):
    """Return one editable review row per fixture with all three roles together."""
    with engine.connect() as connection:
        data = list(connection.execute(text("""
            SELECT f.id fixture_id,f.starts_at,f.home_team,f.away_team,a.role,p.name,
                   a.confirmed,a.reason,a.status assignment_status
            FROM fixtures f JOIN assignments a ON a.fixture_id=f.id
            JOIN people p ON p.id=a.person_id
            ORDER BY f.starts_at,a.role
        """)).mappings())
    review = {}
    for row in data:
        starts = _dt(row.starts_at)
        item = review.setdefault(row.fixture_id, {
            "fixture_id": row.fixture_id,
            "Date": starts.strftime("%d-%b-%Y"),
            "Time": starts.strftime("%I:%M %p").lstrip("0"),
            "Team 1": row.home_team,
            "Team 2": row.away_team,
            "Umpire 1": "", "Umpire 2": "", "Scorer": "",
            "Reason": [], "_confirmed": [], "_statuses": [],
        })
        if row.role in ROLE_COLUMNS:
            item[ROLE_COLUMNS[row.role]] = row.name
        if row.reason and row.reason not in item["Reason"]:
            item["Reason"].append(row.reason)
        item["_confirmed"].append(bool(row.confirmed))
        item["_statuses"].append(row.assignment_status)
    result = []
    for item in review.values():
        statuses = item.pop("_statuses")
        confirmed = item.pop("_confirmed")
        item["Reason"] = "; ".join(item["Reason"])
        if "REPLACEMENT_REQUIRED" in statuses:
            item["Status"] = "Replacement Required"
        elif len(confirmed) == 3 and all(confirmed):
            item["Status"] = "Confirmed"
        else:
            item["Status"] = "Proposed"
        item["Actions"] = "Save row / Confirm row"
        result.append(item)
    return result


def confirmed_allocation_board(engine):
    """Return only complete fixtures whose three current assignments are confirmed and active."""
    return [
        record for record in allocation_board(engine, confirmed_only=True)
        if all(record[column] for column in ("Umpire 1", "Umpire 2", "Scorer"))
    ]


def allocation_board_csv(records):
    """Serialize the official board columns in their published display order."""
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=BOARD_COLUMNS, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    writer.writerows(records)
    return output.getvalue()


def save_allocation_review(engine, records, confirm=False):
    """Save reviewed names; optionally confirm every role in the supplied rows."""
    with engine.begin() as connection:
        people = {row.name: row for row in connection.execute(text(
            "SELECT id,name,can_umpire,can_score FROM people WHERE active=true"
        )).mappings()}
        for record in records:
            fixture_id = int(record["fixture_id"])
            names = [str(record.get(column, "") or "").strip() for column in ROLE_COLUMNS.values()]
            if not all(names):
                raise ValueError("Umpire 1, Umpire 2 and Scorer are required")
            if len(set(names)) != 3:
                raise ValueError("Each fixture requires three distinct people")
            for role, column in ROLE_COLUMNS.items():
                name = str(record[column]).strip()
                if name not in people:
                    raise ValueError(f"Unknown or inactive person: {name}")
                person = people[name]
                capable = person.can_score if role == "scorer" else person.can_umpire
                if not capable:
                    raise ValueError(f"{name} is not eligible for {column}")
                existing = connection.execute(text("""
                    SELECT person_id,confirmed FROM assignments
                    WHERE fixture_id=:fixture AND role=:role
                """), {"fixture": fixture_id, "role": role}).mappings().first()
                keep_confirmed = bool(existing and existing.confirmed) or confirm
                connection.execute(text("""
                    INSERT INTO assignments(fixture_id,role,person_id,confirmed,reason,status)
                    VALUES(:fixture,:role,:person,:confirmed,'Admin allocation review','ASSIGNED')
                    ON CONFLICT(fixture_id,role) DO UPDATE SET person_id=:person,
                        confirmed=:confirmed,reason='Admin allocation review',status='ASSIGNED'
                """), {"fixture": fixture_id, "role": role, "person": person.id, "confirmed": keep_confirmed})
                if existing and existing.person_id != person.id:
                    connection.execute(text("""
                        INSERT INTO assignment_events(fixture_id,role,person_id,replacement_person_id,event_type,reason)
                        VALUES(:fixture,:role,:previous,:replacement,'MANUAL_CHANGE','Allocation review edit')
                    """), {"fixture": fixture_id, "role": role, "previous": existing.person_id, "replacement": person.id})
                if confirm and (not existing or not existing.confirmed):
                    connection.execute(text("""
                        INSERT INTO assignment_events(fixture_id,role,person_id,event_type,reason)
                        VALUES(:fixture,:role,:person,'CONFIRMED','Allocation review confirmation')
                    """), {"fixture": fixture_id, "role": role, "person": person.id})


def confirm_all_proposed(engine):
    """Confirm every complete, active proposal and preserve a per-role audit event."""
    review = [row for row in allocation_review(engine) if row["Status"] == "Proposed"]
    if review:
        save_allocation_review(engine, review, confirm=True)
    return len(review)


def save_allocation_board(engine, records):
    """Persist the final board as confirmed assignments selected by the admin."""
    with engine.begin() as connection:
        people = {row.name: row.id for row in connection.execute(text("SELECT id,name FROM people WHERE active=true")).mappings()}
        for record in records:
            fixture_id = int(record["fixture_id"])
            for role, column in ROLE_COLUMNS.items():
                name = str(record.get(column, "") or "").strip()
                existing = connection.execute(text("SELECT person_id,confirmed FROM assignments WHERE fixture_id=:fixture AND role=:role"), {"fixture": fixture_id, "role": role}).mappings().first()
                if not name:
                    if existing:
                        connection.execute(text("INSERT INTO assignment_events(fixture_id,role,person_id,event_type,reason) VALUES(:fixture,:role,:person,'MANUAL_CHANGE','Cleared on allocation board')"), {"fixture": fixture_id, "role": role, "person": existing.person_id})
                    connection.execute(text("DELETE FROM assignments WHERE fixture_id=:fixture AND role=:role"), {"fixture": fixture_id, "role": role})
                    continue
                if name not in people:
                    raise ValueError(f"Unknown or inactive person: {name}")
                connection.execute(text("""
                    INSERT INTO assignments(fixture_id,role,person_id,confirmed,reason)
                    VALUES(:fixture,:role,:person,true,'Admin allocation board')
                    ON CONFLICT(fixture_id,role) DO UPDATE SET
                        person_id=:person,confirmed=true,reason='Admin allocation board',status='ASSIGNED'
                """), {"fixture": fixture_id, "role": role, "person": people[name]})
                new_person = people[name]
                if existing and existing.person_id != new_person:
                    connection.execute(text("""
                        INSERT INTO assignment_events(fixture_id,role,person_id,replacement_person_id,event_type,reason)
                        VALUES(:fixture,:role,:previous,:replacement,'MANUAL_CHANGE','Allocation board edit')
                    """), {"fixture": fixture_id, "role": role, "previous": existing.person_id, "replacement": new_person})
                elif not existing or not existing.confirmed:
                    connection.execute(text("INSERT INTO assignment_events(fixture_id,role,person_id,event_type,reason) VALUES(:fixture,:role,:person,'CONFIRMED','Allocation board confirmation')"), {"fixture": fixture_id, "role": role, "person": new_person})


def _font(size, bold=False):
    candidates = [
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
        "LiberationSans-Bold.ttf" if bold else "LiberationSans-Regular.ttf",
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    # Pillow's scalable built-in fallback preserves the requested size even on
    # minimal Streamlit containers with no system font packages installed.
    return ImageFont.load_default(size=size)


def allocation_board_png(records, logo_path=None):
    """Render a deterministic, WhatsApp-shareable official allocation sheet."""
    widths = [130, 175, 135, 245, 245, 190, 190, 190]
    margin, title_height, header_height, row_height = 34, 116, 72, 86
    width = sum(widths) + margin * 2
    height = title_height + header_height + max(1, len(records)) * row_height + margin
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    navy, yellow, grid = "#173F78", "#F4C542", "#27364A"
    draw.rectangle((0, 0, width, title_height), fill=navy)
    title_font, header_font, cell_font = _font(52, True), _font(25, True), _font(24, True)
    title = "SGIA Official"
    title_box = draw.textbbox((0, 0), title, font=title_font)
    title_y = (title_height - (title_box[3] - title_box[1])) / 2 - title_box[1]
    draw.text(((width - (title_box[2] - title_box[0])) / 2, title_y), title, fill="white", font=title_font)
    if logo_path and Path(logo_path).exists():
        with Image.open(logo_path) as source:
            logo = source.convert("RGBA")
            logo.thumbnail((88, 88), Image.Resampling.LANCZOS)
            image.paste(logo, (margin, (title_height - logo.height) // 2), logo)

    top = title_height
    x = margin
    for column, cell_width in zip(BOARD_COLUMNS, widths):
        draw.rectangle((x, top, x + cell_width, top + header_height), fill=yellow, outline=grid, width=2)
        _center_text(draw, column, (x, top, x + cell_width, top + header_height), header_font, grid)
        x += cell_width
    for index, record in enumerate(records or [{}]):
        y = top + header_height + index * row_height
        x = margin
        for column, cell_width in zip(BOARD_COLUMNS, widths):
            draw.rectangle((x, y, x + cell_width, y + row_height), fill="#FFFFFF" if index % 2 == 0 else "#F5F7FA", outline=grid, width=2)
            _center_text(draw, str(record.get(column, "")), (x + 5, y + 3, x + cell_width - 5, y + row_height - 3), cell_font, "#17212B")
            x += cell_width
    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def _center_text(draw, value, box, font, fill):
    value = value if len(value) <= 28 else value[:26] + "…"
    bounds = draw.textbbox((0, 0), value, font=font)
    text_width, text_height = bounds[2] - bounds[0], bounds[3] - bounds[1]
    x = box[0] + (box[2] - box[0] - text_width) / 2
    y = box[1] + (box[3] - box[1] - text_height) / 2 - bounds[1]
    draw.text((x, y), value, font=font, fill=fill)
