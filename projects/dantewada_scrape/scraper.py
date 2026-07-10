import os
import re
import time
import urllib.parse
from typing import List, Dict, Optional
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup
from requests.models import Response
from requests.structures import CaseInsensitiveDict
from requests.cookies import cookiejar_from_dict
import psycopg2

from projects.dantewada_scrape.constants import (
    HEADERS,
    DANTEWADA_BASE_URL,
    DANTEWADA_PAGE_URL,
    DANTEWADA_TABLE_SELECTOR,
    DANTEWADA_PDF_SELECTOR,
    FOREST_CG_BASE_URL,
    FOREST_CG_PDF_SELECTOR,
)


# ─── SQL HTTP Proxy (copied from BDC scraper) ───
class SupabaseSQLSession:
    def __init__(self, db_url):
        base_url = db_url.replace(":5432/", ":6543/")
        sep = "&" if "?" in base_url else "?"
        self.db_url = f"{base_url}{sep}sslmode=require&connect_timeout=15"
        self.cookies = {}
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }

    def request(self, method, url, params=None, data=None, headers=None, json=None, timeout=None, verify=None):
        start_time = time.time()
        req_headers = self.headers.copy()
        if headers:
            req_headers.update(headers)

        if self.cookies:
            cookie_str = "; ".join([f"{k}={v}" for k, v in self.cookies.items()])
            req_headers["Cookie"] = cookie_str

        if params:
            url = url + ("?" if "?" not in url else "&") + urllib.parse.urlencode(params)

        content_type = "application/x-www-form-urlencoded"
        body_content = ""
        if json is not None:
            import json as json_lib
            body_content = json_lib.dumps(json)
            content_type = "application/json"
        elif data is not None:
            if isinstance(data, dict):
                body_content = urllib.parse.urlencode(data)
            else:
                body_content = str(data)
        if content_type:
            req_headers["Content-Type"] = content_type

        try:
            conn = psycopg2.connect(self.db_url)
            conn.autocommit = True
            cur = conn.cursor()
        except Exception as conn_err:
            print(f"  [SQL Proxy] Database connection failed: {conn_err}")
            raise conn_err

        try:
            try:
                cur.execute("SET statement_timeout = 25000;")
                cur.execute("SET http.timeout_msec = 20000;")
            except Exception as set_err:
                print(f"  [SQL Proxy] Warning: Failed to set query timeouts: {set_err}")

            header_elements = []
            for k, v in req_headers.items():
                k_esc = k.replace("'", "''")
                v_esc = v.replace("'", "''")
                header_elements.append(f"row('{k_esc}', '{v_esc}')::http_header")

            headers_array_sql = f"ARRAY[{', '.join(header_elements)}]" if header_elements else "ARRAY[]::http_header[]"
            body_esc = body_content.replace("'", "''")

            sql = f"""
                SELECT status, headers, textsend(content)
                FROM http((
                    '{method.upper()}',
                    '{url}',
                    {headers_array_sql},
                    '{content_type}',
                    '{body_esc}'
                )::http_request);
            """

            cur.execute(sql)
            status, resp_headers_raw, content_bytes_mv = cur.fetchone()

            content_bytes = content_bytes_mv.tobytes() if hasattr(content_bytes_mv, 'tobytes') else content_bytes_mv
            if isinstance(content_bytes, str):
                content_bytes = content_bytes.encode('utf-8')

        except Exception as e:
            print(f"  [SQL Proxy] HTTP request execution failed: {e}")
            raise e
        finally:
            cur.close()
            conn.close()

        resp_headers = {}
        if resp_headers_raw:
            for item in resp_headers_raw:
                item_str = item.strip("()")
                if "," in item_str:
                    k, v = item_str.split(",", 1)
                    k_clean = k.strip('"').replace('\\"', '"')
                    v_clean = v.strip('"').replace('\\"', '"')
                    resp_headers[k_clean] = v_clean

                    if k_clean.lower() == "set-cookie":
                        cookie_match = re.match(r"([^=]+)=([^;]+)", v_clean)
                        if cookie_match:
                            ck, cv = cookie_match.groups()
                            self.cookies[ck.strip()] = cv.strip()

        response = Response()
        response.status_code = status
        response.headers = CaseInsensitiveDict(resp_headers)
        response._content = content_bytes
        response.cookies = cookiejar_from_dict(self.cookies)
        response.url = url

        elapsed = time.time() - start_time
        print(f"  [SQL Proxy] {method} {url} -> {status} ({len(content_bytes)} bytes) in {elapsed:.2f}s")
        return response

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)


# ─── Session Factory ───

def create_session() -> requests.Session:
    """Create the appropriate HTTP session based on environment.

    Uses SQL proxy if DATABASE_URL_INDIA is set, otherwise direct.
    Individual request failures will fall back to direct connections.
    """
    db_url_india = os.getenv("DATABASE_URL_INDIA") or os.getenv("PROXY_DATABASE_URL")
    proxy_url = os.getenv("PROXY_URL")

    if db_url_india:
        print("Using Supabase India database proxy for HTTP requests")
        return SupabaseSQLSession(db_url_india)
    elif proxy_url:
        print("Routing HTTP requests through proxy server...")
        session = requests.Session()
        session.proxies = {"http": proxy_url, "https": proxy_url}
        return session

    print("Routing HTTP requests directly...")
    return requests.Session()


# ─── Dantewada Link Discovery ───

def discover_dantewada(session: requests.Session) -> List[Dict]:
    """Scrape all PDF links from dantewada.nic.in notification pages.

    Returns list of dicts with keys: source_url, title, listing_date, source_website.
    """
    all_pdfs = []
    page_num = 1

    while True:
        if page_num == 1:
            url = DANTEWADA_BASE_URL
        else:
            url = DANTEWADA_PAGE_URL.format(page=page_num)

        print(f"  Fetching Dantewada page {page_num}: {url}")
        try:
            resp = session.get(url, headers=HEADERS, timeout=20)
            if resp.status_code != 200:
                print(f"    Page {page_num} returned status {resp.status_code}, stopping.")
                break
        except Exception as e:
            print(f"    Error fetching page {page_num}: {e}")
            break

        soup = BeautifulSoup(resp.content, "lxml")

        rows = soup.select(DANTEWADA_TABLE_SELECTOR)
        if not rows:
            print(f"    No table rows found on page {page_num}, stopping.")
            break

        page_count = 0
        for row in rows:
            cols = row.find_all("td")
            if len(cols) < 3:
                continue

            title = cols[0].get_text(strip=True)
            listing_date = cols[1].get_text(strip=True)

            pdf_link = cols[2].select_one("span.pdf-downloads a")
            if not pdf_link:
                continue

            pdf_url = pdf_link.get("href", "")
            if not pdf_url:
                continue

            all_pdfs.append({
                "source_url": pdf_url,
                "title": title,
                "listing_date": listing_date,
                "source_website": "dantewada",
            })
            page_count += 1

        print(f"    Found {page_count} PDFs on page {page_num}")

        has_next = soup.select_one(".pegination ul li.next a")
        if not has_next:
            break

        page_num += 1
        time.sleep(2)

    print(f"  Dantewada discovery complete: {len(all_pdfs)} PDFs total across {page_num} pages")
    return all_pdfs


# ─── Forest CG Link Discovery ───

def discover_forest_cg(session: requests.Session) -> List[Dict]:
    """Scrape all PDF links from forest.cg.gov.in FCA diversion cases page.

    Returns list of dicts with keys: source_url, title, listing_date, source_website.
    """
    all_pdfs = []
    base_url = "https://forest.cg.gov.in"

    print(f"  Fetching Forest CG page: {FOREST_CG_BASE_URL}")
    try:
        resp = session.get(FOREST_CG_BASE_URL, headers=HEADERS, timeout=30)
        if resp.status_code != 200:
            print(f"    Page returned status {resp.status_code}, trying direct connection...")
            raise Exception("proxy failed")
    except Exception as e:
        # Fall back to direct connection if proxy fails
        print(f"    Proxy fetch failed ({e}), trying direct connection...")
        try:
            direct_session = requests.Session()
            resp = direct_session.get(FOREST_CG_BASE_URL, headers=HEADERS, timeout=30)
            if resp.status_code != 200:
                print(f"    Direct connection also returned status {resp.status_code}")
                return all_pdfs
        except Exception as e2:
            print(f"    Direct connection also failed: {e2}")
            return all_pdfs

    soup = BeautifulSoup(resp.content, "lxml")

    pdf_links = soup.select(FOREST_CG_PDF_SELECTOR)
    for link in pdf_links:
        href = link.get("href", "")
        if not href:
            continue

        title = link.get_text(strip=True)

        if href.startswith("http"):
            pdf_url = href
        elif href.startswith("/"):
            pdf_url = base_url + href
        else:
            pdf_url = urljoin(FOREST_CG_BASE_URL, href)

        all_pdfs.append({
            "source_url": pdf_url,
            "title": title,
            "listing_date": None,
            "source_website": "forest_cg",
        })

    print(f"  Forest CG discovery complete: {len(all_pdfs)} PDFs found")
    return all_pdfs


# ─── PDF Download ───

def download_pdf(session: requests.Session, pdf_url: str) -> Optional[bytes]:
    """Download a PDF and return its bytes, or None on failure.
    Falls back to direct connection if the proxy session fails."""
    try:
        resp = session.get(pdf_url, headers=HEADERS, timeout=30)
        if resp.status_code == 200:
            content = resp.content
            if len(content) < 500:
                print(f"    Warning: PDF suspiciously small ({len(content)} bytes)")
                return None
            return content
        print(f"    Download failed: status {resp.status_code}")
    except Exception as e:
        print(f"    Proxy download failed ({e}), trying direct...")

    # Fall back to direct connection
    try:
        direct_session = requests.Session()
        resp = direct_session.get(pdf_url, headers=HEADERS, timeout=30)
        if resp.status_code == 200:
            content = resp.content
            if len(content) < 500:
                print(f"    Warning: PDF suspiciously small ({len(content)} bytes)")
                return None
            return content
        print(f"    Direct download also failed: status {resp.status_code}")
        return None
    except Exception as e:
        print(f"    Direct download error: {e}")
        return None
