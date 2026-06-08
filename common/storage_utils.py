"""Shared Supabase Storage utility for uploading PDFs."""
import os
from supabase import create_client, Client


def get_supabase() -> Client:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    return create_client(url, key)


def upload_pdf_to_storage(pdf_bytes: bytes, bucket: str, storage_path: str) -> str:
    supabase = get_supabase()
    try:
        supabase.storage.create_bucket(bucket, options={"public": True})
    except Exception as e:
        if "already exists" not in str(e).lower():
            print(f"  !! Warning creating bucket '{bucket}': {e}")
    supabase.storage.from_(bucket).upload(
        path=storage_path,
        file=pdf_bytes,
        file_options={"content-type": "application/pdf", "x-upsert": "true"}
    )
    return supabase.storage.from_(bucket).get_public_url(storage_path)
