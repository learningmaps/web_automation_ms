import streamlit as st
import os
import sys
import pandas as pd

sys.path.append(os.path.join(os.getcwd(), "projects"))

from projects.dantewada_scrape.db import get_pending_pdfs

SCHEMA = "diversions_and_notifications"


def _get_db():
    import psycopg2
    db_url = os.getenv("DATABASE_URL")
    base_url = db_url.replace(":5432/", ":6543/")
    sep = "&" if "?" in base_url else "?"
    return psycopg2.connect(f"{base_url}{sep}sslmode=require&connect_timeout=15")


def load_pdfs():
    conn = _get_db()
    df = pd.read_sql(f"SELECT * FROM {SCHEMA}.processed_pdfs ORDER BY discovered_at DESC", conn)
    conn.close()
    return df


def load_documents():
    conn = _get_db()
    df = pd.read_sql(
        f"""
        SELECT d.*, p.title, p.listing_date, p.source_url, p.storage_url
        FROM {SCHEMA}.documents d
        JOIN {SCHEMA}.processed_pdfs p ON d.pdf_id = p.id
        ORDER BY d.created_on DESC
        """,
        conn,
    )
    conn.close()
    return df


def run_dantewada():
    st.title("Dantewada & Forest CG Notifications")
    st.caption("PDF scraping and extraction from government notification portals")

    tab_docs, tab_pdfs = st.tabs(["Extracted Documents", "Scraped PDFs"])

    # Helper functions for multi-value filtering and math
    import re
    def get_unique_items(series):
        items = set()
        for val in series.dropna():
            for item in str(val).split(","):
                cleaned = item.strip()
                if cleaned:
                    items.add(cleaned)
        return sorted(list(items))

    def filter_by_multivalue(df, column, selected):
        if not selected:
            return df
        def matches(val):
            if not val:
                return False
            parts = {p.strip().lower() for p in str(val).split(",")}
            return any(s.lower() in parts for s in selected)
        return df[df[column].apply(matches)]

    def extract_numeric_ha(val):
        if not val:
            return 0.0
        match = re.search(r"(\d+\.?\d*)", str(val))
        return float(match.group(1)) if match else 0.0

    with tab_docs:
        st.subheader("Extracted Document Data")
        try:
            df_docs = load_documents()
        except Exception as e:
            st.error(f"Failed to load data: {e}")
            df_docs = pd.DataFrame()

        if df_docs.empty:
            st.info("No documents extracted yet.")
        else:
            # Initialize session states for filters if not present
            if "sel_sources" not in st.session_state:
                st.session_state["sel_sources"] = sorted(df_docs["source_website"].dropna().unique().tolist()) if "source_website" in df_docs.columns else []
            if "sel_districts" not in st.session_state:
                st.session_state["sel_districts"] = []
            if "sel_villages" not in st.session_state:
                st.session_state["sel_villages"] = []

            # Retrieve active filter selections from state
            active_sources = st.session_state["sel_sources"]
            active_districts = st.session_state["sel_districts"]
            active_villages = st.session_state["sel_villages"]

            # Compute Source Options based on District and Village filters
            df_for_sources = df_docs.copy()
            if active_districts:
                df_for_sources = filter_by_multivalue(df_for_sources, "district", active_districts)
            if active_villages:
                df_for_sources = filter_by_multivalue(df_for_sources, "village_name", active_villages)
            sources_options = sorted(df_for_sources["source_website"].dropna().unique().tolist()) if "source_website" in df_for_sources.columns else []

            # Compute District Options based on Source and Village filters
            df_for_districts = df_docs.copy()
            if active_sources:
                df_for_districts = df_for_districts[df_for_districts["source_website"].isin(active_sources)]
            if active_villages:
                df_for_districts = filter_by_multivalue(df_for_districts, "village_name", active_villages)
            districts_options = get_unique_items(df_for_districts["district"]) if "district" in df_for_districts.columns else []

            # Compute Village Options based on Source and District filters
            df_for_villages = df_docs.copy()
            if active_sources:
                df_for_villages = df_for_villages[df_for_villages["source_website"].isin(active_sources)]
            if active_districts:
                df_for_villages = filter_by_multivalue(df_for_villages, "district", active_districts)
            villages_options = get_unique_items(df_for_villages["village_name"]) if "village_name" in df_for_villages.columns else []

            # Filter layout
            with st.expander("Filter Extracted Documents", expanded=True):
                col_f1, col_f2, col_f3 = st.columns(3)
                
                # Safeguard defaults to ensure selected items exist within the computed option sets
                default_sources = [s for s in active_sources if s in sources_options]
                selected_sources = col_f1.multiselect("Sources", options=sources_options, default=default_sources, key="sel_sources")
                
                default_districts = [d for d in active_districts if d in districts_options]
                selected_districts = col_f2.multiselect("Districts", options=districts_options, default=default_districts, key="sel_districts")
                
                default_villages = [v for v in active_villages if v in villages_options]
                selected_villages = col_f3.multiselect("Villages", options=villages_options, default=default_villages, key="sel_villages")

            # Final filtered dataset applying all filters
            filtered_docs = df_docs.copy()
            if selected_sources:
                filtered_docs = filtered_docs[filtered_docs["source_website"].isin(selected_sources)]
            if selected_districts:
                filtered_docs = filter_by_multivalue(filtered_docs, "district", selected_districts)
            if selected_villages:
                filtered_docs = filter_by_multivalue(filtered_docs, "village_name", selected_villages)

            # Calculate and display metrics
            total_docs = len(filtered_docs)
            total_ha = filtered_docs["land_hectares"].apply(extract_numeric_ha).sum() if "land_hectares" in filtered_docs.columns else 0.0
            num_districts = len(get_unique_items(filtered_docs["district"])) if "district" in filtered_docs.columns else 0
            num_villages = len(get_unique_items(filtered_docs["village_name"])) if "village_name" in filtered_docs.columns else 0

            col_m1, col_m2, col_m3, col_m4 = st.columns(4)
            col_m1.metric("Total Documents", total_docs)
            col_m2.metric("Total Area (Hectares)", f"{total_ha:.2f} ha")
            col_m3.metric("Districts Involved", num_districts)
            col_m4.metric("Villages Involved", num_villages)

            display_cols = [
                "title", "source_website", "district", "date_of_issuance",
                "village_name", "location_of_incident", "land_hectares",
                "notification_reference_number", "authority_issuing_order",
                "purpose", "project_name", "applicant_name", "act_mentioned",
                "forest_types_involved", "khasra_numbers_involved",
                "storage_url",
            ]

            fmt = lambda x: "" if x in (None, "") else x
            for col in ["forest_types_involved", "khasra_numbers_involved", "additional_fields"]:
                if col in filtered_docs.columns:
                    filtered_docs[col] = filtered_docs[col].apply(fmt).apply(
                        lambda x: "" if not x else (", ".join(x) if isinstance(x, list) else x)
                    )

            st.dataframe(
                filtered_docs[display_cols],
                use_container_width=True,
                column_config={
                    "title": st.column_config.TextColumn("Title", width="large"),
                    "source_website": st.column_config.TextColumn("Source"),
                    "district": st.column_config.TextColumn("District"),
                    "date_of_issuance": st.column_config.TextColumn("Date"),
                    "village_name": st.column_config.TextColumn("Village"),
                    "location_of_incident": st.column_config.TextColumn("Location"),
                    "land_hectares": st.column_config.TextColumn("Land (ha)"),
                    "notification_reference_number": st.column_config.TextColumn("Ref #"),
                    "authority_issuing_order": st.column_config.TextColumn("Authority"),
                    "purpose": st.column_config.TextColumn("Purpose"),
                    "project_name": st.column_config.TextColumn("Project"),
                    "applicant_name": st.column_config.TextColumn("Applicant"),
                    "act_mentioned": st.column_config.TextColumn("Act"),
                    "forest_types_involved": st.column_config.TextColumn("Forest Types"),
                    "khasra_numbers_involved": st.column_config.TextColumn("Khasra #"),
                    "storage_url": st.column_config.LinkColumn("PDF"),
                },
                height=500,
            )

    with tab_pdfs:
        st.subheader("Scraped PDFs")
        try:
            df_pdfs = load_pdfs()
        except Exception as e:
            st.error(f"Failed to load data: {e}")
            df_pdfs = pd.DataFrame()

        if df_pdfs.empty:
            st.info("No PDFs discovered yet. Run the scraper first.")
        else:
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Total", len(df_pdfs))
            col2.metric("Processed", len(df_pdfs[df_pdfs["status"] == "processed"]))
            col3.metric("Pending", len(df_pdfs[df_pdfs["status"] == "pending"]))
            col4.metric("Failed", len(df_pdfs[df_pdfs["status"] == "failed"]))

            filter_source = st.selectbox("Filter by source", ["All", "dantewada", "forest_cg"], key="pdf_source")
            if filter_source != "All":
                df_pdfs = df_pdfs[df_pdfs["source_website"] == filter_source]

            display_cols = ["title", "source_website", "listing_date", "status", "source_url"]
            st.dataframe(
                df_pdfs[display_cols],
                use_container_width=True,
                column_config={
                    "title": st.column_config.TextColumn("Title", width="large"),
                    "source_website": st.column_config.TextColumn("Source"),
                    "listing_date": st.column_config.TextColumn("Date"),
                    "status": st.column_config.TextColumn("Status"),
                    "source_url": st.column_config.LinkColumn("PDF Link"),
                },
                height=500,
            )

    st.markdown("---")
    st.caption("Source: dantewada.nic.in & forest.cg.gov.in | Extraction via Gemini Vision")
