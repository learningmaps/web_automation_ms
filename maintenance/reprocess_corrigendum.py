"""
Re-process all corrigendum PDFs with the new multi-block prompt.
Run AFTER the SQL migration (create corrigendum_blocks, drop old columns).
"""
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))  # project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'projects'))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

import requests
from datetime import datetime
from supabase import create_client

from mstc_py.extractor import safe_extract
from mstc_py.schemas import CorrigendumAddendum, PAGE_SCHEMA_MAP
from mstc_py.main import fuzzy_match_block, upload_pdf_to_storage, normalize_timestamp


def main():
    supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

    # Fetch all pending corrigendum PDFs
    pdfs = supabase.schema("mstc").table("processed_pdfs") \
        .select("*") \
        .eq("source_page", "corrigendum_addendum") \
        .eq("status", "pending") \
        .order("id") \
        .execute()
    print(f"Found {len(pdfs.data)} pending corrigendum PDFs")

    blocks_cache = supabase.schema("mstc").table("mine_block_summaries").select("block_name, state, district").execute()

    for i, pdf in enumerate(pdfs.data, 1):
        file_id = pdf['file_id']
        pdf_url = pdf['pdf_url']
        print(f"\n[{i}/{len(pdfs.data)}] Processing {file_id}...")

        try:
            # 1. Download
            headers = {'User-Agent': 'Mozilla/5.0'}
            pdf_resp = requests.get(pdf_url, headers=headers, timeout=30)
            pdf_resp.raise_for_status()
            pdf_bytes = pdf_resp.content

            # 2. Extract (use config from schemas: model=Pydantic class, prompt=string)
            config = PAGE_SCHEMA_MAP['Corrigendum and Addendum']
            extracted = safe_extract(pdf_bytes, config['model'], config['prompt'])
            if not extracted:
                raise ValueError("Extraction returned None")

            print(f"  -> Extracted {len(extracted.blocks)} block(s)")

            # 3. Fuzzy match each block
            for block in extracted.blocks:
                match = fuzzy_match_block(block.blockName, blocks_cache.data)
                if match:
                    block.state = match['state'] or block.state
                    block.district = match['district'] or block.district

            # 4. Upsert parent
            parent_resp = supabase.schema("mstc").table("corrigendum_addendum").upsert({
                "pdf_id": pdf['id'],
                "document_date": normalize_timestamp(extracted.documentDate),
                "summary": extracted.summary,
            }, on_conflict="pdf_id").execute()
            parent_id = parent_resp.data[0]['id']

            # 5. Insert child blocks
            if extracted.blocks:
                blocks_to_insert = [{
                    "corrigendum_id": parent_id,
                    "block_name": b.blockName,
                    "state": b.state,
                    "district": b.district,
                    "change_summary": b.changeSummary,
                } for b in extracted.blocks]
                supabase.schema("mstc").table("corrigendum_blocks").insert(blocks_to_insert).execute()

            # 6. Upload to S3 if any block is Chhattisgarh
            is_cg = any(b.state and "chhattisgarh" in b.state.lower() for b in extracted.blocks)
            if is_cg:
                s3_path = f"critical_minerals/corrigendum_addendum/chhattisgarh/{file_id}.pdf"
                existing = supabase.schema("mstc").table("processed_pdfs") \
                    .select("storage_url").eq("id", pdf['id']).execute()
                if existing.data and existing.data[0].get('storage_url'):
                    print(f"  -> Already in storage: {existing.data[0]['storage_url']}")
                else:
                    storage_url = upload_pdf_to_storage(pdf_bytes, s3_path)
                    supabase.schema("mstc").table("processed_pdfs").update({
                        "storage_url": storage_url
                    }).eq("id", pdf['id']).execute()
                    print(f"  -> Uploaded to storage: {storage_url}")

            # 7. Mark as processed
            supabase.schema("mstc").table("processed_pdfs").update({
                "status": "processed",
                "extracted_at": datetime.now().isoformat()
            }).eq("id", pdf['id']).execute()
            print(f"  -> Success.")

            # Gap
            if i < len(pdfs.data):
                print("  -> Waiting 10s gap...")
                time.sleep(10)

        except Exception as e:
            print(f"  !! Failed: {e}")
            supabase.schema("mstc").table("processed_pdfs").update({"status": "failed"}).eq("id", pdf['id']).execute()

    print(f"\nDone! Processed {len(pdfs.data)} corrigendum PDFs.")


if __name__ == "__main__":
    main()
