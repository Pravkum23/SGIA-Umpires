import hashlib
import hmac
import json
import os
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from sqlalchemy import text

from src.allocation import allocation_message, propose
from src.board import BOARD_COLUMNS, allocation_board, allocation_board_png, save_allocation_board
from src.db import get_engine, initialize, rows, save_vote
from src.lifecycle import forget_person, person_for_token, published_duties, remember_person, suggest_replacement, withdraw_assignment
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
.identity{display:flex;justify-content:space-between;align-items:center;background:var(--bubble);padding:4px 16px 12px;color:var(--text)}.identity strong{font-size:1rem}.duty{background:#153b33;border:1px solid #356159;border-radius:12px;padding:13px 14px;margin:10px 0;color:var(--text)}.duty-status{color:#9de5d2;font-size:.75rem;text-transform:uppercase;letter-spacing:.04em}
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


def device_bridge(token_to_store=None, forget=False):
    token_json = json.dumps(token_to_store)
    forget_json = "true" if forget else "false"
    components.html(f"""
        <script>
        const key = "sgia_device_token";
        const supplied = {token_json};
        const forget = {forget_json};
        const parentUrl = new URL(window.parent.location.href);
        if (forget) {{
            localStorage.removeItem(key);
            parentUrl.searchParams.delete("device");
            window.parent.location.replace(parentUrl.toString());
        }} else if (supplied) {{
            localStorage.setItem(key, supplied);
        }} else if (!parentUrl.searchParams.get("device")) {{
            const remembered = localStorage.getItem(key);
            if (remembered) {{
                parentUrl.searchParams.set("device", remembered);
                window.parent.location.replace(parentUrl.toString());
            }}
        }}
        </script>
    """, height=0)


def render_public(db):
    raw_token = st.query_params.get("device")
    token = raw_token[0] if isinstance(raw_token, list) and raw_token else raw_token
    remembered = person_for_token(db, token)
    device_bridge()
    brand()
    people = rows(db, "SELECT id,name FROM people WHERE active=true ORDER BY name")
    st.markdown('<div class="poll-head"><div class="poll-question">Provide your availability for this weekend</div><div class="poll-instruction">Select one or more</div></div><div class="poll-name-label">Your Name</div>', unsafe_allow_html=True)
    person = remembered
    if remembered:
        st.markdown(f'<div class="identity"><strong>Hi {remembered["name"]}</strong><span>Your saved profile</span></div>', unsafe_allow_html=True)
        if st.button(f"Not {remembered['name']}? Change person", use_container_width=True):
            forget_person(db, token)
            device_bridge(forget=True)
            st.stop()
    else:
        person_name = st.selectbox("Your Name", [p["name"] for p in people], index=None, placeholder="Select your name", label_visibility="collapsed")
        if person_name:
            person = next(p for p in people if p["name"] == person_name)
            new_token = remember_person(db, person["id"])
            device_bridge(token_to_store=new_token)
    if not person:
        st.markdown('<div class="poll-message">Select your name to view the available slots.</div><div class="view-votes">View votes</div>', unsafe_allow_html=True)
        return
    person_id = person["id"]
    open_slots = rows(db, """
        SELECT f.*,COUNT(a.person_id) votes FROM fixtures f
        LEFT JOIN availability a ON a.fixture_id=f.id
        WHERE f.poll_state='OPEN' GROUP BY f.id ORDER BY f.starts_at
    """)
    frozen_slots = rows(db, """
        SELECT f.*,COUNT(a.person_id) votes FROM fixtures f
        LEFT JOIN availability a ON a.fixture_id=f.id
        WHERE f.poll_state='FROZEN' GROUP BY f.id ORDER BY f.starts_at
    """)
    slots = open_slots or frozen_slots
    poll_state = "OPEN" if open_slots else ("FROZEN" if frozen_slots else None)
    selected = {r["fixture_id"] for r in rows(db, "SELECT fixture_id FROM availability WHERE person_id=:person", {"person": person_id})}
    if not slots:
        st.markdown('<div class="poll-message">No availability poll is open right now.</div><div class="view-votes">View votes</div>', unsafe_allow_html=True)
    else:
        maximum = max([slot["votes"] for slot in slots] + [1])
        choices, voter_details = [], []
        for slot in slots:
            checked = st.checkbox(date_label(slot["starts_at"]), value=slot["id"] in selected, key=f"slot_{person_id}_{slot['id']}", disabled=poll_state != "OPEN")
            if checked:
                choices.append(slot["id"])
            voters = rows(db, "SELECT p.name FROM availability a JOIN people p ON p.id=a.person_id WHERE a.fixture_id=:fixture ORDER BY p.name", {"fixture": slot["id"]})
            initials = " ".join("".join(part[0] for part in v["name"].split()[:2]).upper() for v in voters[:4])
            width = int(slot["votes"] * 100 / maximum)
            st.markdown(f'<div class="poll-row-meta"><span class="secondary">{slot["home_team"]} vs {slot["away_team"]}</span><div class="vote-bar"><div class="vote-fill" style="width:{width}%"></div></div><span class="avatars">{initials}</span><b class="vote-count">{slot["votes"]}</b></div>', unsafe_allow_html=True)
            voter_details.append((slot, voters))
        st.markdown('<div class="view-votes">View votes</div>', unsafe_allow_html=True)
        if poll_state == "FROZEN":
            st.info("Availability is frozen. Your selections are read-only while allocations are prepared.")
        else:
            if st.session_state.pop("availability_saved", False):
                st.markdown('<div class="submitted"><strong>✓ Availability submitted</strong>You can return and update your choices until the poll closes.</div>', unsafe_allow_html=True)
            label = "UPDATE MY AVAILABILITY" if selected & {slot["id"] for slot in slots} else "SAVE MY AVAILABILITY"
            if st.button(label, use_container_width=True, type="primary"):
                save_vote(db, person_id, choices)
                st.session_state.availability_saved = True
                st.rerun()
        with st.expander("View votes"):
            for slot, voters in voter_details:
                st.write(f"**{date_label(slot['starts_at'])}** — " + (", ".join(v["name"] for v in voters) or "No votes yet"))

    duties = published_duties(db, person_id)
    if duties:
        st.subheader("My Duties")
        for duty in duties:
            role_label = {"umpire_1": "Umpire 1", "umpire_2": "Umpire 2", "scorer": "Scorer"}.get(duty["role"], duty["role"])
            st.markdown(f'<div class="duty"><strong>{date_label(duty["starts_at"])}</strong><br>{duty["home_team"]} vs {duty["away_team"]}<br>Role: {role_label}<br><span class="duty-status">{duty["status"].replace("_", " ")}</span></div>', unsafe_allow_html=True)
            if duty["status"] == "ASSIGNED" and st.button("I CAN'T ATTEND", key=f"withdraw_{duty['fixture_id']}_{duty['role']}", use_container_width=True):
                withdraw_assignment(db, duty["fixture_id"], duty["role"], person_id)
                st.rerun()


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
        state_counts = rows(db, "SELECT poll_state,COUNT(*) count FROM fixtures GROUP BY poll_state")
        st.caption(" · ".join(f"{item['poll_state']}: {item['count']}" for item in state_counts))
        freeze_col, publish_col = st.columns(2)
        if freeze_col.button("Freeze Open Poll", use_container_width=True):
            with db.begin() as connection:
                connection.execute(text("UPDATE fixtures SET poll_state='FROZEN',availability_open=false WHERE poll_state='OPEN'"))
            st.success("Open availability is now frozen.")
            st.rerun()
        if publish_col.button("Publish Frozen Allocation", use_container_width=True):
            with db.begin() as connection:
                connection.execute(text("UPDATE fixtures SET poll_state='PUBLISHED',availability_open=false WHERE poll_state='FROZEN'"))
            st.success("Allocation published. Volunteers can now see their confirmed duties.")
            st.rerun()
        opened = []
        for fixture in fixtures:
            locked = fixture["poll_state"] in ("FROZEN", "PUBLISHED")
            if st.checkbox(f"[{fixture['poll_state']}] {pd.Timestamp(fixture['starts_at']).strftime('%a %d %b, %I:%M %p')} — {fixture['home_team']} vs {fixture['away_team']} ({fixture['responses']} available)", value=fixture["poll_state"] == "OPEN", key=f"open_{fixture['id']}", disabled=locked):
                opened.append(fixture["id"])
            voters = rows(db, "SELECT p.name FROM availability a JOIN people p ON p.id=a.person_id WHERE a.fixture_id=:fixture ORDER BY p.name", {"fixture": fixture["id"]})
            if voters:
                st.caption("Available: " + ", ".join(v["name"] for v in voters))
        if st.button("Update open slots"):
            with db.begin() as connection:
                connection.execute(text("UPDATE fixtures SET availability_open=false,poll_state='CLOSED' WHERE poll_state='OPEN'"))
                for fixture_id in opened:
                    connection.execute(text("UPDATE fixtures SET availability_open=true,poll_state='OPEN' WHERE id=:id AND poll_state='CLOSED'"), {"id": fixture_id})
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
        replacements = rows(db, """
            SELECT a.fixture_id,a.role,a.person_id,p.name,f.starts_at,f.home_team,f.away_team
            FROM assignments a JOIN people p ON p.id=a.person_id JOIN fixtures f ON f.id=a.fixture_id
            WHERE a.status='REPLACEMENT_REQUIRED' ORDER BY f.starts_at,a.role
        """)
        if replacements:
            st.error(f"{len(replacements)} assignment(s) require replacement")
            active_people = rows(db, "SELECT id,name FROM people WHERE active=true ORDER BY name")
            for replacement in replacements:
                with st.container(border=True):
                    st.write(f"⚠️ **Replacement Required** — {date_label(replacement['starts_at'])} · {replacement['home_team']} vs {replacement['away_team']} · {replacement['role'].replace('_',' ').title()} ({replacement['name']} withdrew)")
                    suggest_col, manual_col = st.columns(2)
                    if suggest_col.button("Suggest Replacement", key=f"suggest_{replacement['fixture_id']}_{replacement['role']}"):
                        candidate = suggest_replacement(db, replacement["fixture_id"], replacement["role"])
                        if candidate:
                            st.success(f"Suggested {candidate['name']}. Review and confirm below.")
                            st.rerun()
                        else:
                            st.warning("No eligible available replacement found.")
                    options = [person["name"] for person in active_people if person["id"] != replacement["person_id"]]
                    manual = manual_col.selectbox("Manual replacement", options, key=f"manual_{replacement['fixture_id']}_{replacement['role']}")
                    if manual_col.button("Assign Replacement", key=f"assign_replacement_{replacement['fixture_id']}_{replacement['role']}"):
                        person_id = next(person["id"] for person in active_people if person["name"] == manual)
                        with db.begin() as connection:
                            connection.execute(text("UPDATE assignments SET person_id=:person,status='ASSIGNED',confirmed=true,reason='Manual replacement' WHERE fixture_id=:fixture AND role=:role"), {"person": person_id, "fixture": replacement["fixture_id"], "role": replacement["role"]})
                        st.rerun()
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
