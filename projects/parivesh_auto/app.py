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

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("PariveshApp")


def get_secret(key):
    try:
        return st.secrets.get(key) or os.getenv(key)
    except Exception:
        return os.getenv(key)

def get_db_connection():
    conn_string = get_secret("DATABASE_URL")
    if not conn_string:
        st.error("DATABASE_URL not found in Streamlit secrets or .env")
        st.stop()
    return psycopg2.connect(conn_string, port=6543)

# ─── DATA LOADING ───

def load_agendas():
    conn = get_db_connection()
    query = """
        SELECT id, norm_subject, meeting_id, date, committee_type,
               meeting_start_date, meeting_end_date, sector_name,
               statename_derived, matched_keywords, pdffilepath,
               is_processed, processed_on, subject AS raw_subject,
               pdf_storage_url, mom_pdf_storage_url
        FROM parivesh.agenda_v3
        WHERE ref_type = 'AGENDA' AND matched_keywords IS NOT NULL
        ORDER BY date DESC NULLS LAST
    """
    try:
        return pd.read_sql_query(query, conn)
    except Exception as e:
        logger.error(f"Error loading agendas: {e}")
        return pd.DataFrame()
    finally:
        conn.close()

def load_proposals(agenda_ids):
    if not agenda_ids:
        return pd.DataFrame()
    conn = get_db_connection()
    query = """
        SELECT id, agenda_id, sr_no, proposal_no, file_no, project_name,
               proposal_for, activity, sector, state, district,
               proponent, meeting_date, meeting_id, created_on
        FROM parivesh.extracted_proposals
        WHERE agenda_id = ANY(%s)
    """
    try:
        return pd.read_sql_query(query, conn, params=[agenda_ids])
    except Exception as e:
        logger.error(f"Error loading proposals: {e}")
        return pd.DataFrame()
    finally:
        conn.close()

def load_moms(norm_subjects):
    clean = [s for s in norm_subjects if s and pd.notna(s)]
    if not clean:
        return pd.DataFrame()
    conn = get_db_connection()
    query = """
        SELECT id, norm_subject, meeting_id, date, committee_type,
               pdffilepath, subject AS raw_subject,
               meeting_start_date, meeting_end_date,
               pdf_storage_url
        FROM parivesh.agenda_v3
        WHERE ref_type = 'MOM'
        AND norm_subject = ANY(%s)
    """
    try:
        df = pd.read_sql_query(query, conn, params=[clean])
        if not df.empty:
            df['id'] = df['id'].astype(str)
    except Exception as e:
        logger.error(f"Error loading MOMs: {e}")
        df = pd.DataFrame()
    finally:
        conn.close()
    return df

def load_mom_norm_subjects():
    """Fast query: returns a set of norm_subject values that have MOMs."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT DISTINCT norm_subject FROM parivesh.agenda_v3
            WHERE ref_type = 'MOM' AND norm_subject IS NOT NULL
            AND norm_subject IN (
                SELECT norm_subject FROM parivesh.agenda_v3
                WHERE ref_type = 'AGENDA' AND matched_keywords IS NOT NULL
            )
        """)
        return set(row[0] for row in cur.fetchall())
    except Exception as e:
        logger.error(f"Error loading MOM subjects: {e}")
        return set()
    finally:
        conn.close()

def load_proposal_filter_options():
    """Fast queries: returns dict of distinct values for proposal filter dropdowns."""
    conn = get_db_connection()
    cur = conn.cursor()
    options = {}
    try:
        for col in ['state', 'sector', 'proposal_for', 'district']:
            cur.execute(f"""
                SELECT DISTINCT e.{col} FROM parivesh.extracted_proposals e
                JOIN parivesh.agenda_v3 a ON e.agenda_id = a.id
                WHERE a.ref_type = 'AGENDA' AND a.matched_keywords IS NOT NULL
                AND e.{col} IS NOT NULL ORDER BY e.{col}
            """)
            options[col] = [row[0] for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"Error loading filter options: {e}")
        options = {'state': [], 'sector': [], 'proposal_for': [], 'district': []}
    finally:
        conn.close()
    return options

def load_proposal_matching_agenda_ids(state=None, sector=None, proposal_for=None,
                                      district=None, proponent=None, proposal_no=None):
    """Return agenda_ids (from keyword-matched AGENDA set) whose extracted proposals match given filters."""
    conn = get_db_connection()
    cur = conn.cursor()
    conditions = []
    params = []
    if state:
        conditions.append("ep.state = ANY(%s)")
        params.append(state)
    if sector:
        conditions.append("ep.sector = ANY(%s)")
        params.append(sector)
    if proposal_for:
        conditions.append("ep.proposal_for = ANY(%s)")
        params.append(proposal_for)
    if district:
        conditions.append("ep.district = ANY(%s)")
        params.append(district)
    if proponent:
        conditions.append("ep.proponent ILIKE %s")
        params.append(f"%{proponent}%")
    if proposal_no:
        conditions.append("ep.proposal_no ILIKE %s")
        params.append(f"%{proposal_no}%")
    try:
        where = " AND ".join(conditions) if conditions else "TRUE"
        cur.execute(f"""
            SELECT DISTINCT ep.agenda_id FROM parivesh.extracted_proposals ep
            JOIN parivesh.agenda_v3 a ON ep.agenda_id = a.id
            WHERE a.ref_type = 'AGENDA' AND a.matched_keywords IS NOT NULL
            AND {where}
        """, params)
        return {row[0] for row in cur.fetchall()}
    except Exception as e:
        logger.error(f"Error loading matching agenda IDs: {e}")
        return set()
    finally:
        conn.close()

def load_base_metrics():
    conn = get_db_connection()
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

# ─── APP ───

def run_parivesh():
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
        div.stButton > button:active { transform: translateY(0); }
        [data-testid="stSidebar"] {
            background-color: #FFFFFF;
            border-right: 1px solid #E2E8F0;
        }
        [data-testid="stSidebar"] .stHeader { color: #ff4b4b; }
        div[data-testid="stCheckbox"] label p { font-weight: 500; }
        .mom-badge { color: #166534; font-weight: 600; }
        .no-mom-badge { color: #991b1b; font-weight: 500; }
        .section-header-agenda {
            background: linear-gradient(135deg, #EFF6FF 0%, #DBEAFE 100%);
            border-left: 4px solid #2563EB;
            padding: 10px 16px;
            border-radius: 8px;
            margin-bottom: 16px;
            font-weight: 600;
            font-size: 17px;
            color: #1E40AF;
        }
        .section-header-mom {
            background: linear-gradient(135deg, #ECFDF5 0%, #D1FAE5 100%);
            border-left: 4px solid #059669;
            padding: 10px 16px;
            border-radius: 8px;
            margin-bottom: 16px;
            font-weight: 600;
            font-size: 17px;
            color: #065F46;
        }
        .section-header-proposals {
            background: linear-gradient(135deg, #FFFBEB 0%, #FEF3C7 100%);
            border-left: 4px solid #D97706;
            padding: 10px 16px;
            border-radius: 8px;
            margin-bottom: 16px;
            font-weight: 600;
            font-size: 17px;
            color: #92400E;
        }
        </style>
    """, unsafe_allow_html=True)

    # ─── SIDEBAR ───
    with st.sidebar:
        st.header("Settings & Data")
        include_text = st.checkbox("Include PDF Text", value=False, help="Loading text data increases load time significantly.")
        st.divider()
        if st.checkbox("Show Database Diagnostics"):
            st.subheader("DB Status")
            try:
                conn = get_db_connection()
                cur = conn.cursor()
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

    # ─── HEADER ───
    col1, col2 = st.columns([1.2, 2])
    with col1:
        st.title("Parivesh Dashboard")
        st.markdown("<p style='color: #64748B; margin-top: -15px;'>Automated Monitoring & Data Management System</p>", unsafe_allow_html=True)
    with col2:
        st.write("")
        st.write("")
        c1, c2, c3 = st.columns([1.2, 1, 1])
        with c1:
            if st.button("Fetch New Documents", use_container_width=True):
                st.session_state.is_syncing = True
        with c2:
            if st.button("Stop Sync", use_container_width=True):
                st.session_state.is_syncing = False
        with c3:
            st.write("")

        st.write("")
        c_gh1, c_gh2 = st.columns([1.2, 2])
        with c_gh1:
            limit_gh = st.number_input("Limit (GitHub Action)", min_value=1, max_value=500, value=50, step=10, label_visibility="collapsed")
        with c_gh2:
            if st.button("Trigger GitHub Action", use_container_width=True):
                if trigger_parivesh_scrape_workflow(limit=limit_gh):
                    st.toast("GitHub Action triggered successfully!")
                else:
                    st.error("Failed to trigger GitHub Action.")

        if st.session_state.get('is_syncing', False):
            with st.status("Syncing with Parivesh Server...", expanded=True) as status:
                try:
                    scraper = PariveshScraper(conn_string=get_secret("DATABASE_URL"), keywords=KEYWORDS, table_name=TABLE_NAME)
                    committees = ["SEIAA", "SEAC", "EAC"]
                    ref_types = ["AGENDA", "MOM"]
                    total_meta = len(committees) * len(ref_types)
                    meta_bar = st.progress(0, text="Initializing metadata fetch...")
                    new_docs_total = 0
                    for i, (fetch_msg, new_count) in enumerate(scraper.fetch_all_committees(committees, ref_types), 1):
                        new_docs_total += new_count
                        meta_bar.progress(i / total_meta, text=f"Stage 1/2: {fetch_msg}")
                    meta_bar.empty()
                    my_bar = st.progress(0, text="Stage 2/2: Preparing PDF processing...")
                    processed_total = 0
                    for progress in scraper.process_pdfs_and_update():
                        processed_total += 1
                        curr, total = progress["current"], progress["total"]
                        pct = curr / total
                        my_bar.progress(pct, text=f"Stage 2/2: Processing {curr}/{total} (ID: {progress['id']}) - {progress['status']}")
                    scraper.close()
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

    if "last_sync_stats" in st.session_state:
        stats = st.session_state.last_sync_stats
        st.success(f"Last Sync Successful ({stats['time']}): Added **{stats['new_docs']}** new documents and processed **{stats['processed_pdfs']}** PDFs.")
        if st.button("Clear Stats"):
            del st.session_state.last_sync_stats
            st.rerun()

    st.divider()

    # ─── LOAD DATA ───
    try:
        # Phase 1: load agenda metadata + filter options (both cheap queries)
        with st.spinner("Loading agendas..."):
            agendas_df = load_agendas()

        if agendas_df.empty:
            st.info("No agenda records found. Click 'Fetch New Documents' to begin.")
            return

        with st.spinner("Loading filter options..."):
            mom_subjects = load_mom_norm_subjects()
            filter_opts = load_proposal_filter_options()

        all_states = filter_opts['state']
        all_sectors = filter_opts['sector']
        all_prop_for = filter_opts['proposal_for']
        all_districts = filter_opts['district']
        all_committees = sorted(agendas_df['committee_type'].dropna().unique().tolist())
        all_ag_sectors = sorted(agendas_df['sector_name'].dropna().unique().tolist())
        all_ag_states = sorted(agendas_df['statename_derived'].dropna().unique().tolist())

        # ─── AGENDA FILTERS ───
        st.markdown("### Agenda Filters")
        af1, af2, af3, af4 = st.columns(4)
        with af1:
            sel_committee = st.multiselect("Committee", options=all_committees, key="ag_committee")
        with af2:
            meeting_range = st.date_input("Meeting Date Range", value=[], key="ag_date")
        with af3:
            mom_filter = st.selectbox("MOM Status", options=["All", "With MOM", "Without MOM"], key="ag_mom")
        with af4:
            subject_search = st.text_input("Search Subject", placeholder="Type keywords...", key="ag_subject")

        af5, af6, af7, af8 = st.columns(4)
        with af5:
            sel_ag_sector = st.multiselect("Sector", options=all_ag_sectors, key="ag_sector")
        with af6:
            sel_ag_state = st.multiselect("State", options=all_ag_states, key="ag_state")

        af9, af10, af11, af12 = st.columns(4)
        with af9:
            mtg_start_range = st.date_input("Meeting Start Date Range", value=[], key="ag_mtg_start")
        with af10:
            processed_range = st.date_input("Processed On Date Range", value=[], key="ag_processed")
        with af11:
            st.write("")
        with af12:
            st.write("")

        # ─── PROPOSAL FILTERS ───
        st.markdown("### Proposal Filters")
        pf1, pf2, pf3, pf4 = st.columns(4)
        with pf1:
            sel_state = st.multiselect("State", options=all_states, default=[], key="pr_state")
        with pf2:
            sel_sector = st.multiselect("Sector", options=all_sectors, key="pr_sector")
        with pf3:
            sel_prop_for = st.multiselect("Proposal For", options=all_prop_for, key="pr_prop_for")
        with pf4:
            sel_district = st.multiselect("District", options=all_districts, key="pr_district")

        pf5, pf6, pf7 = st.columns([1, 1, 1])
        with pf5:
            proponent_search = st.text_input("Search Proponent", placeholder="Type name...", key="pr_proponent")
        with pf6:
            proposal_search = st.text_input("Search Proposal No", placeholder="e.g. IA/CG/...", key="pr_proposal_no")
        with pf7:
            st.write("")
            st.write("")
            st.markdown(f"**{len(agendas_df)}** total agendas")

        # ─── APPLY AGENDA FILTERS ───
        filtered_agendas = agendas_df.copy()
        if sel_committee:
            filtered_agendas = filtered_agendas[filtered_agendas['committee_type'].isin(sel_committee)]
        if len(meeting_range) == 2:
            sd, ed = meeting_range
            dates = pd.to_datetime(filtered_agendas['date'], errors='coerce').dt.date
            filtered_agendas = filtered_agendas[(dates >= sd) & (dates <= ed)]
        if mom_filter == "With MOM":
            filtered_agendas = filtered_agendas[filtered_agendas['norm_subject'].isin(mom_subjects)]
        elif mom_filter == "Without MOM":
            filtered_agendas = filtered_agendas[~filtered_agendas['norm_subject'].isin(mom_subjects)]
        if subject_search:
            filtered_agendas = filtered_agendas[filtered_agendas['raw_subject'].str.contains(subject_search, case=False, na=False)]
        if sel_ag_sector:
            filtered_agendas = filtered_agendas[filtered_agendas['sector_name'].isin(sel_ag_sector)]
        if sel_ag_state:
            filtered_agendas = filtered_agendas[filtered_agendas['statename_derived'].isin(sel_ag_state)]
        if len(mtg_start_range) == 2:
            sd, ed = mtg_start_range
            dates = pd.to_datetime(filtered_agendas['meeting_start_date'], errors='coerce').dt.date
            filtered_agendas = filtered_agendas[(dates >= sd) & (dates <= ed)]
        if len(processed_range) == 2:
            sd, ed = processed_range
            dates = pd.to_datetime(filtered_agendas['processed_on'], errors='coerce').dt.date
            filtered_agendas = filtered_agendas[(dates >= sd) & (dates <= ed)]

        # Proposal filter flags
        prop_filters_active = bool(sel_state) or bool(sel_sector) or bool(sel_prop_for) or bool(sel_district) or bool(proponent_search) or bool(proposal_search)

        # When proposal filters are active, narrow agendas to those with matching proposals
        if prop_filters_active:
            matching_ids = load_proposal_matching_agenda_ids(
                state=sel_state if sel_state else None,
                sector=sel_sector if sel_sector else None,
                proposal_for=sel_prop_for if sel_prop_for else None,
                district=sel_district if sel_district else None,
                proponent=proponent_search if proponent_search else None,
                proposal_no=proposal_search if proposal_search else None,
            )
            filtered_agendas = filtered_agendas[filtered_agendas['id'].isin(matching_ids)]

        total_filtered = len(filtered_agendas)

        # ─── METRICS ───
        base_metrics = load_base_metrics()
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Agendas", total_filtered)
        m2.metric("With MOM", len(filtered_agendas[filtered_agendas['norm_subject'].isin(mom_subjects)]))
        m3.metric("Unprocessed", base_metrics["unprocessed"])
        m4.metric("Keyword Matches", base_metrics["keyword_matches"])

        # ─── EXPORT ───
        export_output = io.BytesIO()

        # Load proposals and MOMs for the filtered set
        filtered_ids = filtered_agendas['id'].tolist()
        filtered_norm_subjects = filtered_agendas['norm_subject'].dropna().unique().tolist()
        export_proposals = load_proposals(filtered_ids)
        export_moms = load_moms(filtered_norm_subjects)

        # ── Sheet 1: Proposal Details ──
        if not export_proposals.empty:
            agenda_map = filtered_agendas[[
                'id', 'meeting_id', 'committee_type', 'date', 'subject',
                'meeting_start_date', 'meeting_end_date', 'sector_name',
                'statename_derived', 'matched_keywords', 'pdffilepath',
                'norm_subject'
            ]].rename(columns={
                'id': 'agenda_id',
                'date': 'agenda_date',
                'subject': 'agenda_subject',
                'pdffilepath': 'agenda_pdf_url',
            })

            prop_detail = export_proposals.merge(agenda_map, on='agenda_id', how='left')

            if not export_moms.empty:
                mom_map = export_moms[[
                    'norm_subject', 'date', 'meeting_id', 'raw_subject', 'pdf_storage_url'
                ]].rename(columns={
                    'date': 'mom_date', 'meeting_id': 'mom_meeting_id',
                    'raw_subject': 'mom_subject', 'pdf_storage_url': 'mom_pdf_url',
                })
                mom_map = mom_map.drop_duplicates(subset=['norm_subject'])
                prop_detail = prop_detail.merge(mom_map, on='norm_subject', how='left')
            else:
                for c in ['mom_date', 'mom_meeting_id', 'mom_subject', 'mom_pdf_url']:
                    prop_detail[c] = None

            prop_detail['has_mom'] = prop_detail['mom_date'].notna()

            prop_cols = [
                'agenda_id', 'meeting_id', 'committee_type', 'agenda_date',
                'meeting_start_date', 'meeting_end_date', 'agenda_subject',
                'sector_name', 'statename_derived', 'matched_keywords',
                'sr_no', 'proposal_no', 'file_no', 'project_name',
                'proposal_for', 'activity', 'sector', 'state',
                'district', 'proponent',
                'has_mom', 'mom_date', 'mom_meeting_id', 'mom_subject',
                'agenda_pdf_url', 'mom_pdf_url',
            ]
            prop_detail = prop_detail[[c for c in prop_cols if c in prop_detail.columns]]
        else:
            prop_detail = pd.DataFrame()

        # ── Sheet 2: Agendas Summary ──
        agendas_summary = filtered_agendas[[
            'id', 'meeting_id', 'date', 'committee_type', 'subject',
            'sector_name', 'statename_derived', 'matched_keywords',
            'pdffilepath', 'mom_pdf_storage_url'
        ]].rename(columns={
            'id': 'agenda_id',
            'subject': 'agenda_subject',
            'pdffilepath': 'agenda_pdf_url',
        })
        agendas_summary['has_mom'] = agendas_summary['agenda_id'].isin(
            filtered_agendas[filtered_agendas['norm_subject'].isin(mom_subjects)]['id']
        )
        if not export_proposals.empty:
            prop_counts = export_proposals.groupby('agenda_id').size().reset_index(name='proposal_count')
            agendas_summary = agendas_summary.merge(prop_counts, on='agenda_id', how='left')
            agendas_summary['proposal_count'] = agendas_summary['proposal_count'].fillna(0).astype(int)
        else:
            agendas_summary['proposal_count'] = 0

        # ── Write Excel ──
        with pd.ExcelWriter(export_output, engine='xlsxwriter') as writer:
            sheet_configs = []
            if not prop_detail.empty:
                prop_detail.to_excel(writer, index=False, sheet_name='Proposal Details')
                sheet_configs.append(('Proposal Details', prop_detail))
            agendas_summary.to_excel(writer, index=False, sheet_name='Agendas Summary')
            sheet_configs.append(('Agendas Summary', agendas_summary))

            workbook = writer.book
            header_fmt = workbook.add_format({
                'bold': True, 'text_wrap': False, 'valign': 'vcenter',
                'fg_color': '#1F4E78', 'font_color': 'white', 'border': 1
            })
            cell_fmt = workbook.add_format({'valign': 'top', 'text_wrap': False, 'border': 1})
            wide_cols = {'agenda_subject', 'mom_subject', 'agenda_pdf_url', 'mom_pdf_url'}

            for sheet_name, df in sheet_configs:
                ws = writer.sheets[sheet_name]
                ws.set_default_row(20)
                ws.freeze_panes(1, 0)
                ws.autofilter(0, 0, len(df), len(df.columns) - 1)
                for col_num, col_name in enumerate(df.columns):
                    ws.write(0, col_num, col_name, header_fmt)
                    width = 40 if col_name in wide_cols else 20
                    ws.set_column(col_num, col_num, width, cell_fmt)

        # ─── AGENDA TABLE ───
        display_df = filtered_agendas.copy()
        display_df['_mom_status'] = display_df['norm_subject'].apply(
            lambda x: '✓' if pd.notna(x) and str(x) in mom_subjects else '✗'
        )
        display_df['_subject_short'] = display_df['raw_subject'].apply(
            lambda x: (str(x)[:80] + '...') if x and len(str(x)) > 80 else (str(x) if x else '')
        )

        table_cols = ['date', 'meeting_start_date', 'meeting_id', 'committee_type',
                      '_subject_short', 'sector_name', 'statename_derived',
                      '_mom_status', 'matched_keywords', 'processed_on']
        table_df = display_df[table_cols].copy()
        table_df.columns = ['Date', 'Meeting Start', 'Meeting ID', 'Committee',
                            'Subject', 'Sector', 'State',
                            'MOM', 'Keywords', 'Processed On']

        export_col1, export_col2 = st.columns([1, 5])
        with export_col1:
            st.download_button(
                label="⬇ Excel",
                data=export_output.getvalue(),
                file_name=f"parivesh_agendas_{int(time.time())}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )
        with export_col2:
            st.markdown(f"### Agendas ({total_filtered})")
        event = st.dataframe(
            table_df,
            use_container_width=True,
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
        )

        # ─── SELECTION ───
        selected_id = None
        if event and hasattr(event, "selection") and event.selection:
            rows = getattr(event.selection, "rows", [])
            if not rows and isinstance(event.selection, dict):
                rows = event.selection.get("rows", [])
            if rows:
                selected_id = int(filtered_agendas.iloc[rows[0]]['id'])

        if not selected_id and not filtered_agendas.empty:
            selected_id = int(filtered_agendas.iloc[0]['id'])

        if selected_id is None:
            st.info("No agendas match the current filters.")
            st.markdown("---")
        else:
            selected_row = filtered_agendas[filtered_agendas['id'] == selected_id].iloc[0]

            with st.spinner("Loading proposals and MOM..."):
                mom = None
                ns = selected_row['norm_subject']
                if ns and pd.notna(ns):
                    mom_df = load_moms([ns])
                    if not mom_df.empty:
                        mom = mom_df.iloc[0].to_dict()

                proposals_list = []
                proposals_df = load_proposals([selected_id])
                if not proposals_df.empty:
                    proposals_list = proposals_df.to_dict('records')

            # ─── DETAIL: Agenda Metadata ───
            st.markdown("---")
            with st.container(border=True):
                st.markdown('<div class="section-header-agenda">📋 Agenda Details</div>', unsafe_allow_html=True)
                a1, a2 = st.columns(2)
                with a1:
                    st.markdown(f"**Meeting ID:** {selected_row.get('meeting_id', 'N/A') or 'N/A'}")
                    st.markdown(f"**Date:** {selected_row.get('date', 'N/A') or 'N/A'}")
                    st.markdown(f"**Meeting Start:** {selected_row.get('meeting_start_date', 'N/A') or 'N/A'}")
                    st.markdown(f"**Sector:** {selected_row.get('sector_name', 'N/A') or 'N/A'}")
                with a2:
                    st.markdown(f"**Committee:** {selected_row.get('committee_type', 'N/A') or 'N/A'}")
                    st.markdown(f"**State:** {selected_row.get('statename_derived', 'N/A') or 'N/A'}")
                    st.markdown(f"**Meeting End:** {selected_row.get('meeting_end_date', 'N/A') or 'N/A'}")
                    st.markdown(f"**Keywords:** {selected_row.get('matched_keywords', 'N/A') or 'N/A'}")
                st.markdown(f"**Subject:** {selected_row.get('raw_subject', 'N/A') or 'N/A'}")

                link_cols = st.columns(2)
                with link_cols[0]:
                    parivesh_url = selected_row.get('pdffilepath', '')
                    if parivesh_url and pd.notna(parivesh_url):
                        st.link_button("📄 View Agenda PDF (Parivesh)", parivesh_url, use_container_width=True)
                with link_cols[1]:
                    storage_url = selected_row.get('pdf_storage_url', '')
                    if storage_url and pd.notna(storage_url):
                        st.link_button("📄 View Agenda PDF (Supabase)", storage_url, use_container_width=True)

            # ─── DETAIL: Minutes of Meeting ───
            with st.container(border=True):
                if mom:
                    st.markdown('<div class="section-header-mom">📄 Minutes of Meeting</div>', unsafe_allow_html=True)
                    m1, m2 = st.columns(2)
                    with m1:
                        st.markdown(f"**Meeting ID:** {mom.get('meeting_id', 'N/A') or 'N/A'}")
                        st.markdown(f"**Date:** {mom.get('date', 'N/A') or 'N/A'}")
                        st.markdown(f"**Meeting Start:** {mom.get('meeting_start_date', 'N/A') or 'N/A'}")
                    with m2:
                        st.markdown(f"**Committee:** {mom.get('committee_type', 'N/A') or 'N/A'}")
                        st.markdown(f"**Meeting End:** {mom.get('meeting_end_date', 'N/A') or 'N/A'}")
                    st.markdown(f"**Subject:** {mom.get('raw_subject', 'N/A') or 'N/A'}")

                    link_cols = st.columns(2)
                    with link_cols[0]:
                        mom_parivesh = mom.get('pdffilepath', '')
                        if mom_parivesh and pd.notna(mom_parivesh):
                            st.link_button("📄 View MOM PDF (Parivesh)", mom_parivesh, use_container_width=True)
                    with link_cols[1]:
                        mom_storage = mom.get('pdf_storage_url', '') or selected_row.get('mom_pdf_storage_url', '')
                        if mom_storage and pd.notna(mom_storage):
                            st.link_button("📄 View MOM PDF (Supabase)", mom_storage, use_container_width=True)
                else:
                    st.markdown('<div class="section-header-mom">📄 Minutes of Meeting</div>', unsafe_allow_html=True)
                    st.markdown("*No MOM document linked to this agenda.*")

            # ─── DETAIL: Proposals ───
            with st.container(border=True):
                st.markdown(f'<div class="section-header-proposals">📑 Proposals ({len(proposals_list)})</div>', unsafe_allow_html=True)
                if proposals_list:
                    for prop in proposals_list:
                        with st.container(border=True):
                            c1, c2 = st.columns(2)
                            with c1:
                                st.markdown(f"**Sr No:** {prop.get('sr_no', 'N/A')}")
                                st.markdown(f"**Proposal No:** {prop.get('proposal_no', 'N/A') or 'N/A'}")
                                st.markdown(f"**Project Name:** {prop.get('project_name', 'N/A') or 'N/A'}")
                                st.markdown(f"**Proposal For:** {prop.get('proposal_for', 'N/A') or 'N/A'}")
                                st.markdown(f"**Sector:** {prop.get('sector', 'N/A') or 'N/A'}")
                                st.markdown(f"**Activity:** {prop.get('activity', 'N/A') or 'N/A'}")
                            with c2:
                                st.markdown(f"**File No:** {prop.get('file_no', 'N/A') or 'N/A'}")
                                st.markdown(f"**State:** {prop.get('state', 'N/A') or 'N/A'}")
                                st.markdown(f"**District:** {prop.get('district', 'N/A') or 'N/A'}")
                                st.markdown(f"**Proponent:** {prop.get('proponent', 'N/A') or 'N/A'}")
                                st.markdown(f"**Meeting Date:** {prop.get('meeting_date', 'N/A') or 'N/A'}")
                                st.markdown(f"**Meeting ID:** {prop.get('meeting_id', 'N/A') or 'N/A'}")
                else:
                    st.info("No proposals extracted for this agenda yet.")

    except Exception as e:
        st.error("A critical error occurred in the application UI.")
        st.exception(e)
        if st.button("Reset App State"):
            st.session_state.clear()
            st.rerun()

if __name__ == "__main__":
    st.set_page_config(
        page_title="Parivesh Dashboard",
        layout="wide",
        initial_sidebar_state="collapsed"
    )
    run_parivesh()
