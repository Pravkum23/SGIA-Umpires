from datetime import datetime
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import text

BOARD_COLUMNS = ["Day", "Date", "Time", "TEAM 1", "TEAM 2", "Umpire 1", "Umpire 2", "Scorer"]
ROLE_COLUMNS = {"umpire_1": "Umpire 1", "umpire_2": "Umpire 2", "scorer": "Scorer"}


def _dt(value):
    return value if hasattr(value, "date") else datetime.fromisoformat(value)


def allocation_board(engine):
    with engine.connect() as connection:
        data = list(connection.execute(text("""
            SELECT f.id fixture_id,f.starts_at,f.home_team,f.away_team,a.role,p.name
            FROM fixtures f
            LEFT JOIN assignments a ON a.fixture_id=f.id
            LEFT JOIN people p ON p.id=a.person_id
            WHERE EXISTS (SELECT 1 FROM assignments x WHERE x.fixture_id=f.id)
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


def save_allocation_board(engine, records):
    """Persist the final board as confirmed assignments selected by the admin."""
    with engine.begin() as connection:
        people = {row.name: row.id for row in connection.execute(text("SELECT id,name FROM people WHERE active=true")).mappings()}
        for record in records:
            fixture_id = int(record["fixture_id"])
            for role, column in ROLE_COLUMNS.items():
                name = str(record.get(column, "") or "").strip()
                if not name:
                    connection.execute(text("DELETE FROM assignments WHERE fixture_id=:fixture AND role=:role"), {"fixture": fixture_id, "role": role})
                    continue
                if name not in people:
                    raise ValueError(f"Unknown or inactive person: {name}")
                connection.execute(text("""
                    INSERT INTO assignments(fixture_id,role,person_id,confirmed,reason)
                    VALUES(:fixture,:role,:person,true,'Admin allocation board')
                    ON CONFLICT(fixture_id,role) DO UPDATE SET
                        person_id=:person,confirmed=true,reason='Admin allocation board'
                """), {"fixture": fixture_id, "role": role, "person": people[name]})


def _font(size, bold=False):
    candidates = [
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def allocation_board_png(records, logo_path=None):
    """Render a deterministic, WhatsApp-shareable official allocation sheet."""
    widths = [130, 175, 135, 245, 245, 190, 190, 190]
    margin, title_height, header_height, row_height = 34, 142, 58, 66
    width = sum(widths) + margin * 2
    height = title_height + header_height + max(1, len(records)) * row_height + margin
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    navy, yellow, grid = "#173F78", "#F4C542", "#27364A"
    draw.rectangle((0, 0, width, title_height), fill=navy)
    title_font, header_font, cell_font = _font(36, True), _font(18, True), _font(17)
    title = "SGIA Official"
    title_box = draw.textbbox((0, 0), title, font=title_font)
    draw.text(((width - (title_box[2] - title_box[0])) / 2, 46), title, fill="white", font=title_font)
    if logo_path and Path(logo_path).exists():
        with Image.open(logo_path) as source:
            logo = source.convert("RGBA")
            logo.thumbnail((92, 92), Image.Resampling.LANCZOS)
            image.paste(logo, (margin, 24), logo)

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
