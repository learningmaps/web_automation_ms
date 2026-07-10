import json
import os
import psycopg2
from psycopg2.extras import RealDictCursor
from supabase import create_client, Client
from projects.dantewada_scrape.constants import SCHEMA_NAME, STORAGE_BUCKET


def get_supabase() -> Client:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    return create_client(url, key)


def get_db_connection():
    db_url = os.getenv("DATABASE_URL")
    base_url = db_url.replace(":5432/", ":6543/")
    sep = "&" if "?" in base_url else "?"
    db_url_final = f"{base_url}{sep}sslmode=require&connect_timeout=15"
    return psycopg2.connect(db_url_final)


def upload_pdf_to_storage(pdf_bytes: bytes, source_website: str, filename: str) -> str:
    supabase = get_supabase()
    try:
        supabase.storage.create_bucket(STORAGE_BUCKET, options={"public": True})
    except Exception as e:
        if "already exists" not in str(e).lower():
            print(f"  !! Warning creating bucket '{STORAGE_BUCKET}': {e}")

    storage_path = f"{source_website}/{filename}"
    supabase.storage.from_(STORAGE_BUCKET).upload(
        path=storage_path,
        file=pdf_bytes,
        file_options={"content-type": "application/pdf", "x-upsert": "true"},
    )
    return supabase.storage.from_(STORAGE_BUCKET).get_public_url(storage_path)


def upsert_pdf(source_url: str, source_website: str, title: str = None, listing_date: str = None) -> dict:
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            f"""
            INSERT INTO {SCHEMA_NAME}.processed_pdfs (source_url, source_website, title, listing_date)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (source_url) DO UPDATE
                SET title = EXCLUDED.title,
                    listing_date = EXCLUDED.listing_date
            RETURNING id, source_url, source_website, status
            """,
            (source_url, source_website, title, listing_date),
        )
        row = cur.fetchone()
        conn.commit()
        return dict(row) if row else {}
    finally:
        conn.close()


def upsert_document(pdf_id: str, extraction: dict) -> dict:
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        additional = json.dumps(extraction.get("additional_fields", {}))
        forest_types = json.dumps(extraction.get("forest_types_involved", {}))
        khasra = json.dumps(extraction.get("khasra_numbers_involved", []))
        cur.execute(
            f"""
            INSERT INTO {SCHEMA_NAME}.documents
                (pdf_id, district, date_of_issuance, location_of_incident, land_hectares,
                 village_name, source_website, notification_reference_number, authority_issuing_order,
                 purpose, project_name, applicant_name, act_mentioned,
                 forest_types_involved, khasra_numbers_involved, additional_fields)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb)
            ON CONFLICT (pdf_id) DO UPDATE SET
                district = EXCLUDED.district,
                date_of_issuance = EXCLUDED.date_of_issuance,
                location_of_incident = EXCLUDED.location_of_incident,
                land_hectares = EXCLUDED.land_hectares,
                village_name = EXCLUDED.village_name,
                source_website = EXCLUDED.source_website,
                notification_reference_number = EXCLUDED.notification_reference_number,
                authority_issuing_order = EXCLUDED.authority_issuing_order,
                purpose = EXCLUDED.purpose,
                project_name = EXCLUDED.project_name,
                applicant_name = EXCLUDED.applicant_name,
                act_mentioned = EXCLUDED.act_mentioned,
                forest_types_involved = EXCLUDED.forest_types_involved,
                khasra_numbers_involved = EXCLUDED.khasra_numbers_involved,
                additional_fields = EXCLUDED.additional_fields
            RETURNING id, pdf_id
            """,
            (
                pdf_id,
                extraction.get("district", ""),
                extraction.get("date_of_issuance", ""),
                extraction.get("location_of_incident", ""),
                extraction.get("land_hectares", ""),
                extraction.get("village_name", ""),
                extraction.get("source_website", ""),
                extraction.get("notification_reference_number", ""),
                extraction.get("authority_issuing_order", ""),
                extraction.get("purpose", ""),
                extraction.get("project_name", ""),
                extraction.get("applicant_name", ""),
                extraction.get("act_mentioned", ""),
                forest_types,
                khasra,
                additional,
            ),
        )
        row = cur.fetchone()
        conn.commit()
        return dict(row) if row else {}
    finally:
        conn.close()


def get_pending_pdfs(limit: int = 10) -> list[dict]:
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            f"""
            SELECT * FROM {SCHEMA_NAME}.processed_pdfs
            WHERE status != 'processed'
            ORDER BY discovered_at ASC
            LIMIT %s
            """,
            (limit,),
        )
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def mark_processed(pdf_id: str) -> None:
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            f"UPDATE {SCHEMA_NAME}.processed_pdfs SET status = 'processed', extracted_at = NOW() WHERE id = %s",
            (pdf_id,),
        )
        conn.commit()
    finally:
        conn.close()


def mark_failed(pdf_id: str) -> None:
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            f"UPDATE {SCHEMA_NAME}.processed_pdfs SET status = 'failed' WHERE id = %s",
            (pdf_id,),
        )
        conn.commit()
    finally:
        conn.close()


def update_storage_url(pdf_id: str, storage_url: str) -> None:
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            f"UPDATE {SCHEMA_NAME}.processed_pdfs SET storage_url = %s WHERE id = %s",
            (storage_url, pdf_id),
        )
        conn.commit()
    finally:
        conn.close()
