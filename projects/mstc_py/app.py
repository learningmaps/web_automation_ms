import streamlit as st
import pandas as pd
from supabase import create_client, Client
import os
import sys
import requests
from dotenv import load_dotenv

# Add current directory to path for local imports
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.append(current_dir)

load_dotenv()

st.set_page_config(page_title="MSTC Mineral Block Dashboard", layout="wide")

def get_secret(key):
    """Helper to get secret from st.secrets or environment variable."""
    try:
        return st.secrets.get(key) or os.getenv(key)
    except Exception:
        return os.getenv(key)

@st.cache_resource
def get_supabase() -> Client:
    # On Streamlit Cloud, these come from st.secrets
    url = get_secret("SUPABASE_URL")
    key = get_secret("SUPABASE_KEY")
    if not url or not key:
        st.error("Supabase credentials missing. Check .env or .streamlit/secrets.toml")
        st.stop()
    return create_client(url, key)

from scraper import scrape_links

# ... (keep get_secret and get_supabase)

def trigger_github_extraction(limit=10):
    token = get_secret("GITHUB_TOKEN")
    repo = get_secret("GITHUB_REPO")
    
    if not token or not repo:
        st.error(f"GitHub Credentials missing. Token: {'Set' if token else 'Missing'}, Repo: {repo if repo else 'Missing'}")
        return False

    url = f"https://api.github.com/repos/{repo}/actions/workflows/extract_pdfs.yml/dispatches"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
    }
    # Explicitly set task to 'extract'
    data = {"ref": "main", "inputs": {"task": "extract", "limit": str(limit)}}
    
    try:
        resp = requests.post(url, headers=headers, json=data)
        if resp.status_code != 204:
            st.error(f"GitHub API Error: {resp.status_code} - {resp.text}")
            return False
        return True
    except Exception as e:
        st.error(f"Request failed: {e}")
        return False

supabase = get_supabase()

# --- HEADER, STATS & CONTROLS ---
# Fetch stats first to display them
stats = supabase.table("processed_pdfs").select("status", count="exact").execute()
df_stats = pd.DataFrame(stats.data)
total_pending = len(df_stats[df_stats['status'] == 'pending']) if not df_stats.empty else 0
total_processed = len(df_stats[df_stats['status'] == 'processed']) if not df_stats.empty else 0
total_failed = len(df_stats[df_stats['status'] == 'failed']) if not df_stats.empty else 0

top_col1, top_col2, top_col3, top_col4, top_col5 = st.columns([2, 1, 1, 1, 1.5])

with top_col1:
    st.title("MSTC Automation")

with top_col2:
    st.metric("Pending", total_pending)

with top_col3:
    st.metric("Processed", total_processed)

with top_col4:
    st.metric("Failed", total_failed)

with top_col5:
    batch_limit = st.slider("Limit", 1, 50, 10, label_visibility="collapsed")
    
    c1, c2 = st.columns(2)
    with c1:
        if st.button("Fetch", width="stretch", help="Fetch New PDF Links"):
            progress_bar = st.progress(0)
            status_text = st.empty()
            def update_progress(current, total, message):
                progress_bar.progress(current / total)
                status_text.text(message)
            try:
                scrape_links(progress_callback=update_progress)
                st.success("Done!")
                st.rerun()
            except Exception as e:
                st.error(f"Error: {e}")
    with c2:
        if st.button("Extract", width="stretch", help="Extract Data from PDFs"):
            if trigger_github_extraction(batch_limit):
                st.success("Triggered!")
            else:
                st.error("Failed")

st.divider()

# --- DATA VIEWERS ---
tab1, tab2, tab3 = st.tabs(["Scraped URLs", "Mine Block Summaries", "Tenders (NIT)"])

def format_dates(df, date_cols):
    """Utility to format date columns to YYYY-MM-DD HH:MM."""
    for col in date_cols:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col]).dt.strftime('%Y-%m-%d %H:%M')
    return df

with tab1:
    st.subheader("All Scraped PDF URLs")
    urls_resp = supabase.table("processed_pdfs").select("*").order("discovered_at", desc=True).execute()
    if urls_resp.data:
        df_urls = pd.DataFrame(urls_resp.data)
        df_urls = format_dates(df_urls, ['discovered_at', 'extracted_at'])
        # Filter and reorder columns as requested
        cols = ['discovered_at', 'source_page', 'file_id', 'pdf_url', 'status']
        df_urls = df_urls[cols]
        st.dataframe(
            df_urls, 
            width="stretch",
            column_config={
                "pdf_url": st.column_config.LinkColumn("PDF Link")
            }
        )
    else:
        st.info("No URLs scraped yet.")

with tab2:
    st.subheader("Extracted Block Details")
    # Joining with processed_pdfs to get discovered_at, file_id, and pdf_url
    blocks_resp = supabase.table("mine_block_summaries").select("*, processed_pdfs(discovered_at, file_id, pdf_url)").execute()
    if blocks_resp.data:
        df_blocks = pd.DataFrame(blocks_resp.data)
        
        # Flatten the nested processed_pdfs column
        if 'processed_pdfs' in df_blocks.columns:
            nested_df = pd.json_normalize(df_blocks['processed_pdfs'])
            df_blocks = pd.concat([df_blocks.drop(columns=['processed_pdfs']), nested_df], axis=1)
        
        # --- FILTERS ---
        f_col1, f_col2, f_col3 = st.columns(3)
        
        with f_col1:
            # Date range filter
            dates = pd.to_datetime(df_blocks['discovered_at'])
            min_date, max_date = dates.min().date(), dates.max().date()
            date_range = st.date_input("Discovered Range", [min_date, max_date])
            
        with f_col2:
            states = sorted(df_blocks['state'].dropna().unique().tolist())
            selected_states = st.multiselect("State", states, placeholder="All States")
            
        with f_col3:
            districts = sorted(df_blocks['district'].dropna().unique().tolist())
            selected_districts = st.multiselect("District", districts, placeholder="All Districts")

        # Apply Filters
        mask = pd.Series([True] * len(df_blocks))
        
        if selected_states:
            mask &= df_blocks['state'].isin(selected_states)
        
        if selected_districts:
            mask &= df_blocks['district'].isin(selected_districts)
            
        if len(date_range) == 2:
            start_date, end_date = date_range
            mask &= (pd.to_datetime(df_blocks['discovered_at']).dt.date >= start_date) & \
                    (pd.to_datetime(df_blocks['discovered_at']).dt.date <= end_date)
        
        df_filtered = df_blocks[mask].copy()

        # Format and Clean for Display
        df_filtered = format_dates(df_filtered, ['discovered_at'])

        # Reorder to show requested columns first and drop internal IDs
        req_cols = ['discovered_at', 'file_id', 'pdf_url']
        drop_cols = ['id', 'pdf_id']
        other_cols = [c for c in df_filtered.columns if c not in req_cols and c not in drop_cols]
        df_filtered = df_filtered[req_cols + other_cols]
        
        st.dataframe(
            df_filtered, 
            use_container_width=True,
            column_config={
                "pdf_url": st.column_config.LinkColumn("PDF Link")
            }
        )
    else:
        st.info("No block data extracted yet.")

with tab3:
    st.subheader("NIT / Tender Details")
    nit_resp = supabase.table("tenders_nit").select("*").execute()
    if nit_resp.data:
        df_nit = pd.DataFrame(nit_resp.data)
        df_nit = format_dates(df_nit, ['tender_date', 'bid_submission_deadline'])
        # Also clean up IDs in this table for consistency
        cols_to_show = [c for c in df_nit.columns if c not in ['id', 'pdf_id']]
        st.dataframe(df_nit[cols_to_show], use_container_width=True)
    else:
        st.info("No tender data extracted yet.")

st.divider()
st.caption("Data is synced live from Supabase. Backend runs on GitHub Actions.")
