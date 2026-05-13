import streamlit as st
import sys
import os
import psycopg2
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Ensure sub-projects can be imported
sys.path.append(os.path.join(os.getcwd(), "projects"))

# Page config
st.set_page_config(
    page_title="Web Automations",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# Design System Implementation
st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
    
    /* Global Reset & Vertical Space Optimization */
    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif;
    }
    .block-container {
        padding-top: 2rem !important;
        padding-bottom: 0rem !important;
        max-width: 1100px;
    }
    [data-testid="stMetricValue"] {
        font-size: 26px !important;
        font-weight: 700 !important;
        color: #0F172A;
    }
    [data-testid="stMetricLabel"] {
        font-size: 13px !important;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        color: #64748B;
    }
    
    /* Header Refinement */
    .main-title {
        text-align: left;
        font-size: 32px;
        font-weight: 700;
        color: #0F172A;
        letter-spacing: -0.025em;
        margin-bottom: 4px;
    }
    .sub-title {
        text-align: left;
        margin-bottom: 24px;
        color: #64748B;
        font-size: 15px;
    }

    /* Sleek Project Cards */
    .project-card {
        padding: 1.25rem;
        border-radius: 12px;
        background: #FFFFFF;
        border: 1px solid #E2E8F0;
        min-height: 140px;
        transition: all 0.2s ease;
    }
    .project-card:hover {
        border-color: #ff4b4b;
        box-shadow: 0 4px 12px rgba(255, 75, 75, 0.1);
    }
    .card-title {
        font-size: 18px;
        font-weight: 600;
        color: #1E293B;
        margin-bottom: 8px;
    }
    .card-body {
        color: #475569;
        font-size: 14px;
        line-height: 1.5;
        margin-bottom: 16px;
    }

    /* Button Optimization - Universal Style */
    div.stButton > button {
        border-radius: 8px !important;
        font-size: 14px !important;
        font-weight: 600 !important;
        height: 42px !important;
        transition: all 0.2s ease !important;
        border: 1px solid #ff4b4b !important;
        background-color: white !important;
        color: #0F172A !important;
    }
    div.stButton > button:hover {
        border-color: #ff4b4b !important;
        background-color: #ff4b4b !important;
        color: white !important;
        box-shadow: 0 4px 12px rgba(255, 75, 75, 0.2) !important;
        transform: translateY(-1px);
    }
    div.stButton > button:active {
        transform: translateY(0);
    }
    
    /* Nav Button Refinement - Maintain Accent with Icon Support */
    div[data-testid="stColumn"] button[key="nav_home"],
    div[data-testid="stColumn"] button[key="nav_mstc"],
    div[data-testid="stColumn"] button[key="nav_parivesh"],
    div[data-testid="stColumn"] button[key="nav_refresh"] {
        width: 44px !important;
        height: 44px !important;
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
        padding: 0 !important;
        overflow: visible !important;
    }
    /* Target the icon container specifically for all nav buttons */
    div[data-testid="stColumn"] button[key="nav_home"] [data-testid="stIconMaterial"],
    div[data-testid="stColumn"] button[key="nav_home"] span:first-child,
    div[data-testid="stColumn"] button[key="nav_mstc"] [data-testid="stIconMaterial"],
    div[data-testid="stColumn"] button[key="nav_mstc"] span:first-child,
    div[data-testid="stColumn"] button[key="nav_parivesh"] [data-testid="stIconMaterial"],
    div[data-testid="stColumn"] button[key="nav_parivesh"] span:first-child {
        color: #ff4b4b !important;
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
        margin-top: 1px !important;
        font-size: 24px !important;
    }
    /* Special styling for Refresh Button (Black background, White icon) */
    div[data-testid="stColumn"] button[key="nav_refresh"] {
        background-color: #0F172A !important;
        border-color: #0F172A !important;
    }
    div[data-testid="stColumn"] button[key="nav_refresh"] [data-testid="stIconMaterial"],
    div[data-testid="stColumn"] button[key="nav_refresh"] span:first-child {
        color: white !important;
        font-size: 22px !important;
    }
    
    div[data-testid="stColumn"] button[key="nav_home"]:hover [data-testid="stIconMaterial"],
    div[data-testid="stColumn"] button[key="nav_home"]:hover span:first-child,
    div[data-testid="stColumn"] button[key="nav_mstc"]:hover [data-testid="stIconMaterial"],
    div[data-testid="stColumn"] button[key="nav_mstc"]:hover span:first-child,
    div[data-testid="stColumn"] button[key="nav_parivesh"]:hover [data-testid="stIconMaterial"],
    div[data-testid="stColumn"] button[key="nav_parivesh"]:hover span:first-child {
        color: white !important;
    }
    /* Refresh Hover Effect */
    div[data-testid="stColumn"] button[key="nav_refresh"]:hover {
        background-color: #1E293B !important;
        border-color: #1E293B !important;
    }
    </style>
""", unsafe_allow_html=True)

# ─── DATA UTILITIES ───
def get_hub_metrics():
    """Fetch high-value stats from both schemas with 7-day velocity."""
    db_url = os.getenv("DATABASE_URL")
    metrics = {
        "mstc_blocks": 0, "mstc_total": 0, "mstc_7d": 0,
        "parivesh_hits": 0, "parivesh_total": 0, "parivesh_7d": 0
    }
    if not db_url: return metrics
    
    try:
        conn = psycopg2.connect(db_url, port=6543)
        cur = conn.cursor()
        
        # --- MSTC Group ---
        cur.execute("SELECT COUNT(*) FROM mstc.mine_block_summaries")
        metrics["mstc_blocks"] = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM mstc.processed_pdfs WHERE status = 'processed'")
        metrics["mstc_total"] = cur.fetchone()[0]
        # Velocity: New processed PDFs in last 7 days
        cur.execute("SELECT COUNT(*) FROM mstc.processed_pdfs WHERE extracted_at::timestamp > NOW() - INTERVAL '7 days'")
        metrics["mstc_7d"] = cur.fetchone()[0]
        
        # --- Parivesh Group ---
        cur.execute("SELECT COUNT(*) FROM parivesh.agenda_v3 WHERE matched_keywords IS NOT NULL")
        metrics["parivesh_hits"] = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM parivesh.agenda_v3")
        metrics["parivesh_total"] = cur.fetchone()[0]
        # Velocity: New agendas in last 7 days (checking created_on or processed_on)
        cur.execute("SELECT COUNT(*) FROM parivesh.agenda_v3 WHERE processed_on::timestamp > NOW() - INTERVAL '7 days'")
        metrics["parivesh_7d"] = cur.fetchone()[0]
        
        conn.close()
    except Exception:
        pass 
    return metrics

# ─── NAVIGATION STATE ───
if "active_app" not in st.session_state:
    st.session_state.active_app = None

def go_home():
    st.session_state.active_app = None
    st.rerun()

# ─── TOP NAVIGATION ───
if st.session_state.active_app:
    c1, c2, c3, _ = st.columns([1, 1, 1, 23])
    with c1:
        if st.button("", icon=":material/home:", help="Home", key="nav_home"):
            go_home()
    with c2:
        if st.session_state.active_app == "mstc":
            if st.button("", icon=":material/eco:", help="Switch to Parivesh Dashboard", key="nav_parivesh"):
                st.session_state.active_app = "parivesh"
                st.rerun()
        elif st.session_state.active_app == "parivesh":
            if st.button("", icon=":material/layers:", help="Switch to MSTC Dashboard", key="nav_mstc"):
                st.session_state.active_app = "mstc"
                st.rerun()
    with c3:
        if st.button("", icon=":material/refresh:", help="Refresh Data", key="nav_refresh"):
            st.rerun()

# ─── APP ROUTING ───
if st.session_state.active_app == "mstc":
    from mstc_py.app import run_mstc
    run_mstc()

elif st.session_state.active_app == "parivesh":
    from parivesh_auto.app import run_parivesh
    run_parivesh()

else:
    # ─── WEB AUTOMATIONS HUB ───
    st.markdown('<div class="main-title">Web Automations</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-title">Intelligence and monitoring for mining and environmental data.</div>', unsafe_allow_html=True)

    st.write("") 
    st.write("") 

    # ─── PROJECT TILES ───
    col1, col2 = st.columns(2, gap="large")

    with col1:
        st.markdown("""
        <div class="project-card">
            <div class="card-title">MSTC Mineral Blocks</div>
            <div class="card-body">
                Structured extraction of geological, land area, and resource data from auction notices.
            </div>
        </div>
        """, unsafe_allow_html=True)
        if st.button("Launch MSTC Portal", key="btn_mstc", use_container_width=True):
            st.session_state.active_app = "mstc"
            st.rerun()

    with col2:
        st.markdown("""
        <div class="project-card">
            <div class="card-title">Parivesh Monitoring</div>
            <div class="card-body">
                Environmental clearance tracking with automated agenda-to-MOM cross-referencing.
            </div>
        </div>
        """, unsafe_allow_html=True)
        if st.button("Launch Parivesh Portal", key="btn_parivesh", use_container_width=True):
            st.session_state.active_app = "parivesh"
            st.rerun()

    # Footer
    st.markdown("<br><br>", unsafe_allow_html=True)
    st.caption(f"Unified Intelligence Database | Session active: {datetime.now().strftime('%H:%M')}")
