"""Test: re-process 5 keyword-matched-but-no-proposals PDFs and report results."""
import os, sys, json, re, time
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

# Add projects to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
import requests
from parivesh_auto.utils import (
    extract_proposals_via_tables,
    extract_agenda_text, truncate_pdf,
)
from parivesh_auto.gemini_extractor import extract_proposals_via_gemini
from parivesh_auto.constants import KEYWORDS

# Keyword patterns (same as the scraper)
keyword_patterns = {kw: re.compile(rf"\b{re.escape(kw)}\b", re.IGNORECASE) for kw in KEYWORDS}

# Fetch 5 sample records
conn = psycopg2.connect(os.getenv("DATABASE_URL"), port=6543)
cur = conn.cursor()
cur.execute("""
    SELECT a.id, a.pdffilepath, a.subject, a.committee_type, a.meeting_id, a.matched_keywords
    FROM parivesh.agenda_v3 a
    LEFT JOIN parivesh.extracted_proposals p ON a.id = p.agenda_id
    WHERE a.ref_type = 'AGENDA'
      AND a.matched_keywords IS NOT NULL
      AND a.is_processed = 1
      AND p.id IS NULL
    LIMIT 5
""")
rows = cur.fetchall()
conn.close()

print(f"Testing {len(rows)} documents\n")

session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0"})

for rec_id, pdfpath, subject, committee, meeting_id, matched_kw in rows:
    print(f"{'='*80}")
    print(f"ID: {rec_id}")
    print(f"Committee: {committee}, Meeting: {meeting_id}")
    print(f"Subject: {subject}")
    print(f"Stored keywords: {matched_kw}")
    print(f"PDF: {pdfpath}")
    print()

    try:
        # Download
        resp = session.get(pdfpath, timeout=60)
        resp.raise_for_status()
        print(f"  Downloaded: {len(resp.content)} bytes")

        # Truncate + extract text
        truncated = truncate_pdf(resp.content)
        cleaned = extract_agenda_text(truncated)
        text_len = len(cleaned)

        # Keyword matching
        text_lower = cleaned.lower() if cleaned else ""
        matched = [kw for kw, pat in keyword_patterns.items() if pat.search(text_lower)]
        print(f"  Extracted text length: {text_len} chars")
        print(f"  Keywords matched: {matched}")

        # Proposal extraction
        proposals = extract_proposals_via_tables(truncated) if matched else []
        if not proposals and matched:
            proposals = extract_proposals_via_gemini(cleaned)

        for prop in proposals:
            prop['meeting_id'] = meeting_id

        if proposals:
            print(f"  Proposals found: {len(proposals)}")
            for p in proposals[:5]:
                print(f"    - {p.get('proposal_no','?')} | {p.get('project_name','?')} | {p.get('state','?')} | {p.get('district','?')}")
        else:
            print(f"  Proposals found: 0")

            # Debug: show first/last 500 chars of text
            if text_len > 0:
                print(f"\n  --- First 500 chars of extracted text ---")
                print(f"  {cleaned[:500]}")
                print(f"  --- Last 500 chars ---")
                print(f"  {cleaned[-500:]}")
                # Check if text has proposal markers
                if "proposal no" in text_lower:
                    print(f"  NOTE: 'Proposal No' marker FOUND in text but extraction returned nothing")
                else:
                    print(f"  NOTE: No 'Proposal No' marker found in text")
            else:
                print(f"  NOTE: No text could be extracted from PDF")

    except Exception as e:
        print(f"  ERROR: {e}")

    print()

print(f"{'='*80}")
print("Done.")
