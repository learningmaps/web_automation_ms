import requests
import re
from bs4 import BeautifulSoup
from supabase import create_client, Client
import os
from dotenv import load_dotenv

load_dotenv()

URLS = {
    'mine_block_summary': 'https://www.mstcecommerce.com/auctionhome/container.jsp?title_id=Mine%20Block%20Summary&linkid=0&main_link=y&sublink=n&main_link_name=429&portal=mlcl&homepage=index&arcDate=30-11-2021',
    'nit': 'https://www.mstcecommerce.com/auctionhome/container.jsp?title_id=Notice%20Inviting%20Tender&linkid=0&main_link=y&sublink=n&main_link_name=427&portal=mlcl&homepage=index&arcDate=30-11-2021'
}

def get_supabase() -> Client:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        raise ValueError("SUPABASE_URL or SUPABASE_KEY not found")
    return create_client(url, key)

def scrape_links(progress_callback=None):
    supabase = get_supabase()
    print("--- STARTING MASTER BATCH LINK SCRAPER ---")
    
    all_found_links = []
    
    # 1. Scrape all sources first
    for i, (source, url) in enumerate(URLS.items()):
        if progress_callback:
            progress_callback(10 + (i * 20), 100, f"Scraping {source} from MSTC...")
            
        print(f"Scraping {source}...")
        try:
            resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=30)
            matches = re.findall(r'download_docs\.jsp\?FILE_ID=([^&"]+)', resp.text)
            
            for raw_id in matches:
                decoded_id = requests.utils.unquote(raw_id)
                all_found_links.append({
                    "file_id": decoded_id,
                    "source_page": source,
                    "pdf_url": f"https://www.mstcecommerce.com/auctionhome/download_docs.jsp?FILE_ID={raw_id}&path=dynamiclinks&portal=mlcl&mdTyp=-",
                    "status": "pending"
                })
        except Exception as e:
            print(f"  Error scraping {source}: {e}")

    if not all_found_links:
        print("No links found across any sources.")
        return

    # 2. Extract unique file IDs to check database
    unique_ids = list({link['file_id'] for link in all_found_links})
    
    if progress_callback:
        progress_callback(60, 100, f"Checking {len(unique_ids)} unique links against database...")

    # 3. Single batch fetch of existing records
    # Supabase allows up to ~1000 items in an 'in' filter usually, 
    # for 130+ items this is perfectly safe.
    existing_resp = supabase.table("processed_pdfs") \
        .select("file_id") \
        .in_("file_id", unique_ids) \
        .execute()
    
    existing_ids = {row['file_id'] for row in existing_resp.data}
    
    # 4. Filter for only new links (and deduplicate if same file_id in multiple sources)
    to_insert = []
    seen_in_batch = set()
    
    for link in all_found_links:
        fid = link['file_id']
        if fid not in existing_ids and fid not in seen_in_batch:
            to_insert.append(link)
            seen_in_batch.add(fid)

    # 5. Single batch insert
    new_count = len(to_insert)
    if to_insert:
        if progress_callback:
            progress_callback(80, 100, f"Pushing {new_count} new links to database...")
        supabase.table("processed_pdfs").insert(to_insert).execute()
        print(f"Added {new_count} new links in a single batch.")
    else:
        print("No new links discovered.")

    if progress_callback:
        progress_callback(100, 100, "Sync complete!")
            
    print("--- LINK SCRAPER COMPLETE ---")
    return new_count

if __name__ == "__main__":
    scrape_links()
