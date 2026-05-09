import os
import requests
import time
import pandas as pd
import re
from datetime import datetime
from supabase import create_client, Client
from dotenv import load_dotenv
from extractor import safe_extract
from schemas import PAGE_SCHEMA_MAP

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

def process_pending_pdfs(limit=10):
    supabase = get_supabase()
    print(f"--- STARTING PDF EXTRACTOR (PYTHON) - Limit: {limit} ---")

    # Fetch non-processed PDFs (pending or failed)
    resp = supabase.table("processed_pdfs") \
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
        page_name = 'Mine Block Summary' if source == 'mine_block_summary' else 'Notice Inviting Tender'

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
                supabase.table("mine_block_summaries").insert({
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
                }).execute()
            
            elif source == 'nit':
                d = extracted_data
                nit_resp = supabase.table("tenders_nit").insert({
                    "pdf_id": pdf['id'],
                    "nit_number": d.nitNumber,
                    "tranche": d.tranche,
                    "tender_date": normalize_timestamp(d.tenderDate),
                    "bid_submission_deadline": normalize_timestamp(d.bidSubmissionDeadline),
                    "tender_fee": d.tenderFee,
                    "bid_security_emd": d.bidSecurityEMD
                }).execute()
                
                nit_id = nit_resp.data[0]['id']
                if d.blocks:
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
                    supabase.table("tender_blocks").insert(blocks_to_insert).execute()

            # 4. Mark as Processed
            supabase.table("processed_pdfs").update({
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
            supabase.table("processed_pdfs").update({"status": "failed"}).eq("id", pdf['id']).execute()

if __name__ == "__main__":
    limit = int(os.getenv("LIMIT", "10"))
    process_pending_pdfs(limit)
