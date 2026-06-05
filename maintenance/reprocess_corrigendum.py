"""
Re-process all corrigendum PDFs with the new multi-block prompt.
Run AFTER the SQL migration (create corrigendum_blocks, drop old columns).
"""
import os, sys, time, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'projects'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

from supabase import create_client
from mstc_py.utils import download_pdf
from mstc_py.schemas import CorrigendumAddendum
from mstc_py.ai_extraction import extract_gemini_text
from mstc_py.fuzzy_match import fuzzy_match_block
from mstc_py.utils import upload_pdf_to_storage


def main():
    supabase = create_client(
        os.getenv("SUPABASE_URL"),
        os.getenv("SUPABASE_KEY"),
    )

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
        print(f"\n[{i}/{len(pdfs.data)}] Processing {file_id}...")

        # Download PDF
        pdf_bytes = download_pdf(pdf['pdf_url'])
        if not pdf_bytes:
            print(f"  !! Download failed, skipping")
            continue

        # Extract via Gemini
        extracted = extract_gemini_text(
            pdf_bytes,
            file_id,
            model="gemini-2.0-flash",
            target_class=CorrigendumAddendum,
        )
        if not extracted:
            print(f"  !! Extraction returned None, marking as error")
            supabase.schema("mstc").table("processed_pdfs") \
                .update({"status": "error", "extracted_at": "now()"}) \
                .eq("id", pdf['id']).execute()
            continue

        print(f"  -> Extracted {len(extracted.blocks)} block(s)")

        # Fuzzy match each block
        for block in extracted.blocks:
            match = fuzzy_match_block(block.blockName, blocks_cache.data)
            if match:
                block.state = match['state'] or block.state
                block.district = match['district'] or block.district

        # Upsert parent
        parent_resp = supabase.schema("mstc").table("corrigendum_addendum").upsert({
            "pdf_id": pdf['id'],
            "document_date": extracted.documentDate,
            "summary": extracted.summary,
        }, on_conflict="pdf_id").execute()
        parent_id = parent_resp.data[0]['id']

        # Insert child blocks
        if extracted.blocks:
            blocks_to_insert = [{
                "corrigendum_id": parent_id,
                "block_name": b.blockName,
                "state": b.state,
                "district": b.district,
                "change_summary": b.changeSummary,
            } for b in extracted.blocks]
            supabase.schema("mstc").table("corrigendum_blocks").insert(blocks_to_insert).execute()

        # Upload to S3 if any block is Chhattisgarh
        is_cg = any(b.state and "chhattisgarh" in b.state.lower() for b in extracted.blocks)
        if is_cg:
            s3_path = f"critical_minerals/corrigendum_addendum/chhattisgarh/{file_id}.pdf"
            existing = supabase.schema("mstc").table("processed_pdfs") \
                .select("storage_url").eq("id", pdf['id']).execute()
            if existing.data and existing.data[0].get('storage_url'):
                print(f"  -> Already in storage: {existing.data[0]['storage_url']}")
            else:
                try:
                    storage_url = upload_pdf_to_storage(pdf_bytes, s3_path)
                    supabase.schema("mstc").table("processed_pdfs").update({
                        "storage_url": storage_url
                    }).eq("id", pdf['id']).execute()
                    print(f"  -> Uploaded to storage: {storage_url}")
                except Exception as e:
                    print(f"  !! Storage upload failed (non-fatal): {e}")

        # Mark as processed
        supabase.schema("mstc").table("processed_pdfs").update({
            "status": "processed",
            "extracted_at": "now()",
        }).eq("id", pdf['id']).execute()

        # Brief delay between API calls
        time.sleep(1)

    print(f"\nDone! Processed {len(pdfs.data)} corrigendum PDFs.")


if __name__ == "__main__":
    main()
