"""
Backfill script: resets is_processed=0 for documents that should have
proposals extracted, so the scraper re-processes them with the new
table-based extraction logic.

Targets:
  1. EAC documents that have matched_keywords (any keyword hit)
  2. SEAC/SEIAA documents with statename_derived = 'Chhattisgarh'

Usage:
    python projects/parivesh_auto/backfill_proposals.py [--dry-run]

After running, click "Fetch New Documents" in the Streamlit app to reprocess.
"""
import os
import sys

projects_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
root_dir = os.path.dirname(projects_dir)
if projects_dir not in sys.path:
    sys.path.insert(0, projects_dir)
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

import argparse
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()


def get_target_records(cur) -> list[dict]:
    cur.execute("""
        SELECT id, committee_type, meeting_id, matched_keywords, statename_derived
        FROM parivesh.agenda_v3
        WHERE ref_type = 'AGENDA'
          AND (
            (committee_type = 'EAC' AND matched_keywords IS NOT NULL)
            OR
            (committee_type IN ('SEAC', 'SEIAA') AND statename_derived = 'Chhattisgarh')
          )
        ORDER BY committee_type, id
    """)
    return cur.fetchall()


def main():
    parser = argparse.ArgumentParser(description="Reset documents for proposal backfill")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without making changes")
    args = parser.parse_args()

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("Error: DATABASE_URL not found in environment or .env", file=sys.stderr)
        sys.exit(1)

    conn = psycopg2.connect(db_url, port=6543)
    cur = conn.cursor(cursor_factory=RealDictCursor)

    target_ids = []
    eac_kw = 0
    seac_ch = 0
    seiaa_ch = 0

    for row in get_target_records(cur):
        target_ids.append(row["id"])
        if row["committee_type"] == "EAC":
            eac_kw += 1
        elif row["committee_type"] == "SEAC":
            seac_ch += 1
        elif row["committee_type"] == "SEIAA":
            seiaa_ch += 1

    total = len(target_ids)
    print(f"Target records found: {total}")
    print(f"  EAC (any keyword):             {eac_kw}")
    print(f"  SEAC (Chhattisgarh):           {seac_ch}")
    print(f"  SEIAA (Chhattisgarh):          {seiaa_ch}")

    if total == 0:
        print("Nothing to do.")
        cur.close()
        conn.close()
        return

    # Check existing proposals count for these IDs
    cur.execute(
        "SELECT COUNT(*) FROM parivesh.extracted_proposals WHERE agenda_id = ANY(%s)",
        (target_ids,)
    )
    existing_proposals = cur.fetchone()["count"]
    print(f"\nExisting proposals for these IDs: {existing_proposals}")

    if args.dry_run:
        print("\nDry-run mode. No changes made.")
        print(f"Would delete {existing_proposals} proposals and set is_processed=0 for {total} records.")
    else:
        confirm = input(f"\nProceed to reset {total} records? [y/N]: ")
        if confirm.lower() != "y":
            print("Aborted.")
            cur.close()
            conn.close()
            return

        # Delete proposals
        if existing_proposals:
            cur.execute(
                "DELETE FROM parivesh.extracted_proposals WHERE agenda_id = ANY(%s)",
                (target_ids,)
            )
            print(f"Deleted {cur.rowcount} proposals.")

        # Reset is_processed
        cur.execute(
            "UPDATE parivesh.agenda_v3 SET is_processed = 0 WHERE id = ANY(%s)",
            (target_ids,)
        )
        print(f"Set is_processed=0 for {cur.rowcount} records.")

        conn.commit()
        print("Done. You can now run 'Fetch New Documents' in Streamlit.")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
