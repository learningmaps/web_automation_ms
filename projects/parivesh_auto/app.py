import streamlit as st
import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor
import time
import io
import re
import traceback
import logging
import os
import sys
import requests
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables from .env if present
load_dotenv()

# Setup Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("PariveshApp")

def get_secret(key):
    """Helper to get secret from st.secrets or environment variable."""
    try:
        return st.secrets.get(key) or os.getenv(key)
    except Exception:
        return os.getenv(key)

# ─── DATABASE CONNECTION ───
def get_db_connection():
    conn_string = get_secret("DATABASE_URL")
    if not conn_string:
        st.error("DATABASE_URL not found in Streamlit secrets or .env")
        st.stop()
    return psycopg2.connect(conn_string, port=6543)

# ─── DATA ENGINE ───
def load_consolidated_data(include_text=False):
    conn_string = get_secret("DATABASE_URL")
    if not conn_string:
        return pd.DataFrame()
    
    conn = psycopg2.connect(conn_string, port=6543)
    
    cols = [
        "id", "processed_on", "norm_subject", "meeting_id", "date", 
        "committee_type", "matched_keywords", "agenda_pdf_path", "mom_pdf_path",
        "meeting_start_date", "meeting_end_date", "sector_name", 
        "statename_derived", "is_processed", "raw_subject"
    ]
    
    col_str = ", ".join([f"mv.{c}" for c in cols])
    
    if include_text:
        query = f"""
            SELECT {col_str}, base.pdf_text 
            FROM parivesh.mv_consolidated_projects mv
            JOIN parivesh.agenda_v3 base ON mv.id = base.id
            ORDER BY mv.id DESC
        """
    else:
        query = f"SELECT {', '.join(cols)} FROM parivesh.mv_consolidated_projects ORDER BY id DESC"
    
    try:
        df = pd.read_sql_query(query, conn)
        if not df.empty:
            df['id'] = df['id'].astype(str)
    except Exception as e:
        logger.error(f"Error loading data: {e}")
        df = pd.DataFrame()
    finally:
        conn.close()
    return df

def load_base_metrics():
    """Query agenda_v3 directly for counts that should always be fresh (not from materialized view)."""
    conn_string = get_secret("DATABASE_URL")
    if not conn_string:
        return {"unprocessed": 0, "keyword_matches": 0}
    conn = psycopg2.connect(conn_string, port=6543)
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM parivesh.agenda_v3 WHERE is_processed = 0 AND ref_type = 'AGENDA'")
        unprocessed = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM parivesh.agenda_v3 WHERE matched_keywords IS NOT NULL AND ref_type = 'AGENDA'")
        keyword_matches = cur.fetchone()[0]
    except Exception as e:
        logger.error(f"Error loading base metrics: {e}")
        unprocessed = 0
        keyword_matches = 0
    finally:
        conn.close()
    return {"unprocessed": unprocessed, "keyword_matches": keyword_matches}


def load_proposals_data(limit=200):
    conn_string = get_secret("DATABASE_URL")
    if not conn_string:
        return pd.DataFrame()
    conn = psycopg2.connect(conn_string, port=6543)
    query = """
        SELECT
            p.id, p.sr_no, p.proposal_no, p.file_no,
            p.project_name, p.proposal_for, p.sector,
            p.state, p.district, p.proponent,
            p.meeting_date, p.meeting_id, p.created_on,
            a.pdffilepath AS agenda_pdf_path,
            a.norm_subject, a.committee_type
        FROM parivesh.extracted_proposals p
        JOIN parivesh.agenda_v3 a ON p.agenda_id = a.id
        ORDER BY p.created_on DESC, p.sr_no
        LIMIT %s
    """
    try:
        df = pd.read_sql_query(query, conn, params=[limit])
        if not df.empty:
            df['id'] = df['id'].astype(str)
    except Exception as e:
        logger.error(f"Error loading proposals: {e}")
        df = pd.DataFrame()
    finally:
        conn.close()
    return df

def refresh_materialized_view():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Increase timeout to 5 minutes specifically for this operation
        cur.execute("SET statement_timeout = '300s'")
        cur.execute("REFRESH MATERIALIZED VIEW parivesh.mv_consolidated_projects")
        conn.commit()
        st.success("Database View Refreshed!")
    except Exception as e:
        st.error("Failed to refresh materialized view (Database Timeout).")
        st.exception(e)
    finally:
        conn.close()

def trigger_parivesh_scrape_workflow(limit=50):
    token = get_secret("GITHUB_TOKEN")
    repo = get_secret("GITHUB_REPO")
    
    if not token or not repo:
        st.error(f"GitHub Credentials missing. Token: {'Set' if token else 'Missing'}, Repo: {repo if repo else 'Missing'}")
        return False

    url = f"https://api.github.com/repos/{repo}/actions/workflows/parivesh_scrape.yml/dispatches"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
    }
    data = {"ref": "main", "inputs": {"limit": str(limit)}}
    
    try:
        resp = requests.post(url, headers=headers, json=data)
        if resp.status_code != 204:
            st.error(f"GitHub API Error: {resp.status_code} - {resp.text}")
            return False
        return True
    except Exception as e:
        st.error(f"Request failed: {e}")
        return False

def run_parivesh():
    # Ensure parent 'projects' directory is in sys.path to allow absolute sub-project imports
    current_dir = os.path.dirname(os.path.abspath(__file__))
    parent_dir = os.path.dirname(current_dir)
    if parent_dir not in sys.path:
        sys.path.insert(0, parent_dir)
        
    from parivesh_auto.utils import PariveshScraper
    from parivesh_auto.constants import KEYWORDS, TABLE_NAME, PROPOSALS_TABLE_NAME

    # ─── STYLING ───
    st.markdown("""
        <style>
        .main { background-color: #f8f9fa; }
        /* Accent Button Styling */
        div.stButton > button {
            background-color: white !important;
            color: #0F172A !important;
            border: 1px solid #ff4b4b !important;
            font-weight: 600 !important;
            height: 42px !important;
            border-radius: 8px !important;
            transition: all 0.2s ease !important;
        }
        div.stButton > button:hover {
            background-color: #ff4b4b !important;
            color: white !important;
            box-shadow: 0 4px 12px rgba(255, 75, 75, 0.2) !important;
            transform: translateY(-1px);
        }
        div.stButton > button:active {
            transform: translateY(0);
        }
        
        /* Sidebar Refinement */
        [data-testid="stSidebar"] {
            background-color: #FFFFFF;
            border-right: 1px solid #E2E8F0;
        }
        [data-testid="stSidebar"] .stHeader {
            color: #ff4b4b;
        }
        
        /* Checkbox/Radio Accent */
        div[data-testid="stCheckbox"] label p {
            font-weight: 500;
        }
        </style>
        """, unsafe_allow_html=True)

    # ─── SIDEBAR DIAGNOSTICS ───
    with st.sidebar:
        st.header("Settings & Data")
        include_text = st.checkbox("Include PDF Text", value=False, help="Loading text data increases load time significantly.")
        
        st.divider()
        if st.checkbox("Show Database Diagnostics"):
            st.subheader("DB Status")
            try:
                conn = get_db_connection()
                cur = conn.cursor()
                # Use schema prefix for the diagnostic queries
                full_table_name = f"parivesh.{TABLE_NAME}"
                cur.execute(f"SELECT COUNT(*) FROM {full_table_name}")
                total_rows = cur.fetchone()[0]
                st.write(f"Total rows in `{full_table_name}`: **{total_rows}**")
                cur.execute(f"SELECT ref_type, COUNT(*) FROM {full_table_name} GROUP BY ref_type")
                ref_counts = cur.fetchall()
                st.write("Distribution:")
                for r, c in ref_counts:
                    st.write(f"- {r}: {c}")
                proposals_table = f"parivesh.{PROPOSALS_TABLE_NAME}"
                cur.execute(f"SELECT COUNT(*) FROM {proposals_table}")
                st.write(f"Proposals in `{proposals_table}`: **{cur.fetchone()[0]}**")
                conn.close()
            except Exception as e:
                st.error("Diagnostics failed.")
                st.exception(e)
        


    # ─── HEADER SECTION ───
    col1, col2 = st.columns([1.2, 2])
    with col1:
        st.title("Parivesh Dashboard")
        st.markdown("<p style='color: #64748B; margin-top: -15px;'>Automated Monitoring & Data Management System</p>", unsafe_allow_html=True)

    with col2:
        st.write("")
        st.write("")
        
        c1, c2, c3 = st.columns([1.2, 1, 1])
        with c1:
            if st.button("Fetch New Documents", use_container_width=True, help="Downloads the latest meeting agendas and minutes from the Parivesh server."):
                st.session_state.is_syncing = True
        with c2:
            if st.button("Stop Sync", use_container_width=True, help="Safely stops the background sync process."):
                st.session_state.is_syncing = False
        with c3:
            if st.button("Refresh View", use_container_width=True, help="Re-calculates the consolidated view in the database."):
                refresh_materialized_view()
                st.rerun()

        st.write("")
        c_gh1, c_gh2 = st.columns([1.2, 2])
        with c_gh1:
            limit_gh = st.number_input("Limit (GitHub Action)", min_value=1, max_value=500, value=50, step=10, label_visibility="collapsed")
        with c_gh2:
            if st.button("Trigger GitHub Action", use_container_width=True, help="Trigger the Parivesh Scraper weekly pipeline on GitHub Actions"):
                if trigger_parivesh_scrape_workflow(limit=limit_gh):
                    st.toast("GitHub Action triggered successfully!")
                else:
                    st.error("Failed to trigger GitHub Action.")

        if st.session_state.get('is_syncing', False):
            with st.status("Syncing with Parivesh Server...", expanded=True) as status:
                try:
                    # Use get_secret to pull the correct unified URL
                    scraper = PariveshScraper(conn_string=get_secret("DATABASE_URL"), keywords=KEYWORDS, table_name=TABLE_NAME)
                    
                    # Stage 1: Metadata Fetching
                    committees = ["SEIAA", "SEAC", "EAC"]
                    ref_types = ["AGENDA", "MOM"]
                    total_meta = len(committees) * len(ref_types)
                    meta_bar = st.progress(0, text="Initializing metadata fetch...")
                    
                    new_docs_total = 0
                    for i, (fetch_msg, new_count) in enumerate(scraper.fetch_all_committees(committees, ref_types), 1):
                        new_docs_total += new_count
                        meta_bar.progress(i / total_meta, text=f"Stage 1/2: {fetch_msg}")
                    
                    meta_bar.empty()
                    
                    # Stage 2: PDF Processing
                    my_bar = st.progress(0, text="Stage 2/2: Preparing PDF processing...")
                    processed_total = 0
                    for progress in scraper.process_pdfs_and_update():
                        processed_total += 1
                        curr, total = progress["current"], progress["total"]
                        pct = curr / total
                        my_bar.progress(pct, text=f"Stage 2/2: Processing {curr}/{total} (ID: {progress['id']}) - {progress['status']}")
                    
                    scraper.close()
                    status.write("Finalizing view...")
                    refresh_materialized_view()
                    
                    # Display Stats
                    st.session_state.last_sync_stats = {
                        "new_docs": new_docs_total,
                        "processed_pdfs": processed_total,
                        "time": datetime.now().strftime("%H:%M:%S")
                    }
                    
                    status.update(label="Sync Complete", state="complete", expanded=False)
                except Exception as e:
                    st.error(f"Sync failed due to network error: {e}")
                    st.info("The Parivesh server may have closed the connection. Retrying later is recommended.")
                    if 'scraper' in locals():
                        scraper.close()
            st.session_state.is_syncing = False
            st.rerun()

    # ─── SYNC STATS CALLOUT ───
    if "last_sync_stats" in st.session_state:
        stats = st.session_state.last_sync_stats
        st.success(f"Last Sync Successful ({stats['time']}): Added **{stats['new_docs']}** new documents and processed **{stats['processed_pdfs']}** PDFs.")
        if st.button("Clear Stats"):
            del st.session_state.last_sync_stats
            st.rerun()

    st.divider()

    # ─── MAIN CONTENT ───
    try:
        df = load_consolidated_data(include_text=include_text)

        if df.empty:
            st.info("No records found. Click 'Fetch New Documents' to begin.")
        else:
            # ─── SMART FILTERS ───
            with st.container():
                f1, f2, f3, f4 = st.columns(4)
                with f1:
                    subject_search = st.text_input("Search Subject", placeholder="Type keywords...")
                with f2:
                    all_states = sorted(df['statename_derived'].dropna().unique().tolist())
                    selected_states = st.multiselect("State Name", options=all_states)
                with f3:
                    all_committees = sorted(df['committee_type'].dropna().unique().tolist())
                    selected_committees = st.multiselect("Committee Type", options=all_committees)
                with f4:
                    status_filter = st.selectbox("Process Status", options=["All", "Processed", "Pending"])

                d1, d2, d3, d4 = st.columns(4)
                with d1:
                    meeting_range = st.date_input("Meeting Date Range", value=[], help="Select start and end dates")
                with d2:
                    processed_range = st.date_input("Processed On Range", value=[], help="Select start and end dates")
                with d3:
                    kws_set = set()
                    df['matched_keywords'].dropna().apply(lambda x: kws_set.update(x.split(',')) if x else None)
                    keyword_filter = st.multiselect("Keyword Filter", options=sorted(list(kws_set)))
                with d4:
                    mom_filter = st.selectbox("MOM Status", options=["All", "With MOM", "Without MOM"])

                filtered_df = df.copy()
                
                if subject_search:
                    filtered_df = filtered_df[filtered_df['norm_subject'].str.contains(subject_search, case=False, na=False)]
                if selected_states:
                    filtered_df = filtered_df[filtered_df['statename_derived'].isin(selected_states)]
                if selected_committees:
                    filtered_df = filtered_df[filtered_df['committee_type'].isin(selected_committees)]
                if status_filter == "Processed":
                    filtered_df = filtered_df[filtered_df['is_processed'] == 1]
                elif status_filter == "Pending":
                    filtered_df = filtered_df[filtered_df['is_processed'] == 0]
                if mom_filter == "With MOM":
                    filtered_df = filtered_df[filtered_df['mom_pdf_path'].notna()]
                elif mom_filter == "Without MOM":
                    filtered_df = filtered_df[filtered_df['mom_pdf_path'].isna()]
                if keyword_filter:
                    filtered_df = filtered_df[filtered_df['matched_keywords'].apply(
                        lambda x: any(kw in str(x) for kw in keyword_filter) if pd.notna(x) else False
                    )]
                if len(meeting_range) == 2:
                    start_date, end_date = meeting_range
                    temp_dates = pd.to_datetime(filtered_df['date'], errors='coerce').dt.date
                    filtered_df = filtered_df[(temp_dates >= start_date) & (temp_dates <= end_date)]
                if len(processed_range) == 2:
                    start_date, end_date = processed_range
                    temp_proc = pd.to_datetime(filtered_df['processed_on'], errors='coerce').dt.date
                    filtered_df = filtered_df[(temp_proc >= start_date) & (temp_proc <= end_date)]

            # ─── METRICS ───
            base_metrics = load_base_metrics()
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Viewing", len(filtered_df), delta=f"Total: {len(df)}")
            m2.metric("With MOM", len(filtered_df[filtered_df['mom_pdf_path'].notna()]))
            m3.metric("Unprocessed", base_metrics["unprocessed"])
            m4.metric("Keyword Matches", base_metrics["keyword_matches"])

            # ─── MAIN CONSOLIDATED DATAFRAME ───
            st.markdown(f"### Consolidated Data (Agenda + MOM) ({len(filtered_df)})")
            
            col_config = {
                "id": None,
                "is_processed": st.column_config.CheckboxColumn("Processed", width="small"),
                "processed_on": st.column_config.DatetimeColumn("Processed On"),
                "norm_subject": st.column_config.TextColumn("Normalized Subject"),
                "meeting_id": st.column_config.TextColumn("Meeting ID"),
                "date": st.column_config.DateColumn("Date"),
                "committee_type": st.column_config.TextColumn("Committee"),
                "statename_derived": st.column_config.TextColumn("State"),
                "matched_keywords": st.column_config.TextColumn("Keywords"),
                "agenda_pdf_path": st.column_config.LinkColumn("Agenda PDF"),
                "mom_pdf_path": st.column_config.LinkColumn("MOM PDF"),
                "raw_subject": None,
            }
            if include_text:
                col_config["pdf_text"] = st.column_config.TextColumn("PDF Text", width="small")
            else:
                col_config["pdf_text"] = None

            st.dataframe(
                filtered_df,
                use_container_width=True,
                height=600,
                column_config=col_config,
                hide_index=True
            )

            # ─── FOOTER ACTIONS ───
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                filtered_df.to_excel(writer, index=False, sheet_name='Consolidated')
                workbook = writer.book
                worksheet = writer.sheets['Consolidated']
                
                header_format = workbook.add_format({
                    'bold': True, 'text_wrap': False, 'valign': 'vcenter',
                    'fg_color': '#1F4E78', 'font_color': 'white', 'border': 1
                })
                cell_format = workbook.add_format({'valign': 'top', 'text_wrap': False, 'border': 1})
                
                worksheet.set_default_row(20)
                worksheet.freeze_panes(1, 0)
                worksheet.autofilter(0, 0, len(filtered_df), len(filtered_df.columns) - 1)
                for col_num, value in enumerate(filtered_df.columns.values):
                    worksheet.write(0, col_num, value, header_format)
                    if value in ['raw_subject', 'pdf_text']:
                        width = 60
                    elif value in ['matched_keywords']:
                        width = 40
                    else:
                        width = 20
                    worksheet.set_column(col_num, col_num, width, cell_format)
            
            st.download_button(
                label="Download Consolidated Data as Excel",
                data=output.getvalue(),
                file_name=f"parivesh_export_{int(time.time())}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                help="Saves the currently filtered results into an Excel (.xlsx) file for offline analysis."
            )

            # ─── PROPOSALS SECTION ───
            st.divider()
            st.markdown("### Extracted Proposals")

            proposals_df = load_proposals_data()

            if proposals_df.empty:
                st.info("No extracted proposals found. Process PDFs via 'Fetch New Documents' to generate proposals.")
            else:
                p1, p2 = st.columns([1, 3])
                with p1:
                    state_filter = st.multiselect(
                        "State", options=sorted(proposals_df['state'].dropna().unique()),
                        key="prop_state"
                    )
                with p2:
                    sector_filter = st.multiselect(
                        "Sector", options=sorted(proposals_df['sector'].dropna().unique()),
                        key="prop_sector"
                    )

                filtered_proposals = proposals_df.copy()
                if state_filter:
                    filtered_proposals = filtered_proposals[filtered_proposals['state'].isin(state_filter)]
                if sector_filter:
                    filtered_proposals = filtered_proposals[filtered_proposals['sector'].isin(sector_filter)]

                st.metric("Proposals", len(filtered_proposals), delta=f"Total: {len(proposals_df)}")

                prop_col_config = {
                    "id": None,
                    "sr_no": st.column_config.NumberColumn("S.No", width="small"),
                    "proposal_no": st.column_config.TextColumn("Proposal No"),
                    "file_no": st.column_config.TextColumn("File No"),
                    "project_name": st.column_config.TextColumn("Project Name"),
                    "proposal_for": st.column_config.TextColumn("Proposal For"),
                    "sector": st.column_config.TextColumn("Sector"),
                    "state": st.column_config.TextColumn("State"),
                    "district": st.column_config.TextColumn("District"),
                    "proponent": st.column_config.TextColumn("Proponent"),
                    "committee_type": st.column_config.TextColumn("Committee"),
                    "agenda_pdf_path": st.column_config.LinkColumn("Agenda PDF"),
                    "norm_subject": st.column_config.TextColumn("Agenda Subject", width="medium"),
                    "meeting_date": st.column_config.TextColumn("Meeting Date"),
                    "created_on": st.column_config.DatetimeColumn("Extracted On", format="DD-MM-YYYY HH:mm"),
                    "meeting_id": None,
                }

                st.dataframe(
                    filtered_proposals,
                    use_container_width=True,
                    height=500,
                    column_config=prop_col_config,
                    hide_index=True,
                    key="proposals_table"
                )

    except Exception as e:
        st.error("A critical error occurred in the application UI.")
        st.exception(e)
        if st.button("Reset App State", help="Clears internal session state and reruns the app to resolve persistent errors."):
            st.session_state.clear()
            st.rerun()

if __name__ == "__main__":
    st.set_page_config(
        page_title="Parivesh Dashboard",
        layout="wide",
        initial_sidebar_state="collapsed"
    )
    run_parivesh()
