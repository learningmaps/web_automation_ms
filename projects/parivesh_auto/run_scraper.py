import os
import sys
import argparse

# Path resolution to enable absolute package imports starting from the projects/ and root directories
projects_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
root_dir = os.path.dirname(projects_dir)
if projects_dir not in sys.path:
    sys.path.insert(0, projects_dir)
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from parivesh_auto.utils import PariveshScraper
from parivesh_auto.constants import KEYWORDS, TABLE_NAME

def main():
    parser = argparse.ArgumentParser(description="Standalone Parivesh Scraper Runner")
    parser.add_argument("--limit", type=int, default=100, help="Maximum number of PDFs to process")
    args = parser.parse_args()

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("Error: DATABASE_URL environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    print("Initializing Parivesh Scraper...")
    scraper = PariveshScraper(
        conn_string=db_url,
        keywords=KEYWORDS,
        table_name=TABLE_NAME
    )

    print("1. Fetching metadata for all committees...")
    total_new_metadata = 0
    for status, count in scraper.fetch_all_committees():
        print(f"   - {status}: {count} new records added")
        total_new_metadata += count
    print(f"Total new metadata records inserted: {total_new_metadata}")

    print(f"2. Downloading and extracting PDFs (Limit: {args.limit})...")
    processed_count = 0
    # process_pdfs_and_update returns a generator yielding progress dicts
    for progress in scraper.process_pdfs_and_update(limit=args.limit):
        print(f"   [{progress['current']}/{progress['total']}] ID {progress['id']}: {progress['status']}")
        if progress['status'] == "Success":
            processed_count += 1
    print(f"Finished PDF processing. Successfully processed {processed_count} PDFs.")

    scraper.close()
    print("Scraper run completed successfully.")

if __name__ == "__main__":
    main()
