import os
import time
from datetime import datetime

import sys
projects_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
root_dir = os.path.dirname(projects_dir)
for p in [root_dir, projects_dir]:
    if p not in sys.path:
        sys.path.insert(0, p)

from projects.dantewada_scrape.scraper import (
    create_session,
    discover_dantewada,
    discover_forest_cg,
    download_pdf,
)
from projects.dantewada_scrape.extractor import extract_from_pdf
from projects.dantewada_scrape.db import (
    upsert_pdf,
    upsert_document,
    get_pending_pdfs,
    mark_processed,
    mark_failed,
    upload_pdf_to_storage,
    update_storage_url,
)


def discover(session, skip_existing=True):
    """Run discovery for both websites and upsert new PDFs to database."""
    print("\n=== DISCOVERY PHASE ===\n")

    sources = [
        ("Dantewada", discover_dantewada),
        ("Forest CG", discover_forest_cg),
    ]

    total_new = 0
    for name, discover_fn in sources:
        print(f"\n--- Discovering {name} ---")
        try:
            pdfs = discover_fn(session)
        except Exception as e:
            print(f"  !! Discovery failed for {name}: {e}")
            continue

        new_count = 0
        for pdf_info in pdfs:
            try:
                record = upsert_pdf(
                    source_url=pdf_info["source_url"],
                    source_website=pdf_info["source_website"],
                    title=pdf_info.get("title"),
                    listing_date=pdf_info.get("listing_date"),
                )
                if record:
                    new_count += 1
            except Exception as e:
                print(f"  !! Failed to upsert {pdf_info.get('source_url', '?')[:80]}: {e}")

        print(f"  {name}: {new_count} PDFs tracked")
        total_new += new_count

    print(f"\nDiscovery complete: {total_new} total PDFs tracked")
    return total_new


def extract(limit=10):
    """Process pending PDFs: download, convert to images, extract via Gemini."""
    print("\n=== EXTRACTION PHASE ===\n")

    session = create_session()
    pending = get_pending_pdfs(limit)
    if not pending:
        print("No pending PDFs to process.")
        return 0

    print(f"Processing {len(pending)} PDFs...\n")
    success_count = 0

    for idx, pdf in enumerate(pending):
        pdf_id = pdf["id"]
        source_url = pdf["source_url"]
        source_website = pdf["source_website"]
        title = pdf.get("title", "")

        print(f"[{idx + 1}/{len(pending)}] {source_website} - {title[:60]}...")

        try:
            pdf_bytes = download_pdf(session, source_url)
            if not pdf_bytes:
                print(f"  !! Download failed, marking as failed")
                mark_failed(pdf_id)
                continue

            print(f"  Downloaded {len(pdf_bytes)} bytes, extracting...")
            extraction = extract_from_pdf(pdf_bytes, source_website=source_website)

            print(f"  District: {extraction.get('district', 'N/A')}")
            print(f"  Date: {extraction.get('date_of_issuance', 'N/A')}")
            print(f"  Location: {extraction.get('location_of_incident', 'N/A')}")
            print(f"  Land: {extraction.get('land_hectares', 'N/A')}")

            upsert_document(pdf_id, extraction)

            filename = source_url.split("/")[-1].split("?")[0]
            if not filename.endswith(".pdf"):
                filename = f"{pdf_id}.pdf"
            try:
                storage_url = upload_pdf_to_storage(pdf_bytes, source_website, filename)
                update_storage_url(pdf_id, storage_url)
                print(f"  Uploaded to storage: {storage_url[:80]}...")
            except Exception as e:
                print(f"  !! Storage upload failed (non-fatal): {e}")

            mark_processed(pdf_id)
            success_count += 1
            print(f"  Success.\n")

            if idx < len(pending) - 1:
                print("  Waiting 10s gap...")
                time.sleep(10)

        except Exception as e:
            print(f"  !! Failed: {e}")
            mark_failed(pdf_id)

    print(f"\nExtraction complete: {success_count}/{len(pending)} succeeded")
    return success_count


def run_full_pipeline(discovery_limit=None, extraction_limit=10):
    """Run both discovery and extraction phases."""
    print("=" * 60)
    print("DANTEWADA / FOREST CG PDF SCRAPER")
    print(f"Started: {datetime.now().isoformat()}")
    print("=" * 60)

    session = create_session()
    discover(session)
    extract(limit=extraction_limit)

    print("=" * 60)
    print(f"Finished: {datetime.now().isoformat()}")
    print("=" * 60)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Dantewada / Forest CG PDF Scraper")
    parser.add_argument("--mode", choices=["discover", "extract", "full"], default="full")
    parser.add_argument("--limit", type=int, default=int(os.getenv("LIMIT", "10")))
    args = parser.parse_args()

    if args.mode == "discover":
        session = create_session()
        discover(session)
    elif args.mode == "extract":
        extract(limit=args.limit)
    else:
        run_full_pipeline(extraction_limit=args.limit)
