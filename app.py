import hashlib
import hmac
import os
from pathlib import Path

import pandas as pd
import streamlit as st
from sqlalchemy import text

from src.allocation import allocation_message, propose
from src.db import get_engine, initialize, rows, save_vote
from src.routing import is_admin_request

st.set_page_config(page_title="SGIA Umpires", page_icon="🏏", layout="wide")
st.markdown("""<style>
:root{--wa-bg:#0b141a;--wa-panel:#202c33;--wa-green:#00a884;--wa-line:#344047;--wa-text:#e9edef;--wa-muted:#8696a0}
.stApp{background:radial-gradient(circle at 20% 0,#16372d 0,#0b141a 34%);color:var(--wa-text)}
.block-container{max-width:980px;padding-top:1rem}.brand{display:flex;align-items:center;gap:12px;margin:2px 0 18px}
.brand-badge{width:52px;height:52px;border-radius:50%;display:grid;place-items:center;background:#fff;color:#0b6b4f;font-weight:900;border:3px solid #ef9935;box-shadow:0 2px 12px #0008}
.brand-title{font-size:1.45rem;font-weight:800}.brand-sub{font-size:.75rem;color:var(--wa-muted);letter-spacing:.04em}
.poll-head{background:var(--wa-panel);padding:17px 17px 10px;border-radius:13px 13px 0 0;margin-top:4px}
.poll-question{font-size:1.03rem;font-weight:600}.poll-instruction,.secondary{color:var(--wa-muted);font-size:.8rem}
.poll-row-meta{display:flex;align-items:center;gap:8px;margin:-7px 10px 7px 43px;color:var(--wa-muted);font-size:.75rem}
.vote-bar{height:3px;background:#3b4a52;border-radius:4px;flex:1;overflow:hidden}.vote-fill{height:100%;background:var(--wa-green)}
.avatars{letter-spacing:-4px;font-size:.68rem;color:#d7f7ed}.view-votes{background:var(--wa-panel);border-top:1px solid var(--wa-line);border-radius:0 0 13px 13px;text-align:center;color:#53bdeb;padding:11px;margin-bottom:12px;font-size:.9rem}
div[data-testid="stCheckbox"]{background:var(--wa-panel);padding:7px 12px 7px;border-radius:0;margin:0}
div[data-testid="stCheckbox"] label p{color:var(--wa-text);font-weight:500;font-size:.95rem}
div[data-testid="stCheckbox"] [data-testid="stCheckbox"]{border-radius:50%}
div[data-testid="stSelectbox"] label p{color:var(--wa-text)}
.stButton>button{border-radius:24px;background:var(--wa-green);color:white;border:0;font-weight:700;min-height:44px}
div[data-testid="stExpander"]{background:var(--wa-panel);border:0;border-radius:10px}
@media(max-width:640px){.block-container{padding:.55rem .7rem 2rem}.brand{margin-bottom:10px}.brand-badge{width:44px;height:44px}.brand-title{font-size:1.25rem}h1{font-size:1.55rem}}
</style>""", unsafe_allow_html=True)


@st.cache_resource
def engine():
    value = get_engine()
    initialize(value)
    return value


def brand(admin=False):
    # The official site image is used when reachable; the restrained SGIA badge remains as fallback.
    logo = Path("assets/sgia-logo.png")
    if logo.exists():
        left, right = st.columns([1, 8])
        left.image(str(logo), width=58)
        right.markdown(f'<div class="brand-title">SGIA Umpires</div><div class="brand-sub">{"ADMIN CONSOLE" if admin else "SINGAPORE INDIAN ASSOCIATION"}</div>', unsafe_allow_html=True)
    else:
        st.markdown(f'<div class="brand"><div class="brand-badge">SGIA</div><div><div class="brand-title">SGIA Umpires</div><div class="brand-sub">{"ADMIN CONSOLE" if admin else "SINGAPORE INDIAN ASSOCIATION"}</div></div></div>', unsafe_allow_html=True)


def date_label(value):
    dt = pd.Timestamp(value)
    return f"{dt.strftime('%A')} - {dt.strftime('%I:%M %p').lstrip('0')}"


def render_public(db):
    brand()
    people = rows(db, "SELECT id,name FROM people WHERE active=true ORDER BY name")
    person_name = st.selectbox("Your name", [p["name"] for p in people], index=None, placeholder="Select your name")
    if not person_name:
        st.caption("Choose your name to view the current availability poll.")
        return
    person_id = next(p["id"] for p in people if p["name"] == person_name)
    slots = rows(db, """
        SELECT f.*,COUNT(a.person_id) votes FROM fixtures f
        LEFT JOIN availability a ON a.fixture_id=f.id
        WHERE f.availability_open=true GROUP BY f.id ORDER BY f.starts_at
    """)
    selected = {r["fixture_id"] for r in rows(db, "SELECT fixture_id FROM availability WHERE person_id=:person", {"person": person_id})}
    if not slots:
        st.info("No availability poll is open right now.")
        return
    maximum = max([slot["votes"] for slot in slots] + [1])
    st.markdown('<div class="poll-head"><div class="poll-question">Provide your availability for upcoming games</div><div class="poll-instruction">Select one or more</div></div>', unsafe_allow_html=True)
    choices = []
    voter_details = []
    for slot in slots:
        checked = st.checkbox(date_label(slot["starts_at"]), value=slot["id"] in selected, key=f"slot_{person_id}_{slot['id']}")
        if checked:
            choices.append(slot["id"])
        voters = rows(db, "SELECT p.name FROM availability a JOIN people p ON p.id=a.person_id WHERE a.fixture_id=:fixture ORDER BY p.name", {"fixture": slot["id"]})
        initials = " ".join("".join(part[0] for part in v["name"].split()[:2]).upper() for v in voters[:4])
        width = int(slot["votes"] * 100 / maximum)
        st.markdown(f'<div class="poll-row-meta"><span class="secondary">{slot["home_team"]} vs {slot["away_team"]}</span><div class="vote-bar"><div class="vote-fill" style="width:{width}%"></div></div><span class="avatars">{initials}</span><b>{slot["votes"]}</b></div>', unsafe_allow_html=True)
        voter_details.append((slot, voters))
    st.markdown('<div class="view-votes">View votes</div>', unsafe_allow_html=True)
    if st.button("Save availability", use_container_width=True, type="primary"):
        save_vote(db, person_id, choices)
        st.success("Availability saved. You can return later to change it.")
        st.rerun()
    with st.expander("View votes"):
        for slot, voters in voter_details:
            st.write(f"**{date_label(slot['starts_at'])}** — " + (", ".join(v["name"] for v in voters) or "No votes yet"))


def admin_authenticated():
    configured = os.getenv("SGIA_ADMIN_PIN")
    if not configured:
        try:
            configured = st.secrets.get("SGIA_ADMIN_PIN")
        except Exception:
            configured = None
    if "admin_ok" not in st.session_state:
        st.session_state.admin_ok = False
    if st.session_state.admin_ok:
        return True
    if not configured:
        st.warning("Admin is disabled until SGIA_ADMIN_PIN is configured.")
    pin = st.text_input("Admin PIN", type="password", disabled=not configured)
    if st.button("Sign in", disabled=not configured):
        st.session_state.admin_ok = hmac.compare_digest(hashlib.sha256(pin.encode()).digest(), hashlib.sha256(str(configured).encode()).digest())
        if st.session_state.admin_ok:
            st.rerun()
        st.error("Incorrect PIN")
    return False


def render_admin(db):
    brand(admin=True)
    if not admin_authenticated():
        return
    control, allocation, people_tab, output = st.tabs(["Open slots", "Allocations", "People", "Output"])
    with control:
        fixtures = rows(db, "SELECT f.*,COUNT(a.person_id) responses FROM fixtures f LEFT JOIN availability a ON a.fixture_id=f.id GROUP BY f.id ORDER BY f.starts_at")
        opened = []
        for fixture in fixtures:
            if st.checkbox(f"{pd.Timestamp(fixture['starts_at']).strftime('%a %d %b, %I:%M %p')} — {fixture['home_team']} vs {fixture['away_team']} ({fixture['responses']} available)", value=bool(fixture["availability_open"]), key=f"open_{fixture['id']}"):
                opened.append(fixture["id"])
            voters = rows(db, "SELECT p.name FROM availability a JOIN people p ON p.id=a.person_id WHERE a.fixture_id=:fixture ORDER BY p.name", {"fixture": fixture["id"]})
            if voters:
                st.caption("Available: " + ", ".join(v["name"] for v in voters))
        if st.button("Update open slots"):
            with db.begin() as connection:
                connection.execute(text("UPDATE fixtures SET availability_open=false"))
                for fixture_id in opened:
                    connection.execute(text("UPDATE fixtures SET availability_open=true WHERE id=:id"), {"id": fixture_id})
            st.success("Open slots updated")
            st.rerun()
    with allocation:
        left, right = st.columns(2)
        if left.button("Generate proposed allocation", use_container_width=True):
            propose(db)
            st.rerun()
        if right.button("Regenerate unconfirmed only", use_container_width=True):
            propose(db, regenerate_unconfirmed=True)
            st.rerun()
        assignments = rows(db, "SELECT a.fixture_id,a.role,a.person_id,a.confirmed,a.reason,f.starts_at,f.home_team,f.away_team FROM assignments a JOIN fixtures f ON f.id=a.fixture_id ORDER BY f.starts_at,a.role")
        active = rows(db, "SELECT * FROM people WHERE active=true ORDER BY name")
        for assignment in assignments:
            eligible = [p for p in active if p["can_score" if assignment["role"] == "scorer" else "can_umpire"]]
            with st.container(border=True):
                st.write(f"**{pd.Timestamp(assignment['starts_at']).strftime('%a %d %b, %I:%M %p')}** · {assignment['home_team']} vs {assignment['away_team']}")
                names = [p["name"] for p in eligible]
                current = next(p["name"] for p in active if p["id"] == assignment["person_id"])
                choice = st.selectbox(assignment["role"].title(), names, index=names.index(current), key=f"assign_{assignment['fixture_id']}_{assignment['role']}", disabled=bool(assignment["confirmed"]))
                st.caption(("Confirmed · " if assignment["confirmed"] else "Proposed · ") + (assignment["reason"] or "Manual selection"))
                if not assignment["confirmed"] and st.button("Save & confirm", key=f"confirm_{assignment['fixture_id']}_{assignment['role']}"):
                    chosen = next(p["id"] for p in eligible if p["name"] == choice)
                    with db.begin() as connection:
                        connection.execute(text("UPDATE assignments SET person_id=:person,confirmed=true,reason='Admin confirmed' WHERE fixture_id=:fixture AND role=:role"), {"person": chosen, "fixture": assignment["fixture_id"], "role": assignment["role"]})
                    st.rerun()
    with people_tab:
        roster = rows(db, "SELECT * FROM people ORDER BY name")
        edited = st.data_editor(pd.DataFrame(roster), disabled=["id"], hide_index=True, num_rows="dynamic")
        if st.button("Save people"):
            with db.begin() as connection:
                for _, person in edited.iterrows():
                    values = {"name": person["name"], "umpire": bool(person["can_umpire"]), "score": bool(person["can_score"]), "preferred": person["preferred_role"], "active": bool(person["active"])}
                    if pd.isna(person.get("id")):
                        connection.execute(text("INSERT INTO people(name,can_umpire,can_score,preferred_role,active) VALUES(:name,:umpire,:score,:preferred,:active)"), values)
                    else:
                        values["id"] = int(person["id"])
                        connection.execute(text("UPDATE people SET name=:name,can_umpire=:umpire,can_score=:score,preferred_role=:preferred,active=:active WHERE id=:id"), values)
            st.success("People saved")
            st.rerun()
        workload = rows(db, "SELECT p.name,SUM(CASE WHEN a.role='umpire' THEN 1 ELSE 0 END) umpiring,SUM(CASE WHEN a.role='scorer' THEN 1 ELSE 0 END) scoring,COUNT(a.role) total,MAX(f.starts_at) last_duty FROM people p LEFT JOIN assignments a ON a.person_id=p.id LEFT JOIN fixtures f ON f.id=a.fixture_id GROUP BY p.id,p.name ORDER BY total,p.name")
        st.subheader("Workload")
        st.dataframe(workload, use_container_width=True, hide_index=True)
    with output:
        message = allocation_message(db)
        st.text_area("Copy-ready allocation", message, height=360)
        export = rows(db, "SELECT f.starts_at,f.home_team,f.away_team,a.role,p.name,a.confirmed FROM assignments a JOIN fixtures f ON f.id=a.fixture_id JOIN people p ON p.id=a.person_id ORDER BY f.starts_at,a.role")
        st.download_button("Download CSV", pd.DataFrame(export).to_csv(index=False), "sgia_allocations.csv", "text/csv")


db = engine()
if is_admin_request(st.query_params):
    render_admin(db)
else:
    render_public(db)
