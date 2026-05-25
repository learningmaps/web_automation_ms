import streamlit as st
import pandas as pd
import os
import requests
import sys
from dotenv import load_dotenv

# Add current directory to path for local imports
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.append(current_dir)

load_dotenv()

def get_secret(key):
    try:
        return st.secrets.get(key) or os.getenv(key)
    except Exception:
        return os.getenv(key)

def run_query(query, params=None):
    from db import get_db_connection
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(query, params)
        if cur.description is None:
            return []
        columns = [desc[0] for desc in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]
    except Exception as e:
        st.error(f"Database query failed: {e}")
        return []
    finally:
        cur.close()
        conn.close()

def trigger_github_sync():
    token = get_secret("GITHUB_TOKEN")
    repo = get_secret("GITHUB_REPO")
    
    if not token or not repo:
        st.error("GitHub Credentials missing. Checksecrets.toml or environment variables.")
        return False

    url = f"https://api.github.com/repos/{repo}/actions/workflows/bdc_scrape.yml/dispatches"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
    }
    data = {"ref": "main"}
    
    try:
        resp = requests.post(url, headers=headers, json=data)
        if resp.status_code != 204:
            st.error(f"GitHub API Error: {resp.status_code} - {resp.text}")
            return False
        return True
    except Exception as e:
        st.error(f"Request failed: {e}")
        return False

def run_bdc():
    # Stable tabs layout style to prevent page/screen jumping when switching tabs
    st.markdown("""
        <style>
            [data-testid="stTabPanel"] {
                min-height: 380px;
            }
        </style>
    """, unsafe_allow_html=True)
    
    # --- HEADER, STATS & CONTROLS ---
    # Fetch stats
    stats_data = run_query("SELECT case_status, last_synced FROM bdc.cases")
    df_stats = pd.DataFrame(stats_data) if stats_data else pd.DataFrame()
    
    total_cases = len(df_stats) if not df_stats.empty else 0
    total_pending = len(df_stats[df_stats['case_status'].str.lower() == 'pending']) if not df_stats.empty else 0
    total_disposed = len(df_stats[df_stats['case_status'].str.lower() == 'disposed']) if not df_stats.empty else 0
    
    # Calculate last synced timestamp
    last_sync_str = "Never"
    if not df_stats.empty and 'last_synced' in df_stats.columns:
        max_sync = df_stats['last_synced'].max()
        if pd.notna(max_sync):
            last_sync_str = max_sync[:16].replace('T', ' ') if isinstance(max_sync, str) else max_sync.strftime("%Y-%m-%d %H:%M")
            
    top_col1, top_col2, top_col3, top_col4, top_col5 = st.columns([2, 1, 1, 1, 1.5])
    
    with top_col1:
        st.title("Bastar Court Cases")
        st.caption(f"Data Last Synced: {last_sync_str}")
        
    with top_col2:
        st.metric("Total Cases", total_cases)
        
    with top_col3:
        st.metric("Pending", total_pending)
        
    with top_col4:
        st.metric("Disposed", total_disposed)
        
    with top_col5:
        st.write("") # Alignment spacing
        st.write("")
        btn_col1, btn_col2 = st.columns(2)
        with btn_col1:
            if st.button("Sync", use_container_width=True, help="Trigger the GitHub Action scraper workflow to fetch the latest cases"):
                with st.spinner("Triggering..."):
                    if trigger_github_sync():
                        st.toast("Sync workflow triggered successfully!")
        with btn_col2:
            if st.button("Refresh", use_container_width=True, help="Reload dashboard data from the database"):
                st.rerun()
                
    st.divider()
    
    # --- DATA VIEWER & DETAILS ---
    cases_data = run_query("SELECT * FROM bdc.cases ORDER BY last_synced DESC")
    
    if not cases_data:
        st.info("No cases synced in database. Click the Sync Cases button above to start.")
        return
        
    df_cases = pd.DataFrame(cases_data)
    
    # Clean lists for display
    df_cases['petitioners_str'] = df_cases['petitioners'].apply(lambda x: ", ".join(x) if isinstance(x, list) else str(x))
    df_cases['respondents_str'] = df_cases['respondents'].apply(lambda x: ", ".join(x) if isinstance(x, list) else str(x))
    
    # --- FILTERS ---
    st.subheader("Filter Cases")
    f_col1, f_col2, f_col3 = st.columns(3)
    
    with f_col1:
        years = sorted(df_cases['case_year'].dropna().unique().tolist(), reverse=True)
        selected_years = st.multiselect("Case Year", years, default=years)
        
    with f_col2:
        statuses = sorted(df_cases['case_status'].dropna().unique().tolist())
        selected_statuses = st.multiselect("Status", statuses, default=statuses)
        
    with f_col3:
        search_query = st.text_input("Search (CNR, Petitioner, Respondent)", "").strip().lower()
        
    # Apply filters
    filtered_df = df_cases
    if selected_years:
        filtered_df = filtered_df[filtered_df['case_year'].isin(selected_years)]
    if selected_statuses:
        filtered_df = filtered_df[filtered_df['case_status'].isin(selected_statuses)]
    if search_query:
        filtered_df = filtered_df[
            filtered_df['cnr'].str.lower().str.contains(search_query) |
            filtered_df['petitioners_str'].str.lower().str.contains(search_query) |
            filtered_df['respondents_str'].str.lower().str.contains(search_query)
        ]
        
    # Display table list
    st.subheader(f"Cases List ({len(filtered_df)} matches)")
    
    display_cols = ['cnr', 'case_type', 'reg_no', 'case_year', 'case_status', 'petitioners_str', 'respondents_str', 'next_hearing']
    display_df = filtered_df[display_cols].copy()
    display_df.columns = ['CNR Number', 'Case Type', 'Reg No', 'Year', 'Status', 'Petitioner(s)', 'Respondent(s)', 'Next Hearing']
    
    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True
    )
    
    # Case Details Selector
    st.divider()
    st.subheader("Select Case for Detailed View")
    
    case_options = {row['cnr']: f"{row['cnr']} - {row['petitioners_str']} vs {row['respondents_str']}" for _, row in filtered_df.iterrows()}
    
    if not case_options:
        st.info("No cases matching the filters to view details.")
        return
        
    selected_cnr = st.selectbox(
        "Select Case",
        options=list(case_options.keys()),
        format_func=lambda x: case_options[x],
        label_visibility="collapsed"
    )
    
    if selected_cnr:
        case_info = filtered_df[filtered_df['cnr'] == selected_cnr].iloc[0]
        case_uuid = case_info['id']
        
        # 1. Main Info Panels
        detail_col1, detail_col2 = st.columns(2)
        with detail_col1:
            st.markdown(f"**CNR Number:** `{case_info['cnr']}`")
            st.markdown(f"**Case Type:** {case_info['case_type']}")
            st.markdown(f"**Filing Number:** {case_info['filing_no']} (Date: {case_info['filing_date']})")
            st.markdown(f"**Registration Number:** {case_info['reg_no']} (Date: {case_info['reg_date']})")
            st.markdown(f"**Establishment Code:** `{case_info['establishment_code']}`")
            
        with detail_col2:
            st.markdown(f"**Case Status:** `{case_info['case_status']}`")
            st.markdown(f"**Stage of Case:** *{case_info['stage']}*")
            st.markdown(f"**Court Complex:** {case_info['court_name']}")
            st.markdown(f"**Presiding Judge:** {case_info['judge']}")
            st.markdown(f"**Next Hearing Date:** {case_info['next_hearing']}")
            st.markdown(f"**First Hearing Date:** {case_info['first_hearing']}")
            
        # PDF Case Printout Button
        if case_info['page_pdf_url']:
            st.markdown(f"[Download Full Case Page PDF (A4)]({case_info['page_pdf_url']})")
            
        # 2. Tabs for Parties, Acts, FIR, History, Orders
        tab_parties, tab_acts, tab_fir, tab_history, tab_orders = st.tabs([
            "Parties & Representation", "Acts & Sections", "FIR Details", "Hearing History", "PDF Orders"
        ])
        
        with tab_parties:
            p_col1, p_col2 = st.columns(2)
            with p_col1:
                st.write("**Petitioners**")
                for pet in case_info['petitioners']:
                    st.write(f"- {pet}")
                st.write("**Advocate(s):**")
                for adv in case_info['petitioner_adv']:
                    st.write(f"- {adv}")
            with p_col2:
                st.write("**Respondents**")
                for res in case_info['respondents']:
                    st.write(f"- {res}")
                st.write("**Advocate(s):**")
                for adv in case_info['respondent_adv']:
                    st.write(f"- {adv}")
                    
        with tab_acts:
            acts_list = case_info['acts_json']
            if acts_list:
                df_acts = pd.DataFrame(acts_list)
                df_acts.columns = ['Act Name', 'Section(s)']
                st.dataframe(df_acts, height=300, use_container_width=True, hide_index=True)
            else:
                st.info("No acts or sections recorded.")
                
        with tab_fir:
            st.markdown(f"**Police Station:** {case_info['police_station']}")
            st.markdown(f"**FIR Number:** {case_info['fir_number']}")
            st.markdown(f"**FIR Year:** {case_info['fir_year']}")
            
        with tab_history:
            history_data = run_query(
                "SELECT business_date, hearing_date, purpose, judge, business_text FROM bdc.case_history WHERE case_id = %s ORDER BY business_date DESC",
                (case_uuid,)
            )
            if history_data:
                df_hist = pd.DataFrame(history_data)
                
                # Format table
                df_hist_display = df_hist[['business_date', 'hearing_date', 'purpose', 'judge', 'business_text']].copy()
                df_hist_display.columns = ['Business Date', 'Next Hearing Date', 'Purpose of Hearing', 'Presiding Judge', 'Proceedings Text']
                
                st.dataframe(df_hist_display, height=300, use_container_width=True, hide_index=True)
            else:
                st.info("No hearing history recorded.")
                
        with tab_orders:
            orders_data = run_query(
                "SELECT order_date, order_type, file_name, pdf_url FROM bdc.case_orders WHERE case_id = %s ORDER BY order_date DESC",
                (case_uuid,)
            )
            if orders_data:
                df_orders = pd.DataFrame(orders_data)
                df_orders_display = df_orders[['order_date', 'order_type', 'file_name', 'pdf_url']].copy()
                df_orders_display.columns = ['Order Date', 'Type', 'File Name', 'Download Link']
                
                st.dataframe(
                    df_orders_display,
                    height=300,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "Download Link": st.column_config.LinkColumn("Download Link")
                    }
                )
            else:
                st.info("No order PDF documents uploaded for this case.")

if __name__ == "__main__":
    run_bdc()
