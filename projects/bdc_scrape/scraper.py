import os
import re
import time
import base64
import json
import requests
from bs4 import BeautifulSoup
from PIL import Image
import google.generativeai as genai
from datetime import datetime
from requests.models import Response
from requests.structures import CaseInsensitiveDict
from requests.cookies import cookiejar_from_dict
import urllib.parse
import psycopg2

# Custom database HTTP session proxy:
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
        # 1. Merge headers
        req_headers = self.headers.copy()
        if headers:
            req_headers.update(headers)
            
        # 2. Handle cookies in headers
        if self.cookies:
            cookie_str = "; ".join([f"{k}={v}" for k, v in self.cookies.items()])
            req_headers["Cookie"] = cookie_str
            
        # 3. Handle query params in URL
        if params:
            url = url + ("?" if "?" not in url else "&") + urllib.parse.urlencode(params)
            
        # 4. Handle JSON vs Form data
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

        # 5. Connect to database and execute http request
        try:
            conn = psycopg2.connect(self.db_url)
            conn.autocommit = True
            cur = conn.cursor()
        except Exception as conn_err:
            print(f"  [SQL Proxy] Database connection failed: {conn_err}")
            raise conn_err
        
        try:
            # Set statement and HTTP timeouts to avoid hanging indefinitely
            try:
                cur.execute("SET statement_timeout = 25000;")
                cur.execute("SET http.timeout_msec = 20000;")
            except Exception as set_err:
                print(f"  [SQL Proxy] Warning: Failed to set query timeouts: {set_err}")
                
            # Construct array of http_header composite types
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

        # 6. Parse response headers and cookies
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

        # 7. Create mock Response
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

# Import project components
from projects.bdc_scrape.constants import (
    BASE_URL, AJAX_URL, HEADERS, AJAX_HEADERS,
    COURT_COMPLEX_CODE, CASE_TYPE_NIA, STATUSES, YEARS
)
from projects.bdc_scrape.db import (
    get_supabase, upload_pdf_to_storage, upsert_case,
    sync_case_history, sync_case_orders
)

TEMP_DIR = "projects/bdc_scrape/temp_files"
os.makedirs(TEMP_DIR, exist_ok=True)

def parse_date(date_str):
    if not date_str or date_str.strip().lower() in ["", "none", "null", "n/a", "unknown"]:
        return None
    date_str = date_str.strip()
    
    # Try various formats
    formats = [
        "%d-%m-%Y",   # 19-06-2025
        "%d-%B-%Y",   # 02-July-2025
        "%d-%b-%Y",   # 02-Jul-2025
        "%Y-%m-%d"    # 2025-06-19
    ]
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
            
    # Try cleaning characters
    clean_str = re.sub(r'[^0-9a-zA-Z-]', '', date_str)
    for fmt in formats:
        try:
            return datetime.strptime(clean_str, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
            
    return None

def clean_text(text):
    if not text:
        return ""
    # Strip spaces, escape chars, and backslashes
    cleaned = re.sub(r'\s+', ' ', text).strip()
    cleaned = cleaned.replace('\\', '').replace('\n', ' ')
    return cleaned

def solve_captcha(image_path):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY not found in environment variables")
        
    genai.configure(api_key=api_key)
    
    # Sequential fallback models as mandated in project instructions
    models = [
        "gemini-3.1-flash-lite",
        "gemini-2.5-flash",
        "gemini-3-flash-preview"
    ]
    
    img = Image.open(image_path)
    prompt = (
        "Solve the CAPTCHA in this image. Output only the alphanumeric characters "
        "of the CAPTCHA without any spaces, punctuation, or extra words. The CAPTCHA is case-sensitive."
    )
    
    for model_name in models:
        try:
            print(f"  [Captcha] Attempting solve with {model_name}...")
            model = genai.GenerativeModel(model_name)
            generation_config = {"temperature": 0.0} # Deterministic
            response = model.generate_content([prompt, img], generation_config=generation_config)
            solved_text = response.text.strip()
            # Remove any non-alphanumeric chars
            solved_text = re.sub(r'[^a-zA-Z0-9]', '', solved_text)
            if len(solved_text) >= 4:
                print(f"  [Captcha] Solved successfully: '{solved_text}'")
                return solved_text
        except Exception as e:
            print(f"  [Captcha] Model {model_name} failed: {e}")
            
    raise RuntimeError("All configured Gemini models failed to solve the CAPTCHA")

def fetch_search_results(session, year, status):
    """
    Initiates session, downloads captcha, solves it, and posts search form.
    Retries up to 3 times in case of captcha failure.
    """
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        print(f"Scraping Year {year} ({status}) - Attempt {attempt}/{max_attempts}...")
        
        # 1. GET main page
        resp = session.get(BASE_URL, headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            print(f"  Error: Failed to load main page. Status: {resp.status_code}")
            time.sleep(2)
            continue
            
        soup = BeautifulSoup(resp.text, 'lxml')
        
        # Parse tokens
        scid_input = soup.find('input', {'name': 'scid'})
        tok_input = soup.find('input', {'name': lambda s: s and s.startswith('tok_')})
        
        if not scid_input or not tok_input:
            print("  Error: Missing form tokens in page HTML.")
            time.sleep(2)
            continue
            
        scid_val = scid_input.get('value')
        tok_name = tok_input.get('name')
        tok_val = tok_input.get('value')
        
        # 2. Get CAPTCHA image and download
        captcha_img = soup.find('img', {'id': 'siwp_captcha_image_0'}) or soup.find('img', src=lambda s: s and 'captcha' in s.lower())
        if not captcha_img:
            print("  Error: Captcha image element not found.")
            time.sleep(2)
            continue
            
        captcha_src = captcha_img.get('src')
        if not captcha_src.startswith('http'):
            captcha_url = "https://bastar.dcourts.gov.in" + (captcha_src if captcha_src.startswith('/') else "/" + captcha_src)
        else:
            captcha_url = captcha_src
            
        captcha_resp = session.get(captcha_url, headers=HEADERS, timeout=15)
        if captcha_resp.status_code != 200:
            print("  Error: Failed to download Captcha image.")
            time.sleep(2)
            continue
            
        captcha_path = f"{TEMP_DIR}/captcha_{year}_{status}.png"
        with open(captcha_path, "wb") as f:
            f.write(captcha_resp.content)
            
        # 3. Solve CAPTCHA
        try:
            captcha_code = solve_captcha(captcha_path)
        except Exception as e:
            print(f"  Error solving captcha: {e}")
            continue
            
        # 4. POST search request
        payload = {
            "service_type": "courtComplex",
            "est_code": COURT_COMPLEX_CODE,
            "case_type": CASE_TYPE_NIA,
            "reg_year": year,
            "case_status": status,
            "scid": scid_val,
            tok_name: tok_val,
            "siwp_captcha_value": captcha_code,
            "es_ajax_request": "1",
            "action": "get_cases_by_year"
        }
        
        search_resp = session.post(AJAX_URL, data=payload, headers=AJAX_HEADERS, timeout=20)
        if search_resp.status_code != 200:
            print(f"  Error: Search POST failed. Status: {search_resp.status_code}")
            continue
            
        try:
            search_json = search_resp.json()
            if not search_json.get("success"):
                data_val = search_json.get("data")
                if isinstance(data_val, dict):
                    msg = data_val.get("message", "Unknown error")
                else:
                    msg = str(data_val) or "Unknown error"
                print(f"  Search returned failure status: {msg}")
                if "captcha" in msg.lower():
                    print("  Retrying due to incorrect CAPTCHA code...")
                    continue
                return None
                
            return search_json.get("data")
        except Exception as e:
            print(f"  Failed to parse search response: {e}")
            
    print(f"Failed to fetch search results for {year} ({status}) after {max_attempts} attempts.")
    return None

def find_table_by_caption(soup, caption_text):
    for table in soup.find_all("table"):
        caption = table.find("caption")
        if caption and caption_text.lower() in caption.text.lower():
            return table
    return None

def parse_case_details(html_content):
    soup = BeautifulSoup(html_content, "lxml")
    data = {}

    # 1. Parse Case Details Table
    case_details_table = find_table_by_caption(soup, "Case Details")
    if case_details_table:
        tbody = case_details_table.find("tbody")
        if tbody:
            row = tbody.find("tr")
            if row:
                cols = [clean_text(td.text) for td in row.find_all("td")]
                if len(cols) >= 6:
                    data["case_type"] = cols[0]
                    data["filing_no"] = cols[1]
                    data["filing_date"] = parse_date(cols[2])
                    data["reg_no"] = cols[3]
                    data["reg_date"] = parse_date(cols[4])
                    data["cnr"] = cols[5]

    # 2. Parse Case Status Table
    case_status_table = find_table_by_caption(soup, "Case Status")
    if case_status_table:
        tbody = case_status_table.find("tbody")
        if tbody:
            row = tbody.find("tr")
            if row:
                cols = [clean_text(td.text) for td in row.find_all(["td", "th"])]
                if len(cols) >= 5:
                    data["first_hearing"] = parse_date(cols[0])
                    data["next_hearing"] = parse_date(cols[1])
                    data["case_status"] = cols[2]
                    data["stage"] = cols[3]
                    data["judge"] = cols[4]

    # Parse Court Complex Name from Heading
    header = soup.find(["h2", "h3"])
    data["court_name"] = clean_text(header.text) if header else "District And Sessions Court Bastar"

    # 3. Parse Petitioner & Advocate Details
    petitioner_div = soup.find("div", {"class": "Petitioner"})
    petitioners = []
    petitioner_adv = []
    if petitioner_div:
        items = petitioner_div.find_all("li")
        for item in items:
            p_tag = item.find("p")
            if p_tag:
                petitioners.append(clean_text(p_tag.text))
            item_text = clean_text(item.text)
            adv_match = re.search(r'Advocate\s*-\s*([^)]+)', item_text, re.IGNORECASE)
            if adv_match:
                petitioner_adv.append(clean_text(adv_match.group(1)))
    data["petitioners"] = petitioners
    data["petitioner_adv"] = petitioner_adv

    # 4. Parse Respondent & Advocate Details
    respondent_div = soup.find("div", {"class": "respondent"})
    respondents = []
    respondent_adv = []
    if respondent_div:
        items = respondent_div.find_all("li")
        for item in items:
            p_tag = item.find("p")
            if p_tag:
                respondents.append(clean_text(p_tag.text))
            item_text = clean_text(item.text)
            adv_match = re.search(r'Advocate\s*-\s*([^)]+)', item_text, re.IGNORECASE)
            if adv_match:
                respondent_adv.append(clean_text(adv_match.group(1)))
    data["respondents"] = respondents
    data["respondent_adv"] = respondent_adv

    # 5. Parse Acts & Sections
    acts_table = find_table_by_caption(soup, "Acts")
    acts = []
    if acts_table:
        tbody = acts_table.find("tbody")
        if tbody:
            for row in tbody.find_all("tr"):
                cols = [clean_text(td.text) for td in row.find_all("td")]
                if len(cols) >= 2:
                    acts.append({
                        "act": cols[0],
                        "sections": cols[1]
                    })
    data["acts_json"] = acts

    # 6. Parse FIR Details
    fir_table = find_table_by_caption(soup, "FIR Details")
    if fir_table:
        tbody = fir_table.find("tbody")
        if tbody:
            row = tbody.find("tr")
            if row:
                cols = [clean_text(td.text) for td in row.find_all("td")]
                if len(cols) >= 3:
                    data["police_station"] = cols[0]
                    data["fir_number"] = cols[1]
                    data["fir_year"] = cols[2]

    # 7. Parse Case History (Hearings)
    history_table = find_table_by_caption(soup, "Case History")
    history_rows = []
    if history_table:
        tbody = history_table.find("tbody")
        if tbody:
            for row in tbody.find_all("tr"):
                cols = row.find_all("td")
                if len(cols) >= 5:
                    reg_no = clean_text(cols[0].text)
                    judge = clean_text(cols[1].text)
                    
                    business_td = cols[2]
                    business_link = business_td.find("a")
                    business_date = parse_date(clean_text(business_td.text))
                    
                    business_meta = None
                    if business_link and business_link.get("data-case"):
                        try:
                            b64_data = business_link.get("data-case")
                            decoded = base64.b64decode(b64_data).decode("utf-8")
                            business_meta = json.loads(decoded)
                        except Exception as e:
                            print(f"Error decoding base64 data-case: {e}")
                            
                    hearing_date = parse_date(clean_text(cols[3].text))
                    purpose = clean_text(cols[4].text)
                    
                    history_rows.append({
                        "reg_no": reg_no,
                        "judge": judge,
                        "business_date": business_date,
                        "hearing_date": hearing_date,
                        "purpose": purpose,
                        "raw_meta": business_meta
                    })

    # 8. Parse PDF links in orders sections
    orders = []
    # Loop over all tables in the page
    for table in soup.find_all("table"):
        caption = table.find("caption")
        if caption and ("interimorder" in caption.text.lower() or "final order" in caption.text.lower() or "orders" in caption.text.lower()):
            order_type = "final" if "final" in caption.text.lower() else "interim"
            tbody = table.find("tbody")
            if tbody:
                for row in tbody.find_all("tr"):
                    cols = row.find_all("td")
                    # In e-court order lists, columns are usually: [Sl No, Order Date, Order Description, Link]
                    if len(cols) >= 3:
                        order_date_raw = clean_text(cols[1].text)
                        order_date = parse_date(order_date_raw)
                        order_desc = clean_text(cols[2].text)
                        
                        # Find PDF link in the row
                        link = row.find("a", href=True)
                        if link and (".pdf" in link.get("href").lower() or "get_order_pdf" in link.get("href").lower()):
                            orders.append({
                                "order_date": order_date,
                                "order_type": order_type,
                                "description": order_desc,
                                "pdf_url": link.get("href")
                            })

    return data, history_rows, orders

def fetch_business_text(session, raw_meta):
    """
    Calls get_business AJAX endpoint to get detailed order text for a hearing.
    """
    if not raw_meta:
        return ""
    payload = {
        "fields": base64.b64encode(json.dumps(raw_meta).encode('utf-8')).decode('utf-8'),
        "action": "get_business",
        "es_ajax_request": "1"
    }
    
    try:
        resp = session.post(AJAX_URL, data=payload, headers=AJAX_HEADERS, timeout=15)
        if resp.status_code == 200:
            resp_json = resp.json()
            if resp_json.get("success"):
                html = resp_json.get("data")
                soup = BeautifulSoup(html, "lxml")
                # Return cleaned text of the business order details
                return clean_text(soup.text)
    except Exception as e:
        print(f"Error fetching business description: {e}")
    return ""

def generate_pdf_printout(html_content, output_path):
    """
    Uses headless Playwright to load the HTML locally and print to a PDF.
    """
    from playwright.sync_api import sync_playwright
    # Wrap in standard Playwright context
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        # Abort requests to bastar.dcourts.gov.in to avoid geoblocking timeouts
        page.route("**/*", lambda route: route.abort() if "bastar.dcourts.gov.in" in route.request.url else route.continue_())
        page.set_content(html_content)
        # Wait for resources to load
        page.wait_for_load_state("load")
        # Generate A4 PDF with background colors preserved
        page.pdf(path=output_path, format="A4", print_background=True)
        browser.close()

def sync(progress_callback=None, max_cases=None):
    """
    Main orchestrator that runs the entire sync job.
    """
    print("--- STARTING BASTAR COURT CASES SYNC ---")
    db_url_india = os.getenv("DATABASE_URL_INDIA") or os.getenv("PROXY_DATABASE_URL")
    proxy_url = os.getenv("PROXY_URL")
    
    if db_url_india:
        print("Routing HTTP requests through Supabase India database proxy...")
        session = SupabaseSQLSession(db_url_india)
    elif proxy_url:
        print("Routing HTTP requests through proxy server...")
        session = requests.Session()
        session.proxies = {
            "http": proxy_url,
            "https": proxy_url
        }
    else:
        print("Routing HTTP requests directly...")
        session = requests.Session()
    
    scraped_cases = []
    
    # Step 1: Discover and fetch all cases list
    total_steps = len(YEARS) * len(STATUSES)
    current_step = 0
    
    for year in YEARS:
        for status_name, status_code in STATUSES.items():
            current_step += 1
            if progress_callback:
                progress_callback(
                    int((current_step / total_steps) * 30), 100, 
                    f"Discovering {status_name} cases for year {year}..."
                )
                
            results_html = fetch_search_results(session, year, status_code)
            if not results_html:
                print(f"No cases found for {year} ({status_name}).")
                continue
                
            # Parse search results to find CNR numbers and est_codes
            soup = BeautifulSoup(results_html, 'lxml')
            for div in soup.find_all("div", {"class": "distTableContent"}):
                est_code = div.get("data-est-code")
                for link in div.find_all("a", {"class": "viewCnrDetails"}):
                    cno = link.get("data-cno")
                    if cno and est_code:
                        scraped_cases.append({
                            "cno": cno,
                            "est_code": est_code,
                            "year": year,
                            "status": status_name
                        })
            
            # Rate limiting delay
            time.sleep(2)
            
    print(f"Discovered {len(scraped_cases)} total matching cases.")
    if not scraped_cases:
        print("No cases discovered across all sync criteria.")
        return 0
        
    # Step 2: Fetch details and process each case
    synced_count = 0
    for idx, item in enumerate(scraped_cases):
        if max_cases is not None and synced_count >= max_cases:
            print(f"Reached limit of {max_cases} cases to process. Stopping sync.")
            break
            
        cno = item["cno"]
        est_code = item["est_code"]
        year = item["year"]
        status = item["status"]
        
        print(f"[{idx+1}/{len(scraped_cases)}] Processing Case CNR: {cno}...")
        if progress_callback:
            progress_callback(
                30 + int((idx / len(scraped_cases)) * 60), 100, 
                f"Syncing case details [{idx+1}/{len(scraped_cases)}]: {cno}..."
            )
            
        # Fetch case details page HTML
        payload = {
            "cino": cno,
            "est_code": est_code,
            "action": "get_cnr_details",
            "es_ajax_request": 1
        }
        
        try:
            time.sleep(1) # Polite delay
            detail_resp = session.post(AJAX_URL, data=payload, headers=AJAX_HEADERS, timeout=15)
            if detail_resp.status_code != 200:
                print(f"  Error: Failed to fetch details for {cno}. Status: {detail_resp.status_code}")
                continue
                
            resp_json = detail_resp.json()
            if not resp_json.get("success"):
                print(f"  Error: Failed detail response for {cno}")
                continue
                
            details_html = resp_json.get("data")
            if not details_html:
                continue
                
            # Parse HTML content
            case_data, history_rows, orders_list = parse_case_details(details_html)
            case_data["establishment_code"] = est_code
            case_data["case_year"] = int(year)
            case_data["details_html"] = details_html
            
            # Build history rows without downloading Business On Date PDFs
            processed_history = []
            for h_row in history_rows:
                processed_history.append({
                    "judge": h_row["judge"],
                    "business_date": h_row["business_date"],
                    "hearing_date": h_row["hearing_date"],
                    "purpose": h_row["purpose"],
                    "business_text": ""
                })
                
            # Upload dynamic Order PDFs if present
            processed_orders = []
            for order in orders_list:
                pdf_url = order["pdf_url"]
                # E.g. https://bastar.dcourts.gov.in/wp-content/.../somefile.pdf
                print(f"  Downloading Order PDF: {pdf_url}")
                pdf_data_resp = session.get(pdf_url, headers=HEADERS, timeout=20)
                if pdf_data_resp.status_code == 200:
                    local_pdf_name = f"order_{cno}_{order['order_date']}.pdf"
                    local_pdf_path = f"{TEMP_DIR}/{local_pdf_name}"
                    with open(local_pdf_path, "wb") as f:
                        f.write(pdf_data_resp.content)
                        
                    # Upload to Supabase Storage
                    s3_path = f"bdc/cases/{year}/{status}/{cno}/orders/{order['order_date']}.pdf"
                    s3_public_url = upload_pdf_to_storage(local_pdf_path, s3_path)
                    
                    # Clean up local file
                    try:
                        os.remove(local_pdf_path)
                    except:
                        pass
                        
                    processed_orders.append({
                        "order_date": order["order_date"],
                        "order_type": order["order_type"],
                        "file_name": local_pdf_name,
                        "storage_path": s3_path,
                        "pdf_url": s3_public_url
                    })
                    
            # Generate Case Page printout PDF locally
            page_pdf_name = f"case_details_{cno}.pdf"
            page_pdf_path = f"{TEMP_DIR}/{page_pdf_name}"
            # Render a styled HTML wrapping details_html
            styled_html = f"""
            <html>
            <head>
                <style>
                    body {{ padding: 20px; font-family: sans-serif; }}
                    .distTableContent {{ margin-bottom: 20px; }}
                    table {{ width: 100%; border-collapse: collapse; margin-bottom: 15px; }}
                    th, td {{ border: 1px solid #ccc; padding: 8px; text-align: left; }}
                    th {{ background-color: #f2f2f2; }}
                </style>
            </head>
            <body>
                {details_html}
            </body>
            </html>
            """
            
            print(f"  Generating print layout PDF using Playwright...")
            generate_pdf_printout(styled_html, page_pdf_path)
            
            # Upload print layout PDF to storage
            page_s3_path = f"bdc/cases/{year}/{status}/{cno}/case_details.pdf"
            page_s3_url = upload_pdf_to_storage(page_pdf_path, page_s3_path)
            case_data["page_pdf_url"] = page_s3_url
            
            # Clean up local file
            try:
                os.remove(page_pdf_path)
            except:
                pass
                
            # Step 3: Insert / Sync all data to Supabase
            print(f"  Upserting case details in Supabase database...")
            case_uuid = upsert_case(case_data)
            
            print(f"  Syncing case hearing history...")
            sync_case_history(case_uuid, processed_history)
            
            print(f"  Syncing case orders...")
            sync_case_orders(case_uuid, processed_orders)
            
            synced_count += 1
            print(f"  Successfully synced case {cno}.")
            
        except Exception as case_err:
            print(f"  Failed to process case {cno}: {case_err}")
            
    # Clean up temp folder files
    for file in os.listdir(TEMP_DIR):
        try:
            os.remove(os.path.join(TEMP_DIR, file))
        except:
            pass
            
    if progress_callback:
        progress_callback(100, 100, "Sync complete!")
        
    print(f"--- BASTAR COURT SYNC FINISHED: Synced {synced_count} cases ---")
    return synced_count

if __name__ == "__main__":
    sync()
