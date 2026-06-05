import os
import requests
import time
import pandas as pd
import re
from datetime import datetime
from supabase import create_client, Client
from dotenv import load_dotenv

# Ensure correct paths for imports
import sys
projects_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
root_dir = os.path.dirname(projects_dir)
for p in [root_dir, projects_dir]:
    if p not in sys.path:
        sys.path.insert(0, p)

from mstc_py.extractor import safe_extract
from mstc_py.schemas import PAGE_SCHEMA_MAP

load_dotenv()

def normalize_timestamp(ts_str: str) -> str:
    """Uses pandas to convert conversational dates to ISO format for PostgreSQL."""
    if not ts_str or ts_str.lower() in ["n/a", "not specified", "none"]:
        return None
    try:
        # Clean common LLM noise like "on or before" or timezone strings
        clean_str = re.sub(r'on or before|hours|\(Indian Standard Time\)', '', ts_str, flags=re.IGNORECASE).strip()
        dt = pd.to_datetime(clean_str)
        return dt.isoformat()
    except Exception:
        return ts_str

def get_supabase() -> Client:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    return create_client(url, key)

def upload_pdf_to_storage(pdf_bytes: bytes, storage_path: str) -> str:
    supabase = get_supabase()
    bucket_name = "mstc-pdfs"
    try:
        supabase.storage.create_bucket(bucket_name, options={"public": True})
    except Exception as e:
        if "already exists" not in str(e).lower():
            print(f"  !! Warning creating bucket: {e}")
    supabase.storage.from_(bucket_name).upload(
        path=storage_path,
        file=pdf_bytes,
        file_options={"content-type": "application/pdf", "x-upsert": "true"}
    )
    return supabase.storage.from_(bucket_name).get_public_url(storage_path)

def fuzzy_match_block(extracted_name: str, blocks: list[dict]) -> dict | None:
    extracted_tokens = set(re.findall(r'\w+', extracted_name.lower()))
    best = None
    best_score = 0.0

    for block in blocks:
        db_name = block.get('block_name', '') or ''
        db_tokens = set(re.findall(r'\w+', db_name.lower()))

        if extracted_name.lower() == db_name.lower():
            return block

        if extracted_name.lower() in db_name.lower() or db_name.lower() in extracted_name.lower():
            score = 0.8
        else:
            if not extracted_tokens or not db_tokens:
                continue
            score = len(extracted_tokens & db_tokens) / len(extracted_tokens | db_tokens)

        if score > best_score:
            best_score = score
            best = block

    return best if best_score >= 0.7 else None

def process_pending_pdfs(limit=10):
    supabase = get_supabase()
    print(f"--- STARTING PDF EXTRACTOR (PYTHON) - Limit: {limit} ---")

    # Fetch non-processed PDFs (pending or failed)
    resp = supabase.schema("mstc").table("processed_pdfs") \
        .select("*") \
        .neq("status", "processed") \
        .limit(limit) \
        .execute()

    pending_pdfs = resp.data
    if not pending_pdfs:
        print("No pending PDFs to process.")
        return

    print(f"Processing {len(pending_pdfs)} PDFs...")

    for pdf in pending_pdfs:
        file_id = pdf['file_id']
        pdf_url = pdf['pdf_url']
        source = pdf['source_page']
        page_name = 'Mine Block Summary' if source == 'mine_block_summary' else 'Notice Inviting Tender' if source == 'nit' else 'Corrigendum and Addendum'

        print(f"Processing {file_id} ({page_name})...")

        try:
            # 1. Download
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
            pdf_resp = requests.get(pdf_url, headers=headers, timeout=30)
            pdf_resp.raise_for_status()
            pdf_bytes = pdf_resp.content
            
            if len(pdf_bytes) < 1000:
                 print(f"  !! Warning: PDF size unusually small ({len(pdf_bytes)} bytes)")

            # 2. Extract
            config = PAGE_SCHEMA_MAP.get(page_name)
            if not config:
                raise ValueError(f"No schema found for {page_name}")

            print(f"  -> Converting to Markdown and extracting...")
            extracted_data = safe_extract(pdf_bytes, config['model'], config['prompt'])

            # 3. Save Data
            if source == 'mine_block_summary':
                d = extracted_data
                supabase.schema("mstc").table("mine_block_summaries").upsert({
                    "pdf_id": pdf['id'],
                    "block_name": d.blockName,
                    "state": d.state,
                    "district": d.district,
                    "tehsil_taluka": d.tehsilTaluka,
                    "villages": d.villages,
                    "mineral_commodity": d.mineralCommodity,
                    "exploration_stage": d.explorationStage,
                    "forest_land_area": float(d.landBreakdown.forestLandArea) if d.landBreakdown.forestLandArea.replace('.','',1).isdigit() else 0,
                    "revenue_land_area": float(d.landBreakdown.revenueLandArea) if d.landBreakdown.revenueLandArea.replace('.','',1).isdigit() else 0,
                    "private_land_area": float(d.landBreakdown.private_land_area) if d.landBreakdown.private_land_area.replace('.','',1).isdigit() else 0,
                    "government_land_area": float(d.landBreakdown.governmentLandArea) if d.landBreakdown.governmentLandArea.replace('.','',1).isdigit() else 0,
                    "total_area_hectares": float(d.landBreakdown.totalAreaHectares) if d.landBreakdown.totalAreaHectares.replace('.','',1).isdigit() else 0,
                    "total_resources_mt": float(d.resources.totalResourcesMT) if d.resources.totalResourcesMT.replace('.','',1).isdigit() else 0,
                    "average_grade": d.resources.averageGrade,
                    "geological_setting": d.geologicalSetting,
                    "toposheet_number": d.toposheetNumber,
                    "geographic_coordinates": d.geographicCoordinates
                }, on_conflict="pdf_id").execute()
            
            elif source == 'nit':
                d = extracted_data
                nit_resp = supabase.schema("mstc").table("tenders_nit").upsert({
                    "pdf_id": pdf['id'],
                    "nit_number": d.nitNumber,
                    "tranche": d.tranche,
                    "tender_date": normalize_timestamp(d.tenderDate),
                    "bid_submission_deadline": normalize_timestamp(d.bidSubmissionDeadline),
                    "tender_fee": d.tenderFee,
                    "bid_security_emd": d.bidSecurityEMD
                }, on_conflict="pdf_id").execute()
                
                nit_id = nit_resp.data[0]['id']
                if d.blocks:
                    # Clear old blocks if this is an update
                    supabase.schema("mstc").table("tender_blocks").delete().eq("nit_id", nit_id).execute()
                    
                    blocks_to_insert = [{
                        "nit_id": nit_id,
                        "sl_no": b.slNo,
                        "state": b.state,
                        "district": b.district,
                        "block_name": b.blockName,
                        "mineral": b.mineral,
                        "license_type": b.licenseType,
                        "reserve_price": b.reservePrice
                    } for b in d.blocks]
                    supabase.schema("mstc").table("tender_blocks").insert(blocks_to_insert).execute()

            elif source == 'corrigendum_addendum':
                d = extracted_data
                # Fuzzy match each block against known mine block summaries
                blocks_cache = supabase.schema("mstc").table("mine_block_summaries").select("block_name, state, district").execute()
                for block in d.blocks:
                    match = fuzzy_match_block(block.blockName, blocks_cache.data)
                    if match:
                        block.state = match['state'] or block.state
                        block.district = match['district'] or block.district
                # Upsert parent row
                parent_resp = supabase.schema("mstc").table("corrigendum_addendum").upsert({
                    "pdf_id": pdf['id'],
                    "document_date": normalize_timestamp(d.documentDate),
                    "summary": d.summary
                }, on_conflict="pdf_id").execute()
                parent_id = parent_resp.data[0]['id']
                # Clear old child blocks (clean slate for re-extracts)
                supabase.schema("mstc").table("corrigendum_blocks").delete().eq("corrigendum_id", parent_id).execute()
                # Insert new child blocks
                if d.blocks:
                    blocks_to_insert = [{
                        "corrigendum_id": parent_id,
                        "block_name": b.blockName,
                        "state": b.state,
                        "district": b.district,
                        "change_summary": b.changeSummary
                    } for b in d.blocks]
                    supabase.schema("mstc").table("corrigendum_blocks").insert(blocks_to_insert).execute()

            # 4. Upload Chhattisgarh PDFs to Storage
            is_cg = False
            if source == 'mine_block_summary':
                is_cg = d.state and "chhattisgarh" in d.state.lower()
            elif source == 'nit':
                is_cg = any(b.state and "chhattisgarh" in b.state.lower() for b in (d.blocks or []))
            elif source == 'corrigendum_addendum':
                is_cg = any(b.state and "chhattisgarh" in b.state.lower() for b in d.blocks)

            if is_cg:
                s3_path = f"critical_minerals/{source}/chhattisgarh/{file_id}.pdf"
                # Check if already uploaded
                existing = supabase.schema("mstc").table("processed_pdfs").select("storage_url").eq("id", pdf['id']).execute()
                if existing.data and existing.data[0].get('storage_url'):
                    storage_url = existing.data[0]['storage_url']
                    print(f"  -> Already in storage: {storage_url}")
                else:
                    try:
                        storage_url = upload_pdf_to_storage(pdf_bytes, s3_path)
                        supabase.schema("mstc").table("processed_pdfs").update({
                            "storage_url": storage_url
                        }).eq("id", pdf['id']).execute()
                        print(f"  -> Uploaded to storage: {storage_url}")
                    except Exception as e:
                        print(f"  !! Storage upload failed (non-fatal): {e}")

            # 5. Mark as Processed
            supabase.schema("mstc").table("processed_pdfs").update({
                "status": "processed",
                "extracted_at": datetime.now().isoformat()
            }).eq("id", pdf['id']).execute()

            print(f"  -> Success.")
            
            # Gap to respect RPM
            if pending_pdfs.index(pdf) < len(pending_pdfs) - 1:
                print("  -> Waiting 10s gap...")
                time.sleep(10)

        except Exception as e:
            print(f"  !! Failed: {e}")
            supabase.schema("mstc").table("processed_pdfs").update({"status": "failed"}).eq("id", pdf['id']).execute()

if __name__ == "__main__":
    limit = int(os.getenv("LIMIT", "10"))
    process_pending_pdfs(limit)
