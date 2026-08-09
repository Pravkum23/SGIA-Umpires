import hashlib
import hmac
import os
from pathlib import Path

import pandas as pd
import streamlit as st
from sqlalchemy import text

from src.allocation import allocation_message, propose
from src.board import BOARD_COLUMNS, allocation_board, allocation_board_png, save_allocation_board
from src.db import get_engine, initialize, rows, save_vote
from src.routing import is_admin_request

st.set_page_config(page_title="SGIA Umpires", page_icon="🏏", layout="wide")
ADMIN_MODE = is_admin_request(st.query_params)

PUBLIC_CSS = """<style>
:root{--chat:#0b141a;--bubble:#17473b;--bubble-dark:#143c33;--green:#00a884;--line:#356159;--text:#f0f2f5;--muted:#b4c4c0}
[data-testid="stHeader"],#MainMenu,footer,[data-testid="stToolbar"],[data-testid="stSidebar"]{display:none!important}
html,body,[data-testid="stAppViewContainer"],.stApp{background:radial-gradient(circle at 50% -10%,#173a32 0,#0b141a 38%,#081014 100%)!important;color:var(--text)}
.block-container{max-width:500px!important;width:100%!important;padding:18px 15px 36px!important;margin:0 auto!important}
[data-testid="stVerticalBlock"]{gap:0!important}.stElementContainer:has([data-testid="stImage"]){width:100%!important}[data-testid="stFullScreenFrame"]:has([data-testid="stImage"]){display:flex!important;justify-content:center!important}[data-testid="stImage"]{display:flex!important;justify-content:center!important;margin:0 auto 5px}[data-testid="stImage"] img{width:90px!important;height:90px!important;object-fit:contain;border-radius:50%;background:white;box-shadow:0 5px 18px #0007}
.brand-copy{text-align:center;margin-bottom:15px}.brand-title{font-size:1.45rem;font-weight:800;line-height:1.2;color:var(--text)}.brand-sub{font-size:.72rem;color:var(--muted);letter-spacing:.1em;margin-top:3px}
.poll-head{background:var(--bubble);padding:17px 17px 11px;border-radius:15px 15px 0 0;box-shadow:0 7px 22px #0004}
.poll-question{font-size:1.02rem;font-weight:650;line-height:1.35}.poll-instruction,.secondary{color:var(--muted);font-size:.78rem}.poll-name-label{background:var(--bubble);padding:7px 17px 8px;color:var(--text);font-size:.78rem;font-weight:650;line-height:1.3}
div[data-testid="stSelectbox"]{background:var(--bubble);padding:0 14px 12px;margin:0}div[data-testid="stSelectbox"]>div>div{background:#0f3029;border-color:#4c716a;border-radius:9px;color:var(--text)}
.poll-message{background:var(--bubble);color:var(--muted);padding:18px 17px 22px;text-align:center;font-size:.88rem;border-top:1px solid var(--line)}
div[data-testid="stCheckbox"]{background:var(--bubble);padding:8px 14px 6px;border-radius:0;margin:0;border-top:1px solid rgba(77,113,105,.48)}
div[data-testid="stCheckbox"] label p{color:var(--text);font-weight:600;font-size:.93rem}div[data-testid="stCheckbox"] label>div:first-child{border-radius:50%!important}
.poll-row-meta{display:flex;align-items:center;gap:7px;background:var(--bubble);padding:0 14px 8px 44px;color:var(--muted);font-size:.7rem;min-width:0}
.poll-row-meta .secondary{max-width:44%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.vote-bar{height:4px;background:#40645d;border-radius:5px;flex:1;overflow:hidden}.vote-fill{height:100%;background:#21c99b}
.avatars{font-size:.65rem;color:#d8fff4;white-space:nowrap}.vote-count{min-width:16px;text-align:right;color:var(--text)}
.view-votes{background:var(--bubble-dark);border-top:1px solid var(--line);border-radius:0 0 15px 15px;text-align:center;color:#74d7c0;padding:11px;margin-bottom:13px;font-size:.86rem;box-shadow:0 7px 22px #0004}
.stButton>button{border-radius:24px;background:var(--green);color:white;border:0;font-weight:800;letter-spacing:.035em;min-height:47px;box-shadow:0 5px 15px #0005}.stButton>button:hover{background:#06bd94;color:white}
.submitted{background:#103c32;border:1px solid #278f77;border-radius:12px;padding:13px 15px;margin:4px 0 12px;color:#eafff9;font-size:.88rem}.submitted strong{display:block;color:#63e6be;font-size:.96rem;margin-bottom:2px}
div[data-testid="stExpander"]{background:#122b26;border:1px solid #31534c;border-radius:10px;color:var(--text)}
@media(max-width:430px){.block-container{padding:12px 10px 28px!important}[data-testid="stImage"] img{width:78px!important;height:78px!important}.brand-title{font-size:1.28rem}.poll-head{padding:15px 14px 10px}.poll-name-label{padding-left:14px}.poll-row-meta{padding-left:41px;padding-right:11px}}
</style>"""

ADMIN_CSS = """<style>
.block-container{max-width:1180px;padding-top:1.2rem}.admin-brand{display:flex;align-items:center;gap:12px;margin-bottom:15px}.admin-brand img{width:58px;height:58px;object-fit:contain}.brand-title{font-size:1.4rem;font-weight:800}.brand-sub{font-size:.72rem;color:#667085;letter-spacing:.08em}
</style>"""

st.markdown(ADMIN_CSS if ADMIN_MODE else PUBLIC_CSS, unsafe_allow_html=True)


@st.cache_resource
def engine():
    value = get_engine()
    initialize(value)
    return value


def brand(admin=False):
    logo = Path("assets/sgia-logo.png")
    if not logo.exists():
        raise FileNotFoundError("Required SGIA logo is missing: assets/sgia-logo.png")
    if admin:
        left, right = st.columns([1, 12])
        left.image(str(logo), width=58)
        right.markdown('<div class="brand-title">SGIA Umpires</div><div class="brand-sub">ADMIN CONSOLE</div>', unsafe_allow_html=True)
        return
    st.image(str(logo), width=90)
    st.markdown('<div class="brand-copy"><div class="brand-title">SGIA Umpires</div><div class="brand-sub">SINGAPORE INDIAN ASSOCIATION</div></div>', unsafe_allow_html=True)


def date_label(value):
    dt = pd.Timestamp(value)
    return f"{dt.strftime('%A')} - {dt.strftime('%I:%M %p').lstrip('0')}"


def render_public(db):
    brand()
    people = rows(db, "SELECT id,name FROM people WHERE active=true ORDER BY name")
    st.markdown('<div class="poll-head"><div class="poll-question">Provide your availability for this weekend</div><div class="poll-instruction">Select one or more</div></div><div class="poll-name-label">Your Name</div>', unsafe_allow_html=True)
    person_name = st.selectbox("Your Name", [p["name"] for p in people], index=None, placeholder="Select your name", label_visibility="collapsed")
    if not person_name:
        st.markdown('<div class="poll-message">Select your name to view the available slots.</div><div class="view-votes">View votes</div>', unsafe_allow_html=True)
        return
    person_id = next(p["id"] for p in people if p["name"] == person_name)
    slots = rows(db, """
        SELECT f.*,COUNT(a.person_id) votes FROM fixtures f
        LEFT JOIN availability a ON a.fixture_id=f.id
        WHERE f.availability_open=true GROUP BY f.id ORDER BY f.starts_at
    """)
    selected = {r["fixture_id"] for r in rows(db, "SELECT fixture_id FROM availability WHERE person_id=:person", {"person": person_id})}
    if not slots:
        st.markdown('<div class="poll-message">No availability poll is open right now.</div><div class="view-votes">View votes</div>', unsafe_allow_html=True)
        return
    maximum = max([slot["votes"] for slot in slots] + [1])
    choices = []
    voter_details = []
    for slot in slots:
        checked = st.checkbox(date_label(slot["starts_at"]), value=slot["id"] in selected, key=f"slot_{person_id}_{slot['id']}")
        if checked:
            choices.append(slot["id"])
        voters = rows(db, "SELECT p.name FROM availability a JOIN people p ON p.id=a.person_id WHERE a.fixture_id=:fixture ORDER BY p.name", {"fixture": slot["id"]})
        initials = " ".join("".join(part[0] for part in v["name"].split()[:2]).upper() for v in voters[:4])
        width = int(slot["votes"] * 100 / maximum)
        st.markdown(f'<div class="poll-row-meta"><span class="secondary">{slot["home_team"]} vs {slot["away_team"]}</span><div class="vote-bar"><div class="vote-fill" style="width:{width}%"></div></div><span class="avatars">{initials}</span><b class="vote-count">{slot["votes"]}</b></div>', unsafe_allow_html=True)
        voter_details.append((slot, voters))
    st.markdown('<div class="view-votes">View votes</div>', unsafe_allow_html=True)
    if st.session_state.pop("availability_saved", False):
        st.markdown('<div class="submitted"><strong>✓ Availability submitted</strong>You can return and update your choices until the poll closes.</div>', unsafe_allow_html=True)
    if st.button("SAVE MY AVAILABILITY", use_container_width=True, type="primary"):
        save_vote(db, person_id, choices)
        st.session_state.availability_saved = True
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
    control, allocation, board_tab, people_tab, output = st.tabs(["Open slots", "Allocations", "Allocation Board", "People", "Output"])
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
                role_label = {"umpire_1": "Umpire 1", "umpire_2": "Umpire 2", "scorer": "Scorer"}.get(assignment["role"], assignment["role"].title())
                choice = st.selectbox(role_label, names, index=names.index(current), key=f"assign_{assignment['fixture_id']}_{assignment['role']}", disabled=bool(assignment["confirmed"]))
                st.caption(("Confirmed · " if assignment["confirmed"] else "Proposed · ") + (assignment["reason"] or "Manual selection"))
                if not assignment["confirmed"] and st.button("Save & confirm", key=f"confirm_{assignment['fixture_id']}_{assignment['role']}"):
                    chosen = next(p["id"] for p in eligible if p["name"] == choice)
                    with db.begin() as connection:
                        connection.execute(text("UPDATE assignments SET person_id=:person,confirmed=true,reason='Admin confirmed' WHERE fixture_id=:fixture AND role=:role"), {"person": chosen, "fixture": assignment["fixture_id"], "role": assignment["role"]})
                    st.rerun()
    with board_tab:
        st.subheader("Final Allocation Board")
        board_rows = allocation_board(db)
        if not board_rows:
            st.info("Generate proposed allocations first to populate the board.")
        else:
            active_names = [person["name"] for person in rows(db, "SELECT name FROM people WHERE active=true ORDER BY name")]
            board_frame = pd.DataFrame(board_rows)
            edited_board = st.data_editor(
                board_frame,
                hide_index=True,
                use_container_width=True,
                disabled=["fixture_id", "Day", "Date", "Time", "TEAM 1", "TEAM 2"],
                column_config={
                    "fixture_id": None,
                    "Umpire 1": st.column_config.SelectboxColumn("Umpire 1", options=active_names, required=True),
                    "Umpire 2": st.column_config.SelectboxColumn("Umpire 2", options=active_names, required=True),
                    "Scorer": st.column_config.SelectboxColumn("Scorer", options=active_names, required=True),
                },
                key="allocation_board_editor",
            )
            if st.button("Save Final Allocation Board", type="primary"):
                save_allocation_board(db, edited_board.to_dict("records"))
                st.success("Final allocation board saved and confirmed.")
                st.rerun()
            preview_records = edited_board.to_dict("records")
            png = allocation_board_png(preview_records, "assets/sgia-logo.png")
            st.subheader("Image Preview")
            st.image(png, use_container_width=True)
            st.download_button("Download PNG", png, "sgia-official-allocation.png", "image/png")
            st.download_button("Download Board CSV", edited_board[BOARD_COLUMNS].to_csv(index=False), "sgia-allocation-board.csv", "text/csv")
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
        workload = rows(db, "SELECT p.name,SUM(CASE WHEN a.role IN ('umpire_1','umpire_2') THEN 1 ELSE 0 END) umpiring,SUM(CASE WHEN a.role='scorer' THEN 1 ELSE 0 END) scoring,COUNT(a.role) total,MAX(f.starts_at) last_duty FROM people p LEFT JOIN assignments a ON a.person_id=p.id LEFT JOIN fixtures f ON f.id=a.fixture_id GROUP BY p.id,p.name ORDER BY total,p.name")
        st.subheader("Workload")
        st.dataframe(workload, use_container_width=True, hide_index=True)
    with output:
        message = allocation_message(db)
        st.text_area("Copy-ready allocation", message, height=360)
        export = rows(db, "SELECT f.starts_at,f.home_team,f.away_team,a.role,p.name,a.confirmed FROM assignments a JOIN fixtures f ON f.id=a.fixture_id JOIN people p ON p.id=a.person_id ORDER BY f.starts_at,a.role")
        st.download_button("Download CSV", pd.DataFrame(export).to_csv(index=False), "sgia_allocations.csv", "text/csv")


db = engine()
if ADMIN_MODE:
    render_admin(db)
else:
    render_public(db)
