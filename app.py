import hashlib
import hmac
import json
import os
from pathlib import Path
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from sqlalchemy import text

from src.allocation import allocation_message, propose
from src.allocation_scope import current_allocation_summary, published_allocation_cycles
from src.board import BOARD_COLUMNS, allocation_board, allocation_board_csv, allocation_board_png, allocation_review, confirm_all_proposed, confirmed_allocation_board, save_allocation_board, save_allocation_review
from src.db import get_engine, initialize, rows, save_vote
from src.fixtures import add_fixture, edit_fixture, import_bulk_fixtures, preview_bulk_fixtures
from src.lifecycle import assignment_history, authenticate_person, confirm_assignment, forget_person, person_for_token, pin_status, published_duties, replace_assignment, set_person_pin, suggest_replacement, withdraw_assignment
from src.live_poll import SGIA_TIMEZONE, live_poll_monitor
from src.poll_audit import activity_history, coarse_client, record_poll_view, response_audit, response_audit_csv, set_fixture_poll_state, transition_poll_state, update_open_slots
from src.routing import is_admin_request
from src.workload import fixture_history, season_workload, set_match_status

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
.block-container{max-width:1180px;padding-top:1.2rem}.admin-brand{display:flex;align-items:center;gap:12px;margin-bottom:15px}.admin-brand img{width:58px;height:58px;object-fit:contain}.brand-title{font-size:1.4rem;font-weight:800}.brand-sub{font-size:.72rem;color:#667085;letter-spacing:.08em}.match-badge{display:inline-block;border-radius:14px;padding:4px 9px;font-size:.7rem;font-weight:800;letter-spacing:.04em}.status-scheduled{background:#e9eef5;color:#344054}.status-completed{background:#d1fadf;color:#05603a}.status-cancelled{background:#fee4e2;color:#b42318}.live-title{font-size:1.15rem;font-weight:850;letter-spacing:.04em;margin-top:4px}.live-sub{font-size:.72rem;color:#667085}.live-alert{background:#fff7ed;border:1px solid #fed7aa;border-radius:12px;padding:11px 12px;margin:8px 0 12px}.live-ok{background:#ecfdf3;border-color:#abefc6;color:#067647}.coverage-covered{color:#067647;font-weight:800}.coverage-warning{color:#b54708;font-weight:800}.match-live{border-left:4px solid #98a2b3;padding-left:10px;margin:7px 0}.match-live strong{font-size:1rem}.match-people{font-size:.78rem;color:#475467;margin-top:5px}.st-key-mobile_allocation_review{display:none}
@media(max-width:700px){.block-container{padding:.7rem .65rem 2rem!important}.stButton>button,.stDownloadButton>button{width:100%!important;min-height:46px!important}.quick-card{border:1px solid #d0d5dd;border-radius:13px;padding:12px;margin:7px 0;background:#f8fafc}.quick-number{font-size:1.55rem;font-weight:800}.quick-label{font-size:.72rem;color:#667085;text-transform:uppercase}.stTabs [data-baseweb="tab-list"]{overflow-x:auto}.stTabs [data-baseweb="tab"]{min-width:max-content}.st-key-desktop_allocation_review{display:none!important}.st-key-mobile_allocation_review{display:block!important}}
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


def allocation_cycle_caption(summary):
    start = pd.Timestamp(summary["starts_at"])
    end = pd.Timestamp(summary["ends_at"])
    date_range = start.strftime("%d %b") if start.date() == end.date() else f"{start.strftime('%d %b')} \u2013 {end.strftime('%d %b')}"
    return f"Current allocation: {date_range} | {summary['match_count']} matches"


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
        device_bridge(token_to_store=token)
        st.markdown(f'<div class="identity"><strong>Hi {remembered["name"]}</strong><span>Your saved profile</span></div>', unsafe_allow_html=True)
        if st.button(f"Not {remembered['name']}? Change person", use_container_width=True):
            forget_person(db, token)
            device_bridge(forget=True)
            st.stop()
    else:
        person_name = st.selectbox("Your Name", [p["name"] for p in people], index=None, placeholder="Select your name", label_visibility="collapsed")
        if person_name:
            selected_person = next(p for p in people if p["name"] == person_name)
            pin = st.text_input("Your 4-digit PIN", type="password", max_chars=4, placeholder="Enter PIN")
            if not pin_status(db, selected_person["id"]):
                st.warning("Your PIN has not been set. Please contact the SGIA administrator.")
            elif st.button("CONTINUE", use_container_width=True, type="primary"):
                new_token, result = authenticate_person(db, selected_person["id"], pin)
                if new_token:
                    st.query_params["device"] = new_token
                    st.rerun()
                elif result == "LOCKED":
                    st.error("Too many failed attempts. Please try again in 15 minutes.")
                else:
                    st.error("Incorrect PIN.")
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
    try:
        user_agent = st.context.headers.get("User-Agent", "")
    except Exception:
        user_agent = ""
    device_type, browser_name = coarse_client(user_agent)
    if poll_state:
        record_poll_view(db, person_id, poll_state, device_type, browser_name)
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
                save_vote(db, person_id, choices, device_type, browser_name)
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
    quick_tab, control, fixtures_tab, allocation, board_tab, people_tab, workload_tab, history_tab, output = st.tabs(["Quick Admin", "Open Poll", "Fixtures", "Allocations", "Allocation Board", "People", "Season Workload", "History", "Output"])
    with quick_tab:
        st.subheader("Quick Admin")
        live = live_poll_monitor(db)
        refresh_col, refreshed_col = st.columns([1, 1])
        if refresh_col.button("REFRESH LIVE POLL", use_container_width=True):
            st.rerun()
        refreshed_col.caption("Last refreshed: " + datetime.now(ZoneInfo(SGIA_TIMEZONE)).strftime("%I:%M %p SGT").lstrip("0"))
        st.markdown('<div class="live-title">LIVE POLL</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="live-sub"><strong>{live["responded"]} / {live["active_volunteers"]} Responded</strong> · {len(live["yet_to_respond"])} Yet to Respond · {len(live["needs_review"])} Needs Review · {live["open_matches"]} Open Matches</div>', unsafe_allow_html=True)
        live_cards = st.columns(2)
        live_metrics = (
            (live["active_volunteers"], "Active Volunteers"),
            (live["responded"], "Responded"),
            (len(live["yet_to_respond"]), "Yet to Respond"),
            (len(live["needs_review"]), "Needs Review"),
            (live["open_matches"], "Open Matches"),
        )
        for index, (value, label) in enumerate(live_metrics):
            live_cards[index % 2].markdown(f'<div class="quick-card"><div class="quick-number">{value}</div><div class="quick-label">{label}</div></div>', unsafe_allow_html=True)
        st.markdown("#### Yet to Respond")
        if live["yet_to_respond"]:
            st.markdown('<div class="live-alert">' + " · ".join(escape(name) for name in live["yet_to_respond"]) + '</div>', unsafe_allow_html=True)
        else:
            st.markdown('<div class="live-alert live-ok">✓ Everyone has responded</div>', unsafe_allow_html=True)
        for title, names in (
            ("Viewed — Not Submitted", live["viewed_not_submitted"]),
            ("Responded — No Availability", live["responded_zero"]),
            ("Needs Review", live["needs_review"]),
        ):
            if names:
                st.markdown(f"#### {title}")
                st.markdown('<div class="live-alert">' + " · ".join(escape(name) for name in names) + '</div>', unsafe_allow_html=True)
        st.markdown('<div class="live-title">RESPONSE AUDIT</div>', unsafe_allow_html=True)
        audit_cycles = rows(db, "SELECT id,status,revision,created_at FROM poll_cycles ORDER BY id DESC")
        if not audit_cycles:
            st.info("Open a poll to begin collecting response evidence.")
        else:
            cycle_labels = [f"Cycle {cycle['id']} · {cycle['status']} · Revision {cycle['revision']}" for cycle in audit_cycles]
            default_cycle = next((index for index, cycle in enumerate(audit_cycles) if cycle["id"] == live.get("poll_cycle_id")), 0)
            selected_cycle_label = st.selectbox("Poll cycle", cycle_labels, index=default_cycle, key="response_audit_cycle")
            selected_cycle = audit_cycles[cycle_labels.index(selected_cycle_label)]
            audit = response_audit(db, selected_cycle["id"])
            audit_names = [person["Person"] for person in audit["people"]]
            audit_name = st.selectbox("Volunteer", audit_names, key="response_audit_person")
            audited = next(person for person in audit["people"] if person["Person"] == audit_name)
            warning = " · ⚠ NEEDS REVIEW — Poll changed after last submission" if audited["Needs Review"] else ""
            st.markdown(f"### {escape(audited['Person'])}")
            st.markdown(f"**{escape(audited['Status'])}**{warning}")
            details = [
                ("First viewed", audited["First Viewed SGT"] or "No recorded view"),
                ("First submitted", audited["First Submitted SGT"] or "Not submitted"),
                ("Last updated", audited["Last Updated SGT"] or "Not submitted"),
                ("Submissions", str(audited["Submission Count"])),
                ("Current selection", f"{audited['Selected Count']} matches"),
                ("Device", audited["Device"]), ("Browser", audited["Browser"]),
            ]
            for label, value in details:
                st.markdown(f"**{label}:** {escape(value)}")
            if audited["Evidence Source"] == "LEGACY_BACKFILL":
                st.info("Legacy response imported — exact original view history is unavailable.")
            st.markdown("#### Current Selections")
            if audited["Current Selections"]:
                for selection in audited["Current Selections"]:
                    st.markdown(f"**{escape(selection['when'])}**  \n{escape(selection['match'])}")
            elif audited["Submission Count"]:
                st.caption("Zero fixtures were saved in the latest response.")
            else:
                st.caption("No submission has been recorded.")
            st.markdown("#### Activity History")
            history = activity_history(db, audit["cycle"]["id"], audited["person_id"])
            if not history:
                st.caption("No activity evidence recorded.")
            for event in history:
                wording = {"VIEWED": "Poll successfully viewed", "SUBMITTED": "Submission recorded", "UPDATED": "Update recorded", "LEGACY_IMPORTED": "Legacy response imported"}.get(event["event_type"], event["event_type"].title())
                if event["event_type"] in ("SUBMITTED", "UPDATED", "LEGACY_IMPORTED"):
                    with st.expander(f"{event['timestamp']} — {wording} · {event['selected_count']} fixtures saved"):
                        if event["selections"]:
                            for selection in event["selections"]:
                                st.write(f"{selection['when']} — {selection['match']}")
                        else:
                            st.write("Zero fixtures were saved.")
                else:
                    st.write(f"{event['timestamp']} — {wording}")
            st.download_button("DOWNLOAD RESPONSE AUDIT CSV", response_audit_csv(audit), "sgia-response-audit.csv", "text/csv", use_container_width=True)
        if not live["days"]:
            st.info("No availability poll is currently open.")
        for day in live["days"]:
            with st.expander(f'{day["label"].upper()} · {len(day["matches"])} Matches · {day["unique_available"]} Unique Available', expanded=True):
                for match in day["matches"]:
                    covered = match["coverage"] == "COVERED"
                    coverage_class = "coverage-covered" if covered else "coverage-warning"
                    coverage_icon = "✓" if covered else "⚠"
                    names = " · ".join(escape(person["name"]) for person in match["available"]) or "No volunteers available"
                    st.markdown(
                        f'<div class="match-live"><strong>{match["time"]}</strong><br>{escape(match["home_team"])} vs {escape(match["away_team"])}<br>'
                        f'<b>{match["available_count"]} AVAILABLE</b><br><span class="{coverage_class}">{coverage_icon} {match["coverage"]}</span><br>'
                        f'<span class="live-sub">{match["umpire_count"]} umpire capable · {match["scorer_count"]} scorer capable</span>'
                        f'<div class="match-people">{names}</div></div>',
                        unsafe_allow_html=True,
                    )
        upcoming = rows(db, "SELECT COUNT(*) count FROM fixtures WHERE starts_at>=CURRENT_TIMESTAMP")[0]["count"]
        open_count = rows(db, "SELECT COUNT(*) count FROM fixtures WHERE poll_state='OPEN'")[0]["count"]
        responses = rows(db, "SELECT COUNT(DISTINCT person_id) count FROM availability a JOIN fixtures f ON f.id=a.fixture_id WHERE f.poll_state IN ('OPEN','FROZEN')")[0]["count"]
        required = rows(db, "SELECT COUNT(*) count FROM assignments WHERE status='REPLACEMENT_REQUIRED'")[0]["count"]
        cards = st.columns(2)
        for index, (value, label) in enumerate(((upcoming,"Upcoming Matches"),(open_count,"Open Poll"),(responses,"With Availability"),(required,"Replacement Required"))):
            cards[index % 2].markdown(f'<div class="quick-card"><div class="quick-number">{value}</div><div class="quick-label">{label}</div></div>', unsafe_allow_html=True)
        st.markdown("#### Season workload")
        for person in season_workload(db)[:5]:
            st.markdown(
                f'<div class="quick-card"><strong>{person["name"]}</strong><br>'
                f'<span class="quick-label">Completed {person["games_completed"]} · Umpire {person["umpire_completed"]} · '
                f'Scorer {person["scorer_completed"]} · Upcoming {person["upcoming_duties"]} · Total {person["total_assigned"]}</span></div>',
                unsafe_allow_html=True,
            )
        st.markdown("#### Quick actions")
        qa1, qa2 = st.columns(2)
        if qa1.button("FREEZE POLL", key="quick_freeze"):
            transition_poll_state(db, "OPEN", "FROZEN")
            st.rerun()
        if qa2.button("GENERATE ALLOCATION", key="quick_generate"):
            propose(db, regenerate_unconfirmed=True); st.rerun()
        st.markdown("#### + Add match")
        q_date = st.text_input("Date", placeholder="22-Aug-2026", key="quick_date")
        q_time = st.text_input("Time", placeholder="7:00 PM", key="quick_time")
        q_team1 = st.text_input("TEAM 1", key="quick_team1")
        q_team2 = st.text_input("TEAM 2", key="quick_team2")
        q_add, q_open = st.columns(2)
        def quick_add(open_poll=False):
            if add_fixture(db,q_date,q_time,q_team1,q_team2):
                if open_poll:
                    fixture_id = rows(db, "SELECT id FROM fixtures WHERE home_team=:home AND away_team=:away ORDER BY id DESC LIMIT 1", {"home":q_team1.strip(),"away":q_team2.strip()})[0]["id"]
                    set_fixture_poll_state(db, fixture_id, "OPEN")
                st.success("Match added" + (" and poll opened." if open_poll else ".")); st.rerun()
            else: st.warning("Duplicate fixture was not added.")
        if q_add.button("ADD MATCH", key="quick_add"):
            try: quick_add(False)
            except ValueError as error: st.error(str(error))
        if q_open.button("ADD & OPEN POLL", key="quick_add_open"):
            try: quick_add(True)
            except ValueError as error: st.error(str(error))
        st.markdown("#### Matches")
        mobile_fixtures = rows(db,"SELECT f.*,COUNT(a.person_id) responses FROM fixtures f LEFT JOIN availability a ON a.fixture_id=f.id GROUP BY f.id ORDER BY f.starts_at")
        for fixture in mobile_fixtures:
            with st.expander(f"{pd.Timestamp(fixture['starts_at']).strftime('%a %d %b · %I:%M %p')} — {fixture['home_team']} vs {fixture['away_team']}"):
                st.markdown(f'<span class="match-badge status-{fixture["match_status"].lower()}">{fixture["match_status"]}</span> · {fixture["poll_state"]} · {fixture["responses"]} available', unsafe_allow_html=True)
                action_cols=st.columns(4)
                for idx,(label,state) in enumerate((("Open","OPEN"),("Close","CLOSED"),("Freeze","FROZEN"),("Publish","PUBLISHED"))):
                    if action_cols[idx].button(label,key=f"mobile_state_{fixture['id']}_{state}"):
                        set_fixture_poll_state(db, fixture["id"], state)
                        st.rerun()
                match_actions = st.columns(3)
                for idx, (label, status) in enumerate((("MARK COMPLETED", "COMPLETED"), ("MARK CANCELLED", "CANCELLED"), ("RESTORE TO SCHEDULED", "SCHEDULED"))):
                    if match_actions[idx].button(label, key=f"match_status_{fixture['id']}_{status}"):
                        try:
                            set_match_status(db, fixture["id"], status)
                            st.success(f"Match is now {status}.")
                            st.rerun()
                        except ValueError as error:
                            st.warning(str(error))
        st.markdown("#### Final allocation")
        quick_board = allocation_board(db)
        active_names = [person["name"] for person in rows(db,"SELECT name FROM people WHERE active=true ORDER BY name")]
        for match in quick_board:
            with st.container(border=True):
                st.write(f"**{match['Day']} · {match['Time']}**")
                st.caption(f"{match['TEAM 1']} vs {match['TEAM 2']}")
                edited_match = dict(match)
                for role in ("Umpire 1","Umpire 2","Scorer"):
                    current = match[role] if match[role] in active_names else active_names[0]
                    edited_match[role] = st.selectbox(role,active_names,index=active_names.index(current),key=f"quick_{match['fixture_id']}_{role}")
                if st.button("SAVE",key=f"quick_save_{match['fixture_id']}"):
                    save_allocation_board(db,[edited_match]);st.success("Assignment saved.");st.rerun()
        if quick_board:
            quick_png=allocation_board_png(quick_board,"assets/sgia-logo.png")
            st.image(quick_png,use_container_width=True)
            st.download_button("DOWNLOAD PNG",quick_png,"sgia-official-allocation.png","image/png",key="quick_png")
            st.download_button("DOWNLOAD CSV",pd.DataFrame(quick_board)[BOARD_COLUMNS].to_csv(index=False),"sgia-allocation.csv","text/csv",key="quick_csv")
            st.text_area("COPY WHATSAPP MESSAGE",allocation_message(db),height=220,key="quick_whatsapp")
    with control:
        fixtures = rows(db, "SELECT f.*,COUNT(a.person_id) responses FROM fixtures f LEFT JOIN availability a ON a.fixture_id=f.id GROUP BY f.id ORDER BY f.starts_at")
        state_counts = rows(db, "SELECT poll_state,COUNT(*) count FROM fixtures GROUP BY poll_state")
        st.caption(" · ".join(f"{item['poll_state']}: {item['count']}" for item in state_counts))
        freeze_col, publish_col = st.columns(2)
        if freeze_col.button("Freeze Open Poll", use_container_width=True):
            transition_poll_state(db, "OPEN", "FROZEN")
            st.success("Open availability is now frozen.")
            st.rerun()
        if publish_col.button("Publish Frozen Allocation", use_container_width=True):
            transition_poll_state(db, "FROZEN", "PUBLISHED")
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
            update_open_slots(db, opened)
            st.success("Open slots updated")
            st.rerun()
    with fixtures_tab:
        st.subheader("Fixture Manager")
        add_section, edit_section, bulk_section = st.tabs(["Add Fixture", "Edit Fixture", "Bulk Import"])
        with add_section:
            add_date = st.text_input("Date", placeholder="22-Aug-2026", key="fixture_add_date")
            add_time = st.text_input("Time", placeholder="11:00 AM", key="fixture_add_time")
            add_team_1 = st.text_input("TEAM 1", key="fixture_add_team_1")
            add_team_2 = st.text_input("TEAM 2", key="fixture_add_team_2")
            if st.button("Add Fixture"):
                try:
                    if add_fixture(db, add_date, add_time, add_team_1, add_team_2):
                        st.success("Fixture added.")
                    else:
                        st.warning("Duplicate fixture was not added.")
                except ValueError as error:
                    st.error(str(error))
        with edit_section:
            fixture_options = rows(db, "SELECT id,starts_at,home_team,away_team FROM fixtures ORDER BY starts_at")
            fixture_labels = {f"{pd.Timestamp(item['starts_at']).strftime('%d-%b-%Y %I:%M %p')} · {item['home_team']} vs {item['away_team']}": item for item in fixture_options}
            selected_label = st.selectbox("Fixture", list(fixture_labels), key="fixture_edit_select")
            if selected_label:
                selected_fixture = fixture_labels[selected_label]
                starts = pd.Timestamp(selected_fixture["starts_at"])
                edit_date = st.text_input("Date", starts.strftime("%d-%b-%Y"), key="fixture_edit_date")
                edit_time = st.text_input("Time", starts.strftime("%I:%M %p").lstrip("0"), key="fixture_edit_time")
                edit_team_1 = st.text_input("TEAM 1", selected_fixture["home_team"], key="fixture_edit_team_1")
                edit_team_2 = st.text_input("TEAM 2", selected_fixture["away_team"], key="fixture_edit_team_2")
                if st.button("Save Fixture Changes"):
                    try:
                        edit_fixture(db, selected_fixture["id"], edit_date, edit_time, edit_team_1, edit_team_2)
                        st.success("Fixture updated.")
                        st.rerun()
                    except Exception as error:
                        st.error(f"Fixture could not be updated: {error}")
        with bulk_section:
            bulk_text = st.text_area("Paste fixtures", placeholder="Day | Date | Time | TEAM 1 | TEAM 2\nSaturday | 05-Sep-2026 | 11:00 AM | Team A | Team B", height=180)
            if st.button("Preview Bulk Import"):
                st.session_state.fixture_preview = preview_bulk_fixtures(db, bulk_text)
            fixture_preview = st.session_state.get("fixture_preview", [])
            if fixture_preview:
                st.dataframe(pd.DataFrame([{key: value for key, value in item.items() if key != "starts_at"} for item in fixture_preview]), hide_index=True, use_container_width=True)
                if st.button("Import Ready Fixtures"):
                    imported = import_bulk_fixtures(db, fixture_preview)
                    st.success(f"Imported {imported} fixture(s). Duplicates and invalid rows were skipped.")
                    st.session_state.fixture_preview = []
                    st.rerun()
    with allocation:
        st.subheader("Allocation Review")
        allocation_summary = current_allocation_summary(db)
        if allocation_summary:
            st.caption(allocation_cycle_caption(allocation_summary))
        else:
            st.info("No current allocation is being prepared.")
        left, right, confirm_all_col = st.columns(3)
        if left.button("Generate proposed allocation", use_container_width=True):
            propose(db)
            st.rerun()
        if right.button("Regenerate unconfirmed only", use_container_width=True):
            propose(db, regenerate_unconfirmed=True)
            st.rerun()
        if confirm_all_col.button("Confirm all proposed", use_container_width=True):
            try:
                count = confirm_all_proposed(db)
                st.success(f"Confirmed {count} fixture(s).")
                st.rerun()
            except ValueError as error:
                st.error(str(error))
        review_rows = allocation_review(db)
        active_people = rows(db, "SELECT id,name,can_umpire,can_score FROM people WHERE active=true ORDER BY name")
        umpire_names = [person["name"] for person in active_people if person["can_umpire"]]
        scorer_names = [person["name"] for person in active_people if person["can_score"]]
        if not review_rows:
            st.info("Generate proposed allocations to begin review.")
        else:
            desktop_frame = pd.DataFrame(review_rows)
            with st.container(key="desktop_allocation_review"):
                st.markdown("#### Fixture allocation table")
                edited_review = st.data_editor(
                    desktop_frame,
                    hide_index=True,
                    use_container_width=True,
                    disabled=["fixture_id", "Date", "Time", "Team 1", "Team 2", "Reason", "Status", "Actions"],
                    column_config={
                        "fixture_id": None,
                        "Umpire 1": st.column_config.SelectboxColumn("Umpire 1", options=umpire_names, required=True),
                        "Umpire 2": st.column_config.SelectboxColumn("Umpire 2", options=umpire_names, required=True),
                        "Scorer": st.column_config.SelectboxColumn("Scorer", options=scorer_names, required=True),
                    },
                    key="allocation_review_table",
                )
                edited_records = edited_review.to_dict("records")
                action_labels = [f"{record['Date']} · {record['Time']} · {record['Team 1']} vs {record['Team 2']}" for record in edited_records]
                selected_label = st.selectbox("Fixture for row action", action_labels)
                selected_record = edited_records[action_labels.index(selected_label)]
                save_row, confirm_row, save_all, confirm_visible = st.columns(4)
                if save_row.button("Save row", key="desktop_save_review_row", use_container_width=True):
                    try:
                        save_allocation_review(db, [selected_record], confirm=False)
                        st.success("Selected row saved.")
                        st.rerun()
                    except ValueError as error:
                        st.error(str(error))
                if confirm_row.button("Confirm row", key="desktop_confirm_review_row", use_container_width=True):
                    try:
                        save_allocation_review(db, [selected_record], confirm=True)
                        st.success("Selected row confirmed.")
                        st.rerun()
                    except ValueError as error:
                        st.error(str(error))
                if save_all.button("Save all visible", use_container_width=True):
                    try:
                        save_allocation_review(db, edited_records, confirm=False)
                        st.success("Visible allocation rows saved.")
                        st.rerun()
                    except ValueError as error:
                        st.error(str(error))
                if confirm_visible.button("Confirm all visible", use_container_width=True):
                    try:
                        save_allocation_review(db, edited_records, confirm=True)
                        st.success("Visible allocation rows confirmed.")
                        st.rerun()
                    except ValueError as error:
                        st.error(str(error))
            with st.container(key="mobile_allocation_review"):
                for review in review_rows:
                    with st.container(border=True):
                        st.write(f"**{review['Date']} · {review['Time']}**")
                        st.write(f"{review['Team 1']} vs {review['Team 2']}")
                        mobile = dict(review)
                        for column, options in (("Umpire 1", umpire_names), ("Umpire 2", umpire_names), ("Scorer", scorer_names)):
                            current = review[column] if review[column] in options else options[0]
                            mobile[column] = st.selectbox(column, options, index=options.index(current), key=f"mobile_review_{review['fixture_id']}_{column}")
                        st.caption(f"{review['Status']} · {review['Reason'] or 'Manual selection'}")
                        save_row, confirm_row = st.columns(2)
                        if save_row.button("Save row", key=f"save_review_{review['fixture_id']}", use_container_width=True):
                            try:
                                save_allocation_review(db, [mobile], confirm=False)
                                st.success("Row saved.")
                                st.rerun()
                            except ValueError as error:
                                st.error(str(error))
                        if confirm_row.button("Confirm row", key=f"confirm_review_{review['fixture_id']}", use_container_width=True):
                            try:
                                save_allocation_review(db, [mobile], confirm=True)
                                st.success("Row confirmed.")
                                st.rerun()
                            except ValueError as error:
                                st.error(str(error))
    with board_tab:
        st.subheader("Current Allocation Board")
        allocation_summary = current_allocation_summary(db)
        if allocation_summary:
            st.caption(allocation_cycle_caption(allocation_summary))
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
                        replace_assignment(db, replacement["fixture_id"], replacement["role"], person_id)
                        st.rerun()
        board_rows = allocation_board(db)
        if not board_rows:
            if allocation_summary:
                st.info("Generate proposed allocations first to populate the board.")
            else:
                st.info("No current allocation is being prepared.")
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
        published_cycles = published_allocation_cycles(db)
        if published_cycles:
            with st.expander("Published Allocations"):
                labels = {
                    f"{pd.Timestamp(cycle['starts_at']).strftime('%d %b %Y')} \u2013 {pd.Timestamp(cycle['ends_at']).strftime('%d %b %Y')} | {cycle['match_count']} matches": cycle
                    for cycle in published_cycles
                }
                published_label = st.selectbox("Published cycle", list(labels), key="published_allocation_cycle")
                published_board = allocation_board(db, confirmed_only=True, cycle_id=labels[published_label]["id"])
                if published_board:
                    st.dataframe(pd.DataFrame(published_board)[BOARD_COLUMNS], hide_index=True, use_container_width=True)
                else:
                    st.info("This published cycle has no confirmed allocation rows.")
    with people_tab:
        roster = rows(db, "SELECT id,name,can_umpire,can_score,preferred_role,active,CASE WHEN pin_hash IS NULL THEN 'NOT SET' ELSE 'SET' END pin_status FROM people ORDER BY name")
        edited = st.data_editor(pd.DataFrame(roster), disabled=["id", "pin_status"], hide_index=True, num_rows="dynamic")
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
        st.subheader("Personal PIN")
        pin_people = rows(db, "SELECT id,name,CASE WHEN pin_hash IS NULL THEN 'NOT SET' ELSE 'SET' END pin_status FROM people WHERE active=true ORDER BY name")
        pin_names = [f"{person['name']} · {person['pin_status']}" for person in pin_people]
        pin_selection = st.selectbox("Volunteer", pin_names, key="pin_person")
        new_pin = st.text_input("New 4-digit PIN", type="password", max_chars=4, key="new_person_pin")
        revoke = st.checkbox("Revoke remembered devices", value=True, key="revoke_person_devices")
        if st.button("Set / Reset PIN"):
            person = pin_people[pin_names.index(pin_selection)]
            try:
                set_person_pin(db, person["id"], new_pin, revoke_tokens=revoke)
                st.success("PIN securely updated. The plaintext PIN was not stored.")
                st.rerun()
            except ValueError as error:
                st.error(str(error))
    with workload_tab:
        st.subheader("Season Workload")
        st.caption("Completed work counts only confirmed, active duties on matches marked COMPLETED. Workload is used only as an allocation tie-breaker.")
        workload_search = st.text_input("Search person", key="workload_search").strip().lower()
        workload = season_workload(db)
        if workload_search:
            workload = [person for person in workload if workload_search in person["name"].lower()]
        workload_frame = pd.DataFrame(workload).rename(columns={
            "name": "Person", "games_completed": "Completed", "umpire_completed": "Umpire",
            "scorer_completed": "Scorer", "upcoming_duties": "Upcoming", "total_assigned": "Total Assigned",
        })
        if not workload_frame.empty:
            desktop_columns = ["Person", "Completed", "Umpire", "Scorer", "Upcoming", "Total Assigned"]
            st.dataframe(workload_frame[desktop_columns], hide_index=True, use_container_width=True)
            st.download_button("Download Workload CSV", workload_frame[desktop_columns].to_csv(index=False), "sgia-season-workload.csv", "text/csv")
            st.markdown("#### Mobile summary")
            for person in workload:
                st.markdown(
                    f'<div class="quick-card"><strong>{person["name"]}</strong><br>Completed {person["games_completed"]}<br>'
                    f'<span class="quick-label">Umpire {person["umpire_completed"]} | Scorer {person["scorer_completed"]}<br>'
                    f'Upcoming {person["upcoming_duties"]} · Total {person["total_assigned"]}</span></div>',
                    unsafe_allow_html=True,
                )
        else:
            st.info("No active people match this search.")
    with history_tab:
        st.subheader("Assignment History")
        history = assignment_history(db)
        if not history:
            st.info("No assignment events recorded yet.")
        else:
            people_filter = ["All"] + sorted({item["person"] for item in history} | {item["replacement"] for item in history if item["replacement"]})
            action_filter = ["All"] + sorted({item["action"] for item in history})
            filter_person = st.selectbox("Person", people_filter, key="history_person")
            filter_action = st.selectbox("Action", action_filter, key="history_action")
            filter_date = st.date_input("Date", value=None, key="history_date")
            display = []
            for item in history:
                starts = pd.Timestamp(item["starts_at"])
                if filter_person != "All" and filter_person not in (item["person"], item["replacement"]):
                    continue
                if filter_action != "All" and item["action"] != filter_action:
                    continue
                if filter_date and starts.date() != filter_date:
                    continue
                display.append({"Date": starts.strftime("%d-%b-%Y"), "Time": starts.strftime("%I:%M %p").lstrip("0"), "Match": f"{item['home_team']} vs {item['away_team']}", "Role": item["role"].replace("_", " ").title(), "Person": item["person"], "Action": item["action"], "Replacement": item["replacement"] or "", "Reason": item["reason"] or "", "Timestamp": item["created_at"]})
            st.dataframe(pd.DataFrame(display), hide_index=True, use_container_width=True)
        st.subheader("Match Status History")
        match_history = fixture_history(db)
        if match_history:
            st.dataframe(pd.DataFrame([{
                "Date": pd.Timestamp(item["starts_at"]).strftime("%d-%b-%Y"),
                "Time": pd.Timestamp(item["starts_at"]).strftime("%I:%M %p").lstrip("0"),
                "Match": f'{item["home_team"]} vs {item["away_team"]}',
                "Action": item["action"], "Previous": item["previous_status"], "New": item["new_status"],
                "Timestamp": item["created_at"],
            } for item in match_history]), hide_index=True, use_container_width=True)
        else:
            st.info("No match status events recorded yet.")
    with output:
        st.subheader("Final Confirmed Allocation")
        allocation_summary = current_allocation_summary(db)
        if allocation_summary:
            st.caption(allocation_cycle_caption(allocation_summary) + ". Exports include only complete confirmed assignments in this batch.")
        final_board = confirmed_allocation_board(db, cycle_id=allocation_summary["id"]) if allocation_summary else []
        if not allocation_summary:
            st.info("No current allocation is being prepared.")
        elif not final_board:
            st.info("No complete confirmed fixture allocations are ready for export.")
        else:
            if st.button("Regenerate preview", use_container_width=True):
                st.rerun()
            final_png = allocation_board_png(final_board, "assets/sgia-logo.png")
            st.markdown("#### Preview image")
            st.image(final_png, use_container_width=True)
            png_col, csv_col = st.columns(2)
            png_col.download_button("Download PNG", final_png, "sgia-official-allocation.png", "image/png", use_container_width=True)
            csv_col.download_button("Download CSV", allocation_board_csv(final_board), "sgia-official-allocation.csv", "text/csv", use_container_width=True)
        st.markdown("#### WhatsApp text")
        message = allocation_message(db, cycle_id=allocation_summary["id"]) if allocation_summary else "\U0001f3cf *SGIA Umpires \u2013 Final Allocation*\n"
        st.text_area("Copy WhatsApp Message", message, height=360)


db = engine()
if ADMIN_MODE:
    render_admin(db)
else:
    render_public(db)
