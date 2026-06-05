"""
Reprocess all agenda records whose extracted proposals have district > 15 chars.
These are text-fallback artifacts that need re-extraction with the fixed code.

Steps:
1. Query for proposals with LENGTH(district) > 15
2. Delete ALL proposals for the affected agenda_ids
3. Mark those agenda records as unprocessed
4. Re-process each record sequentially in the main thread (no ThreadPoolExecutor,
   because PyMuPDF is not thread-safe and _fitz_lock only fixes concurrent calls)
"""
import os, sys, logging, time, re
from datetime import datetime
from typing import List

projects_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'projects')
root_dir = os.path.join(projects_dir, '..')
for p in [projects_dir, root_dir]:
    if p not in sys.path:
        sys.path.insert(0, p)

from dotenv import load_dotenv
load_dotenv()

import psycopg2
from psycopg2.extras import RealDictCursor, execute_values
import requests

from parivesh_auto.utils import (
    PariveshScraper, truncate_pdf, extract_agenda_text,
    extract_proposals_via_tables, merge_page_boundaries,
    extract_proposals_from_text
)
from parivesh_auto.constants import KEYWORDS, TABLE_NAME

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("ReprocessDistrict")


def get_db_connection():
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL not set in .env")
    conn = psycopg2.connect(db_url, port=6543)
    conn.autocommit = False
    return conn


def find_affected_agendas(conn) -> List[dict]:
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT DISTINCT p.agenda_id, a.pdffilepath, a.meeting_id
        FROM parivesh.extracted_proposals p
        JOIN parivesh.agenda_v3 a ON a.id = p.agenda_id
        WHERE LENGTH(p.district) > 15
        ORDER BY p.agenda_id
    """)
    rows = cur.fetchall()
    cur.close()
    return rows


def count_bad_proposals(conn, agenda_id: int) -> int:
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM parivesh.extracted_proposals WHERE agenda_id = %s AND LENGTH(district) > 15",
        (agenda_id,)
    )
    n = cur.fetchone()[0]
    cur.close()
    return n


def count_all_proposals(conn, agenda_id: int) -> int:
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM parivesh.extracted_proposals WHERE agenda_id = %s",
        (agenda_id,)
    )
    n = cur.fetchone()[0]
    cur.close()
    return n


def process_single_record(
    agenda_id: int,
    pdf_url: str,
    meeting_id: str,
    keyword_patterns: dict,
) -> tuple:
    """
    Process a single PDF in the MAIN THREAD.
    Returns (agenda_id, cleaned_text, matched_keywords, proposals, status).
    """
    try:
        logger.info(f"  Downloading PDF for ID {agenda_id}...")
        resp = requests.get(pdf_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
        resp.raise_for_status()

        truncated = truncate_pdf(resp.content)
        cleaned = extract_agenda_text(truncated)

        matched = [kw for kw, pat in keyword_patterns.items() if pat.search(cleaned.lower())]
        logger.info(f"  Keywords matched: {matched}")

        proposals = extract_proposals_via_tables(truncated) if matched else []
        logger.info(f"  Table proposals: {len(proposals)}")

        if not proposals and matched:
            logger.info("  Falling back to text extraction...")
            merged = merge_page_boundaries(truncated)
            proposals = extract_proposals_from_text(merged.strip())
            logger.info(f"  Text proposals: {len(proposals)}")

        for prop in proposals:
            prop['meeting_id'] = meeting_id

        return agenda_id, cleaned, matched, proposals, "Success"
    except Exception as e:
        logger.warning(f"  Failed: {e}")
        return agenda_id, "", [], [], f"Error: {str(e)}"


def main():
    conn = get_db_connection()
    cur = conn.cursor()

    # Step 1: Find affected agendas
    affected = find_affected_agendas(conn)
    agenda_ids = [r['agenda_id'] for r in affected]
    total_agendas = len(agenda_ids)

    if total_agendas == 0:
        print("No proposals with district > 15 found.")
        conn.close()
        return

    # Count total bad proposals
    total_bad = 0
    total_all = 0
    for aid in agenda_ids:
        total_bad += count_bad_proposals(conn, aid)
        total_all += count_all_proposals(conn, aid)
    print(f"Found {total_agendas} agenda records with {total_bad} bad proposals out of {total_all} total")

    # Step 2: Delete ALL proposals for these agendas (clean slate)
    print(f"\nDeleting all {total_all} proposals for {total_agendas} agenda records...")
    cur.execute(
        "DELETE FROM parivesh.extracted_proposals WHERE agenda_id = ANY(%s)",
        (agenda_ids,)
    )
    deleted = cur.rowcount
    print(f"  Deleted {deleted} proposals")

    # Step 3: Mark as unprocessed
    print("Marking agenda records as unprocessed...")
    cur.execute(
        "UPDATE parivesh.agenda_v3 SET is_processed = 0 WHERE id = ANY(%s)",
        (agenda_ids,)
    )
    print(f"  Marked {cur.rowcount} records")

    conn.commit()
    print("  Committed.")

    # Step 4: Build keyword patterns (same as PariveshScraper does)
    keyword_patterns = {
        kw: re.compile(rf"\b{re.escape(kw)}\b", re.IGNORECASE)
        for kw in [k.lower() for k in KEYWORDS]
    }

    # Step 5: Process each record sequentially in MAIN THREAD
    print(f"\nProcessing {total_agendas} records sequentially in main thread...\n")

    insert_sql = """
        INSERT INTO parivesh.extracted_proposals
            (agenda_id, sr_no, proposal_no, file_no, project_name,
             proposal_for, activity, sector, state, district,
             proponent, meeting_date, meeting_id)
        VALUES %s
    """
    update_sql = """
        UPDATE parivesh.agenda_v3 AS t SET
            is_processed = 1,
            matched_keywords = v.matched,
            processed_on = v.proc_on,
            pdf_text = v.txt
        FROM (VALUES %s) AS v(id, matched, proc_on, txt)
        WHERE v.id = t.id
    """

    total_proposals = 0
    for i, row in enumerate(affected, 1):
        aid = row['agenda_id']
        pdf_url = row['pdffilepath']
        meeting_id = row['meeting_id']

        print(f"[{i}/{total_agendas}] agenda_id={aid}")

        # process in main thread
        rec_id, text, keywords, proposals, status = process_single_record(
            aid, pdf_url, meeting_id, keyword_patterns
        )

        print(f"  Status: {status} ({len(proposals)} proposals)")

        if status == "Success":
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            kw_str = ",".join(keywords) if keywords else None

            # Update agenda
            execute_values(cur, update_sql, [(rec_id, kw_str, now, text)])

            # Insert proposals
            if proposals:
                prop_rows = []
                for p in proposals:
                    prop_rows.append((
                        rec_id, p.get("sr_no"), p.get("proposal_no"),
                        p.get("file_no"), p.get("project_name"),
                        p.get("proposal_for"), p.get("activity"),
                        p.get("sector"), p.get("state"),
                        p.get("district"), p.get("proponent"),
                        p.get("meeting_date"), p.get("meeting_id"),
                    ))
                execute_values(cur, insert_sql, prop_rows)
                total_proposals += len(prop_rows)

            conn.commit()
            print(f"  Committed {len(proposals)} proposals.")
        else:
            print(f"  SKIPPED (error)")
            conn.rollback()

    print(f"\nDone. Processed {total_agendas} agenda records, {total_proposals} proposals total.")

    # Final verification
    cur.execute("SELECT COUNT(*) FROM parivesh.extracted_proposals")
    final_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM parivesh.extracted_proposals WHERE LENGTH(district) > 15")
    remaining_bad = cur.fetchone()[0]
    print(f"Final state: {final_count} proposals, {remaining_bad} with district > 15")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
