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

    tab_pdfs, tab_docs = st.tabs(["Scraped PDFs", "Extracted Documents"])

    with tab_pdfs:
        st.subheader("Scraped PDFs")
        try:
            df = load_pdfs()
        except Exception as e:
            st.error(f"Failed to load data: {e}")
            df = pd.DataFrame()

        if df.empty:
            st.info("No PDFs discovered yet. Run the scraper first.")
        else:
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Total", len(df))
            col2.metric("Processed", len(df[df["status"] == "processed"]))
            col3.metric("Pending", len(df[df["status"] == "pending"]))
            col4.metric("Failed", len(df[df["status"] == "failed"]))

            filter_source = st.selectbox("Filter by source", ["All", "dantewada", "forest_cg"], key="pdf_source")
            if filter_source != "All":
                df = df[df["source_website"] == filter_source]

            display_cols = ["title", "source_website", "listing_date", "status", "source_url"]
            st.dataframe(
                df[display_cols],
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

    with tab_docs:
        st.subheader("Extracted Document Data")
        try:
            df = load_documents()
        except Exception as e:
            st.error(f"Failed to load data: {e}")
            df = pd.DataFrame()

        if df.empty:
            st.info("No documents extracted yet.")
        else:
            col1, col2 = st.columns(2)
            col1.metric("Total Documents", len(df))
            col2.metric("Sources", df["source_website"].nunique())

            filter_source = st.selectbox("Filter by source", ["All", "dantewada", "forest_cg"], key="doc_source")
            if filter_source != "All":
                df = df[df["source_website"] == filter_source]

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
                if col in df.columns:
                    df[col] = df[col].apply(fmt).apply(
                        lambda x: "" if not x else (", ".join(x) if isinstance(x, list) else x)
                    )

            st.dataframe(
                df[display_cols],
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

    st.markdown("---")
    st.caption("Source: dantewada.nic.in & forest.cg.gov.in | Extraction via Gemini Vision")
