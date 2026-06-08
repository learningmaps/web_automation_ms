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

PAGE_SIZE = 25

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
               is_processed, processed_on, subject AS raw_subject
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
               meeting_start_date, meeting_end_date
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

def load_base_metrics():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM parivesh.agenda_v3 WHERE is_processed = 0 AND ref_type = 'AGENDA' AND matched_keywords IS NOT NULL")
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

        # Add a key that lets us detect filter changes for pagination reset
        all_states = filter_opts['state']
        all_sectors = filter_opts['sector']
        all_prop_for = filter_opts['proposal_for']
        all_districts = filter_opts['district']
        all_committees = sorted(agendas_df['committee_type'].dropna().unique().tolist())

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

        # ─── PROPOSAL FILTERS ───
        st.markdown("### Proposal Filters")
        pf1, pf2, pf3, pf4 = st.columns(4)
        with pf1:
            cg = next((s for s in all_states if s.upper() == 'CHHATTISGARH'), None)
            default_state = [cg] if cg else []
            sel_state = st.multiselect("State", options=all_states, default=default_state, key="pr_state")
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

        # Proposal filter flags (for resetting page and checking if active)
        prop_filters_active = bool(sel_state) or bool(sel_sector) or bool(sel_prop_for) or bool(sel_district) or bool(proponent_search) or bool(proposal_search)

        # ─── PAGINATION ───
        total_filtered = len(filtered_agendas)
        total_pages = max(1, (total_filtered + PAGE_SIZE - 1) // PAGE_SIZE)

        if 'page' not in st.session_state:
            st.session_state.page = 1
        if st.session_state.page > total_pages:
            st.session_state.page = total_pages

        current_page = st.session_state.page
        start_idx = (current_page - 1) * PAGE_SIZE
        end_idx = start_idx + PAGE_SIZE
        page_agendas = filtered_agendas.iloc[start_idx:end_idx]

        # ─── METRICS ───
        base_metrics = load_base_metrics()
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Agendas", total_filtered)
        m2.metric("With MOM", len(filtered_agendas[filtered_agendas['norm_subject'].isin(mom_subjects)]))
        m3.metric("Unprocessed", base_metrics["unprocessed"])
        m4.metric("Keyword Matches", base_metrics["keyword_matches"])

        # ─── LOAD CURRENT PAGE DATA (proposals + MOMs only for this page) ───
        page_ids = page_agendas['id'].tolist()
        page_norm_subjects = page_agendas['norm_subject'].dropna().tolist()

        with st.spinner("Loading proposals for this page..."):
            proposals_df = load_proposals(page_ids)
        with st.spinner("Loading MOM documents for this page..."):
            moms_df = load_moms(page_norm_subjects)

        proposals_by_agenda = {}
        if not proposals_df.empty:
            for _, row in proposals_df.iterrows():
                proposals_by_agenda.setdefault(row['agenda_id'], []).append(row.to_dict())

        moms_by_subject = {}
        if not moms_df.empty:
            for _, row in moms_df.iterrows():
                moms_by_subject[row['norm_subject']] = row.to_dict()

        # ─── PAGE NAVIGATION ───
        st.markdown(f"### Agendas & Proposals (page {current_page} of {total_pages})")

        nav = st.columns([1, 2, 1, 1, 1, 2, 1])
        with nav[0]:
            if st.button("◀ First", disabled=(current_page <= 1), use_container_width=True):
                st.session_state.page = 1
                st.rerun()
        with nav[1]:
            if st.button("◀ Previous", disabled=(current_page <= 1), use_container_width=True):
                st.session_state.page = current_page - 1
                st.rerun()
        with nav[2]:
            st.write(f"Page **{current_page}**")
        with nav[3]:
            dummy = st.number_input("Go to", min_value=1, max_value=total_pages, value=current_page, label_visibility="collapsed", key="page_jump")
            if dummy != current_page:
                st.session_state.page = dummy
                st.rerun()
        with nav[4]:
            st.write(f"of **{total_pages}**")
        with nav[5]:
            if st.button("Next ▶", disabled=(current_page >= total_pages), use_container_width=True):
                st.session_state.page = current_page + 1
                st.rerun()
        with nav[6]:
            if st.button("Last ▶", disabled=(current_page >= total_pages), use_container_width=True):
                st.session_state.page = total_pages
                st.rerun()

        # ─── RENDER AGENDA CARDS ───
        cards_shown = 0
        for _, agenda in page_agendas.iterrows():
            aid = agenda['id']
            agenda_proposals = proposals_by_agenda.get(aid, [])

            filtered_props = agenda_proposals
            if sel_state:
                filtered_props = [p for p in filtered_props if p.get('state') in sel_state]
            if sel_sector:
                filtered_props = [p for p in filtered_props if p.get('sector') in sel_sector]
            if sel_prop_for:
                filtered_props = [p for p in filtered_props if p.get('proposal_for') in sel_prop_for]
            if sel_district:
                filtered_props = [p for p in filtered_props if p.get('district') in sel_district]
            if proponent_search:
                q = proponent_search.lower()
                filtered_props = [p for p in filtered_props if q in (p.get('proponent', '') or '').lower()]
            if proposal_search:
                q = proposal_search.lower()
                filtered_props = [p for p in filtered_props if q in (p.get('proposal_no', '') or '').lower()]

            if prop_filters_active and not filtered_props:
                continue

            cards_shown += 1

            mom = moms_by_subject.get(agenda['norm_subject'])
            mom_badge = ""
            if mom:
                mom_badge = '<span class="mom-badge">✓ MOM</span>'
            else:
                mom_badge = '<span class="no-mom-badge">No MOM</span>'

            meeting_date = agenda.get('date', '?') or '?'
            committee = agenda.get('committee_type', '?') or '?'
            subject_display = agenda.get('raw_subject', '') or '(no subject)'
            if len(subject_display) > 100:
                subject_display = subject_display[:100] + "..."

            n_props = len(filtered_props)
            expanded = n_props <= 3
            label = f"{meeting_date} · {committee} · {n_props} proposal(s) · {agenda.get('statename_derived', '') or ''}"

            with st.expander(label, expanded=expanded):
                meta_cols = st.columns([2, 1, 1])
                with meta_cols[0]:
                    st.markdown(f"**Meeting ID:** {agenda.get('meeting_id', 'N/A') or 'N/A'}")
                    st.markdown(f"**Subject:** {subject_display}")
                    if agenda.get('sector_name'):
                        st.markdown(f"**Sector:** {agenda['sector_name']}")
                with meta_cols[1]:
                    pdf_url = agenda.get('pdffilepath', '')
                    if pdf_url and pd.notna(pdf_url):
                        st.link_button("📄 View Agenda PDF", pdf_url, use_container_width=True)
                with meta_cols[2]:
                    st.markdown(mom_badge, unsafe_allow_html=True)
                    if mom:
                        mom_pdf = mom.get('pdffilepath', '')
                        if mom_pdf and pd.notna(mom_pdf):
                            st.link_button("📄 View MOM PDF", mom_pdf, use_container_width=True)

                st.markdown("---")
                if mom:
                    st.markdown("##### Minutes of Meeting")
                    mom_cols = st.columns(3)
                    with mom_cols[0]:
                        st.markdown(f"**Date:** {mom.get('date', 'N/A') or 'N/A'}")
                    with mom_cols[1]:
                        st.markdown(f"**Meeting ID:** {mom.get('meeting_id', 'N/A') or 'N/A'}")
                    with mom_cols[2]:
                        mom_subj = mom.get('raw_subject', '') or '(no subject)'
                        st.markdown(f"**Subject:** {mom_subj[:80]}{'...' if len(mom_subj) > 80 else ''}")
                else:
                    st.markdown("*No MOM document linked to this agenda.*")

                st.markdown("---")
                st.markdown(f"##### Proposals ({n_props})")
                if filtered_props:
                    props_df = pd.DataFrame(filtered_props)
                    display_cols = ['sr_no', 'proposal_no', 'project_name', 'proposal_for',
                                    'sector', 'state', 'district', 'proponent']
                    existing_cols = [c for c in display_cols if c in props_df.columns]
                    st.dataframe(
                        props_df[existing_cols],
                        use_container_width=True,
                        hide_index=True,
                        column_config={
                            "sr_no": st.column_config.NumberColumn("S.No", width="small"),
                            "proposal_no": st.column_config.TextColumn("Proposal No", width="medium"),
                            "project_name": st.column_config.TextColumn("Project Name", width="large"),
                            "proposal_for": st.column_config.TextColumn("Proposal For", width="medium"),
                            "sector": st.column_config.TextColumn("Sector", width="small"),
                            "state": st.column_config.TextColumn("State", width="small"),
                            "district": st.column_config.TextColumn("District", width="small"),
                            "proponent": st.column_config.TextColumn("Proponent", width="medium"),
                        }
                    )
                else:
                    st.info("No proposals extracted for this agenda yet.")

        if cards_shown == 0:
            st.info("No agendas match the current filters on this page.")

        # ─── BOTTOM PAGINATION ───
        st.markdown("---")
        bnav = st.columns([1, 2, 2, 1])
        with bnav[0]:
            if st.button("◀ First", disabled=(current_page <= 1), use_container_width=True, key="bfirst"):
                st.session_state.page = 1
                st.rerun()
        with bnav[1]:
            if st.button("◀ Previous", disabled=(current_page <= 1), use_container_width=True, key="bprev"):
                st.session_state.page = current_page - 1
                st.rerun()
        with bnav[2]:
            if st.button("Next ▶", disabled=(current_page >= total_pages), use_container_width=True, key="bnext"):
                st.session_state.page = current_page + 1
                st.rerun()
        with bnav[3]:
            if st.button("Last ▶", disabled=(current_page >= total_pages), use_container_width=True, key="blast"):
                st.session_state.page = total_pages
                st.rerun()

        # ─── EXPORT ───
        st.markdown("---")
        output = io.BytesIO()
        export_df = filtered_agendas.copy()
        export_df['has_mom'] = export_df['norm_subject'].isin(mom_subjects)
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            export_df.to_excel(writer, index=False, sheet_name='Agendas')
            workbook = writer.book
            worksheet = writer.sheets['Agendas']
            header_fmt = workbook.add_format({
                'bold': True, 'text_wrap': False, 'valign': 'vcenter',
                'fg_color': '#1F4E78', 'font_color': 'white', 'border': 1
            })
            cell_fmt = workbook.add_format({'valign': 'top', 'text_wrap': False, 'border': 1})
            worksheet.set_default_row(20)
            worksheet.freeze_panes(1, 0)
            worksheet.autofilter(0, 0, len(export_df), len(export_df.columns) - 1)
            for col_num, value in enumerate(export_df.columns.values):
                worksheet.write(0, col_num, value, header_fmt)
                width = 40 if value in ['raw_subject', 'norm_subject'] else 20
                worksheet.set_column(col_num, col_num, width, cell_fmt)

        st.download_button(
            label="Download All Filtered Agendas as Excel",
            data=output.getvalue(),
            file_name=f"parivesh_agendas_{int(time.time())}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )

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
