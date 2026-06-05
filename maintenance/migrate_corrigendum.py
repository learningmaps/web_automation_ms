"""
One-time migration: delete old corrigendum data + S3, reset PDFs,
and print DDL for schema migration.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'projects'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

from supabase import create_client


def main():
    supabase = create_client(
        os.getenv("SUPABASE_URL"),
        os.getenv("SUPABASE_KEY"),
    )

    # 1. Find and delete S3 objects
    pdfs = supabase.schema("mstc").table("processed_pdfs") \
        .select("id, file_id, storage_url") \
        .eq("source_page", "corrigendum_addendum") \
        .not_.is_("storage_url", "null") \
        .execute()
    print(f"Found {len(pdfs.data)} corrigendum PDFs with storage URLs")

    if pdfs.data:
        paths = [
            f"critical_minerals/corrigendum_addendum/chhattisgarh/{p['file_id']}.pdf"
            for p in pdfs.data if p.get('file_id')
        ]
        if paths:
            print(f"Deleting {len(paths)} S3 objects...")
            try:
                supabase.storage.from_("mstc-pdfs").remove(paths)
                print("  Deleted from storage")
            except Exception as e:
                print(f"  Storage delete error (non-fatal): {e}")

    # 2. Delete existing corrigendum rows
    existing = supabase.schema("mstc").table("corrigendum_addendum").select("id").execute()
    print(f"Found {len(existing.data)} corrigendum rows to delete")
    if existing.data:
        supabase.schema("mstc").table("corrigendum_addendum").delete().neq("id", "00000000-0000-0000-0000-000000000000").execute()
        print("  Deleted")

    # 3. Reset PDFs for re-extraction
    supabase.schema("mstc").table("processed_pdfs") \
        .update({"status": "pending", "extracted_at": None, "storage_url": None}) \
        .eq("source_page", "corrigendum_addendum") \
        .execute()
    print("Reset PDFs to pending")

    # 4. Print DDL for manual execution
    print("""
Migration data step complete. Now run this in the Supabase SQL editor:

-- Create child table
CREATE TABLE IF NOT EXISTS mstc.corrigendum_blocks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    corrigendum_id UUID NOT NULL REFERENCES mstc.corrigendum_addendum(id) ON DELETE CASCADE,
    block_name TEXT NOT NULL,
    state TEXT,
    district TEXT,
    change_summary TEXT
);
ALTER TABLE mstc.corrigendum_blocks ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Public Read Access" ON mstc.corrigendum_blocks FOR SELECT USING (true);

-- Drop old columns from parent
ALTER TABLE mstc.corrigendum_addendum
    DROP COLUMN IF EXISTS block_name,
    DROP COLUMN IF EXISTS state,
    DROP COLUMN IF EXISTS district;

Then run: python maintenance/reprocess_corrigendum.py
""")


if __name__ == "__main__":
    main()
