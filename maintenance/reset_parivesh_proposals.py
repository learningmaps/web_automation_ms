"""
Reset is_processed=0 for agenda documents so they get re-extracted with
the latest extraction logic, then clear the proposals table.

Targets:
  1. EAC documents that have matched_keywords (any keyword hit)
  2. SEAC/SEIAA documents with statename_derived = 'Chhattisgarh'

Usage:
    python maintenance/reset_parivesh_proposals.py
"""

import os
import sys

projects_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'projects')
root_dir = os.path.join(projects_dir, '..')
for p in [projects_dir, root_dir]:
    if p not in sys.path:
        sys.path.insert(0, p)

from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import RealDictCursor

load_dotenv()


def get_target_ids(cur):
    cur.execute("""
        SELECT id, committee_type
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
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("Error: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    conn = psycopg2.connect(db_url, port=6543)
    cur = conn.cursor(cursor_factory=RealDictCursor)

    rows = get_target_ids(cur)
    ids = [r["id"] for r in rows]
    if not ids:
        print("No matching records found.")
        cur.close()
        conn.close()
        return

    eac = sum(1 for r in rows if r["committee_type"] == "EAC")
    seac = sum(1 for r in rows if r["committee_type"] == "SEAC")
    seiaa = sum(1 for r in rows if r["committee_type"] == "SEIAA")
    print(f"Target records: {len(ids)} total  (EAC: {eac}, SEAC: {seac}, SEIAA: {seiaa})")

    # Step 1: clear the proposals table
    cur.execute("SELECT COUNT(*) FROM parivesh.extracted_proposals")
    before = cur.fetchone()["count"]
    cur.execute("DELETE FROM parivesh.extracted_proposals")
    print(f"Proposals cleared: {before} → 0 (deleted {cur.rowcount})")

    # Step 2: reset is_processed
    cur.execute(
        "UPDATE parivesh.agenda_v3 SET is_processed = 0 WHERE id = ANY(%s)",
        (ids,)
    )
    print(f"Records reset (is_processed=0): {cur.rowcount}")

    conn.commit()
    cur.close()
    conn.close()
    print("Done. Run 'Fetch New Documents' in Streamlit to reprocess.")


if __name__ == "__main__":
    main()
