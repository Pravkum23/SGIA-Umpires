import hashlib
import hmac
import os
import pandas as pd
import streamlit as st
from sqlalchemy import text
from src.db import get_engine, initialize, rows, save_vote
from src.allocation import propose, allocation_message

st.set_page_config(page_title="SGIA Umpires", page_icon="🏏", layout="wide")
st.markdown("""<style>
.stApp{background:linear-gradient(145deg,#071a15,#102b24);color:#f4f7f5}.block-container{max-width:980px;padding-top:1.2rem}
.poll-card{background:#d9fdd3;color:#13231c;border-radius:16px;padding:18px;box-shadow:0 8px 28px #0005;margin-bottom:16px}
.slot{border-bottom:1px solid #b8d8b3;padding:10px 0}.muted{color:#587067;font-size:.85rem}
div[data-testid="stForm"]{background:#d9fdd3;border:0;border-radius:16px;padding:12px;color:#13231c}
.stButton>button,.stFormSubmitButton>button{border-radius:22px;background:#25a866;color:white;border:0;font-weight:700}
@media(max-width:640px){.block-container{padding:.75rem}.poll-card{border-radius:12px;padding:14px}h1{font-size:1.75rem}}
</style>""", unsafe_allow_html=True)

@st.cache_resource
def engine():
    e=get_engine(); initialize(e); return e
db=engine()

def dt_label(value):
    dt=pd.Timestamp(value); return f"{dt.strftime('%A')} · {dt.strftime('%I:%M %p').lstrip('0')}"

st.title("🏏 SGIA Umpires")
public, admin = st.tabs(["Availability", "Admin"])
with public:
    st.markdown('<div class="poll-card"><b>Provide your availability for upcoming games</b><br><span class="muted">Select one or more · you can return and change your choices</span></div>', unsafe_allow_html=True)
    people=rows(db,"SELECT id,name FROM people WHERE active=true ORDER BY name")
    person_name=st.selectbox("Your name", [p["name"] for p in people], index=None, placeholder="Select your name")
    if person_name:
        pid=next(p["id"] for p in people if p["name"]==person_name)
        slots=rows(db,"SELECT f.*,COUNT(a.person_id) votes FROM fixtures f LEFT JOIN availability a ON a.fixture_id=f.id WHERE f.availability_open=true GROUP BY f.id ORDER BY f.starts_at")
        selected={r["fixture_id"] for r in rows(db,"SELECT fixture_id FROM availability WHERE person_id=:p",{"p":pid})}
        if not slots: st.info("No availability polls are open right now.")
        else:
            with st.form("availability"):
                choices=[]
                for s in slots:
                    checked=st.checkbox(dt_label(s["starts_at"]), value=s["id"] in selected, key=f"slot_{pid}_{s['id']}", help=f"{s['home_team']} vs {s['away_team']} · {s['votes']} available")
                    st.caption(f"{s['home_team']} vs {s['away_team']}  ·  {s['votes']} available")
                    if checked: choices.append(s["id"])
                if st.form_submit_button("Save availability", use_container_width=True):
                    save_vote(db,pid,choices); st.success("Availability saved. Thank you!"); st.rerun()
with admin:
    configured=os.getenv("SGIA_ADMIN_PIN")
    if not configured:
        try: configured=st.secrets.get("SGIA_ADMIN_PIN")
        except Exception: configured=None
    if "admin_ok" not in st.session_state: st.session_state.admin_ok=False
    if not st.session_state.admin_ok:
        if not configured: st.warning("Admin is disabled until SGIA_ADMIN_PIN is configured.")
        pin=st.text_input("Admin PIN",type="password",disabled=not configured)
        if st.button("Sign in",disabled=not configured):
            st.session_state.admin_ok=hmac.compare_digest(hashlib.sha256(pin.encode()).digest(),hashlib.sha256(str(configured).encode()).digest())
            if st.session_state.admin_ok: st.rerun()
            else: st.error("Incorrect PIN")
    else:
        control, allocation, people_tab, output = st.tabs(["Open slots","Allocations","People","Output"])
        with control:
            fixtures=rows(db,"SELECT f.*,COUNT(a.person_id) responses FROM fixtures f LEFT JOIN availability a ON a.fixture_id=f.id GROUP BY f.id ORDER BY f.starts_at")
            with st.form("open_slots"):
                opened=[]
                for f in fixtures:
                    if st.checkbox(f"{pd.Timestamp(f['starts_at']).strftime('%a %d %b, %I:%M %p')} — {f['home_team']} vs {f['away_team']} ({f['responses']} available)",value=bool(f["availability_open"]),key=f"open_{f['id']}"): opened.append(f["id"])
                    voters=rows(db,"SELECT p.name FROM availability a JOIN people p ON p.id=a.person_id WHERE a.fixture_id=:f ORDER BY p.name",{"f":f["id"]})
                    if voters: st.caption("Available: "+", ".join(v["name"] for v in voters))
                if st.form_submit_button("Update open slots"):
                    with db.begin() as c:
                        c.execute(text("UPDATE fixtures SET availability_open=false"))
                        for fid in opened:c.execute(text("UPDATE fixtures SET availability_open=true WHERE id=:id"),{"id":fid})
                    st.success("Open slots updated");st.rerun()
        with allocation:
            c1,c2=st.columns(2)
            if c1.button("Generate proposed allocation",use_container_width=True): propose(db);st.rerun()
            if c2.button("Regenerate unconfirmed only",use_container_width=True): propose(db,True);st.rerun()
            assignments=rows(db,"SELECT a.fixture_id,a.role,a.person_id,a.confirmed,a.reason,f.starts_at,f.home_team,f.away_team FROM assignments a JOIN fixtures f ON f.id=a.fixture_id ORDER BY f.starts_at,a.role")
            active=rows(db,"SELECT * FROM people WHERE active=true ORDER BY name")
            for a in assignments:
                eligible=[p for p in active if p["can_score" if a["role"]=="scorer" else "can_umpire"]]
                with st.container(border=True):
                    st.write(f"**{pd.Timestamp(a['starts_at']).strftime('%a %d %b, %I:%M %p')}** · {a['home_team']} vs {a['away_team']}")
                    names=[p["name"] for p in eligible]; current=next(p["name"] for p in active if p["id"]==a["person_id"])
                    choice=st.selectbox(a["role"].title(),names,index=names.index(current),key=f"assign_{a['fixture_id']}_{a['role']}")
                    st.caption(a["reason"] or "Manual selection")
                    if st.button("Save & confirm",key=f"confirm_{a['fixture_id']}_{a['role']}"):
                        chosen=next(p["id"] for p in eligible if p["name"]==choice)
                        with db.begin() as c:c.execute(text("UPDATE assignments SET person_id=:p,confirmed=true,reason='Admin confirmed' WHERE fixture_id=:f AND role=:r"),{"p":chosen,"f":a["fixture_id"],"r":a["role"]})
                        st.rerun()
        with people_tab:
            roster=rows(db,"SELECT * FROM people ORDER BY name")
            edited=st.data_editor(pd.DataFrame(roster),disabled=["id"],hide_index=True,num_rows="dynamic")
            if st.button("Save people"):
                with db.begin() as c:
                    for _,p in edited.iterrows():
                        if pd.isna(p.get("id")): c.execute(text("INSERT INTO people(name,can_umpire,can_score,preferred_role,active) VALUES(:n,:u,:s,:r,:a)"),{"n":p["name"],"u":bool(p["can_umpire"]),"s":bool(p["can_score"]),"r":p["preferred_role"],"a":bool(p["active"])})
                        else:c.execute(text("UPDATE people SET name=:n,can_umpire=:u,can_score=:s,preferred_role=:r,active=:a WHERE id=:id"),{"id":int(p["id"]),"n":p["name"],"u":bool(p["can_umpire"]),"s":bool(p["can_score"]),"r":p["preferred_role"],"a":bool(p["active"])})
                st.success("People saved");st.rerun()
            workload=rows(db,"SELECT p.name,SUM(CASE WHEN a.role='umpire' THEN 1 ELSE 0 END) umpiring,SUM(CASE WHEN a.role='scorer' THEN 1 ELSE 0 END) scoring,COUNT(a.role) total,MAX(f.starts_at) last_duty FROM people p LEFT JOIN assignments a ON a.person_id=p.id LEFT JOIN fixtures f ON f.id=a.fixture_id GROUP BY p.id,p.name ORDER BY total,p.name")
            st.subheader("Workload");st.dataframe(workload,use_container_width=True,hide_index=True)
        with output:
            message=allocation_message(db);st.text_area("Copy-ready allocation",message,height=360)
            export=rows(db,"SELECT f.starts_at,f.home_team,f.away_team,a.role,p.name,a.confirmed FROM assignments a JOIN fixtures f ON f.id=a.fixture_id JOIN people p ON p.id=a.person_id ORDER BY f.starts_at,a.role")
            st.download_button("Download CSV",pd.DataFrame(export).to_csv(index=False),"sgia_allocations.csv","text/csv")

