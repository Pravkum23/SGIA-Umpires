from sqlalchemy import text


def create_tournament(engine, name):
    name = str(name or "").strip()
    if not name or len(name) > 80:
        raise ValueError("Enter a tournament name of 1–80 characters.")
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO tournaments(name) VALUES(:name) ON CONFLICT(name) DO NOTHING"), {"name": name})
    return name
