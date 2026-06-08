"""Backfill PDFs to Supabase Storage for existing keyword-matched agendas.

Usage:
    python projects/parivesh_auto/backfill_pdfs.py          # backfill AGENDA + MOM
    python projects/parivesh_auto/backfill_pdfs.py --agenda  # AGENDA only
    python projects/parivesh_auto/backfill_pdfs.py --mom     # MOM only
    python projects/parivesh_auto/backfill_pdfs.py --limit 20
"""
import os
import sys
import argparse
import time

projects_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
root_dir = os.path.dirname(projects_dir)
for _p in (projects_dir, root_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from dotenv import load_dotenv
load_dotenv()

import psycopg2
import requests
from common.storage_utils import upload_pdf_to_storage

BUCKET = "parivesh-pdfs"
DB_URL = os.getenv("DATABASE_URL")
DB_PORT = 6543


def get_conn():
    return psycopg2.connect(DB_URL, port=DB_PORT)


def backfill_agendas(limit=None):
    """Upload keyword-matched AGENDA PDFs that are not yet stored."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, pdffilepath, committee_type
        FROM parivesh.agenda_v3
        WHERE ref_type = 'AGENDA'
        AND matched_keywords IS NOT NULL
        AND pdf_storage_url IS NULL
        AND pdffilepath IS NOT NULL
        ORDER BY id
    """ + (" LIMIT %s" % limit if limit else ""))
    rows = cur.fetchall()
    total = len(rows)
    print(f"Backfilling {total} AGENDA PDFs...")

    for i, (aid, pdfpath, ctype) in enumerate(rows, 1):
        try:
            print(f"  [{i}/{total}] ID {aid} ({ctype})...", end=" ", flush=True)
            resp = requests.get(pdfpath, timeout=60)
            resp.raise_for_status()
            storage_url = upload_pdf_to_storage(
                resp.content, BUCKET,
                f"parivesh/{ctype}/{aid}/agenda.pdf"
            )
            cur.execute(
                "UPDATE parivesh.agenda_v3 SET pdf_storage_url = %s WHERE id = %s",
                (storage_url, aid)
            )
            conn.commit()
            print("OK")
        except Exception as e:
            conn.rollback()
            print(f"FAIL: {e}")
        time.sleep(0.2)

    cur.close()
    conn.close()
    print(f"Done. {total} agendas processed.")


def backfill_moms(limit=None):
    """Upload MOM PDFs associated with keyword-matched agendas, not yet stored."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT a.id AS agenda_id, a.committee_type,
               m.id AS mom_id, m.pdffilepath AS mom_pdfpath
        FROM parivesh.agenda_v3 a
        JOIN parivesh.agenda_v3 m
            ON m.norm_subject = a.norm_subject
            AND m.committee_type = a.committee_type
            AND m.ref_type = 'MOM'
        WHERE a.ref_type = 'AGENDA'
        AND a.matched_keywords IS NOT NULL
        AND a.mom_pdf_storage_url IS NULL
        AND m.pdffilepath IS NOT NULL
        ORDER BY a.id
    """ + (" LIMIT %s" % limit if limit else ""))
    rows = cur.fetchall()
    total = len(rows)
    print(f"Backfilling {total} MOM PDFs...")

    for i, (agenda_id, ctype, mom_id, mom_pdfpath) in enumerate(rows, 1):
        try:
            print(f"  [{i}/{total}] agenda={agenda_id} mom={mom_id} ({ctype})...", end=" ", flush=True)
            resp = requests.get(mom_pdfpath, timeout=60)
            resp.raise_for_status()
            storage_url = upload_pdf_to_storage(
                resp.content, BUCKET,
                f"parivesh/{ctype}/{agenda_id}/mom.pdf"
            )
            cur.execute(
                "UPDATE parivesh.agenda_v3 SET mom_pdf_storage_url = %s WHERE id = %s",
                (storage_url, agenda_id)
            )
            cur.execute(
                "UPDATE parivesh.agenda_v3 SET pdf_storage_url = %s WHERE id = %s AND ref_type = 'MOM'",
                (storage_url, mom_id)
            )
            conn.commit()
            print("OK")
        except Exception as e:
            conn.rollback()
            print(f"FAIL: {e}")
        time.sleep(0.2)

    cur.close()
    conn.close()
    print(f"Done. {total} MOMs processed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill Parivesh PDFs to Supabase Storage")
    parser.add_argument("--agenda", action="store_true", help="Backfill AGENDA PDFs only")
    parser.add_argument("--mom", action="store_true", help="Backfill MOM PDFs only")
    parser.add_argument("--limit", type=int, default=None, help="Max PDFs to process")
    args = parser.parse_args()

    if not args.agenda and not args.mom:
        args.agenda = args.mom = True

    if args.agenda:
        backfill_agendas(limit=args.limit)
    if args.mom:
        backfill_moms(limit=args.limit)
