import os
from pathlib import Path
from sqlalchemy import create_engine, text
from .seed import FIXTURES, PEOPLE

SCHEMA = """
CREATE TABLE IF NOT EXISTS people (id INTEGER PRIMARY KEY, name VARCHAR(80) UNIQUE NOT NULL, can_umpire BOOLEAN NOT NULL, can_score BOOLEAN NOT NULL, preferred_role VARCHAR(10) NOT NULL, active BOOLEAN NOT NULL DEFAULT TRUE);
CREATE TABLE IF NOT EXISTS fixtures (id INTEGER PRIMARY KEY, starts_at TIMESTAMP UNIQUE NOT NULL, home_team VARCHAR(100) NOT NULL, away_team VARCHAR(100) NOT NULL, availability_open BOOLEAN NOT NULL DEFAULT FALSE);
CREATE TABLE IF NOT EXISTS availability (person_id INTEGER NOT NULL REFERENCES people(id), fixture_id INTEGER NOT NULL REFERENCES fixtures(id), created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(person_id, fixture_id));
CREATE TABLE IF NOT EXISTS assignments (fixture_id INTEGER NOT NULL REFERENCES fixtures(id), role VARCHAR(10) NOT NULL, person_id INTEGER NOT NULL REFERENCES people(id), confirmed BOOLEAN NOT NULL DEFAULT FALSE, reason TEXT, PRIMARY KEY(fixture_id, role));
"""

def database_url():
    url = os.getenv("DATABASE_URL") or os.getenv("SUPABASE_DB_URL")
    if url and url.startswith("postgres://"):
        url = "postgresql+psycopg2://" + url[len("postgres://"):]
    return url or f"sqlite:///{Path(os.getenv('SGIA_SQLITE_PATH', 'sgia_umpires.db')).resolve().as_posix()}"

def get_engine(url=None):
    return create_engine(url or database_url(), pool_pre_ping=True)

def initialize(engine):
    with engine.begin() as c:
        for statement in SCHEMA.split(";"):
            if statement.strip(): c.execute(text(statement))
        for name, ump, score, pref in PEOPLE:
            c.execute(text("INSERT INTO people(name,can_umpire,can_score,preferred_role,active) VALUES(:n,:u,:s,:p,true) ON CONFLICT(name) DO NOTHING"), {"n":name,"u":ump,"s":score,"p":pref})
        for dt, home, away in FIXTURES:
            c.execute(text("INSERT INTO fixtures(starts_at,home_team,away_team,availability_open) VALUES(:d,:h,:a,false) ON CONFLICT(starts_at) DO NOTHING"), {"d":dt,"h":home,"a":away})

def rows(engine, sql, params=None):
    with engine.connect() as c: return [dict(r) for r in c.execute(text(sql), params or {}).mappings()]

def save_vote(engine, person_id, fixture_ids):
    with engine.begin() as c:
        c.execute(text("DELETE FROM availability WHERE person_id=:p"), {"p":person_id})
        for fixture_id in fixture_ids:
            open_slot = c.execute(text("SELECT 1 FROM fixtures WHERE id=:f AND availability_open=true"), {"f":fixture_id}).first()
            if open_slot: c.execute(text("INSERT INTO availability(person_id,fixture_id) VALUES(:p,:f)"), {"p":person_id,"f":fixture_id})

