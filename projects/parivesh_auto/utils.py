import psycopg2
from psycopg2.extras import RealDictCursor, execute_values
import re
from datetime import datetime
from typing import Iterable, List, Optional, Dict, Generator, Tuple
from urllib.parse import urljoin
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import time
import os
import threading
import fitz

# PyMuPDF (fitz) has known thread-safety issues with global state (font cache, etc.).
# Serialise all fitz calls through this lock.
_fitz_lock = threading.Lock()

# Ensure parent 'projects' directory is in sys.path to allow absolute sub-project imports
import sys
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from parivesh_auto.constants import KEYWORDS, TABLE_NAME, PROPOSALS_TABLE_NAME
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging

# Setup Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("PariveshScraper")

STATE_MAPPING: Dict[int, str] = {
    35: "Andaman and Nicobar Islands", 28: "Andhra Pradesh", 12: "Arunachal Pradesh",
    18: "Assam", 10: "Bihar", 4: "Chandigarh", 22: "Chhattisgarh", 7: "Delhi",
    30: "Goa", 24: "Gujarat", 6: "Haryana", 2: "Himachal Pradesh", 1: "Jammu and Kashmir",
    20: "Jharkhand", 29: "Karnataka", 32: "Kerala", 37: "Ladakh", 31: "Lakshadweep",
    23: "Madhya Pradesh", 27: "Maharashtra", 14: "Manipur", 17: "Meghalaya",
    15: "Mizoram", 13: "Nagaland", 21: "Odisha", 34: "Puducherry", 3: "Punjab",
    8: "Rajasthan", 11: "Sikkim", 33: "Tamil Nadu", 36: "Telangana",
    38: "The Dadra and Nagar Haveli and Daman and Diu", 16: "Tripura",
    5: "Uttarakhand", 9: "Uttar Pradesh", 19: "West Bengal"
}

CHHATTISGARH_VARIANTS = {
    'chhattisgarh', 'chattisgarh', 'chhatisgarh', 'chatisgarh',
    'chhattisghar', 'chattisghar',
}





def extract_proposals_via_tables(pdf_content: bytes) -> list[dict]:
    '''
    Extract proposals using PyMuPDF's table detection.

    Iterates pages, detects 5-column proposal tables via find_tables(),
    merges continuation rows, and parses field values from each cell.
    Falls back to Gemini extraction (in _download_and_extract_text) if no tables found.
    '''
    with _fitz_lock:
        doc = fitz.open(stream=pdf_content, filetype="pdf")

        all_rows = []
        for page in doc:
            for table in page.find_tables():
                if table.col_count != 5:
                    continue
                cells = table.extract()
                # Normalize whitespace before colons (Sector : → Sector:) to handle PDF spacing variations
                FIELD_LABELS = ['Sector:', 'Activity:', 'Proposal For:', 'District:', 'Proposal No:']
                if not any(any(fl in re.sub(r'\s*:', ':', str(c)) for fl in FIELD_LABELS) for row in cells for c in row if c):
                    continue
                for row in cells:
                    sr = str(row[0]).strip() if row[0] else ''
                    det = str(row[1]) if row[1] else ''
                    if sr and 'Sr.' in sr and 'Proposal' in det[:30]:
                        continue
                    all_rows.append(row)
        doc.close()

    if not all_rows:
        return []

    # Merge continuation rows into preceding proposal
    merged = []
    for row in all_rows:
        sr = str(row[0]).strip() if row[0] else ''
        if sr:
            merged.append({
                'details': str(row[1]) if row[1] else '',
                'location': str(row[2]) if row[2] else '',
                'meeting_date': str(row[3]) if row[3] else '',
                'proponent': str(row[4]) if row[4] else '',
            })
        elif merged:
            for ci, key in enumerate(['details', 'location', 'meeting_date', 'proponent'], 1):
                if row[ci] and str(row[ci]).strip():
                    separator = '\n' if merged[-1][key] else ''
                    merged[-1][key] += separator + str(row[ci])

    # Parse fields from each merged proposal's cells
    results = []
    for idx, m in enumerate(merged):
        p: dict = {}
        p['sr_no'] = idx + 1

        details = m['details']
        location = m['location']

        # Proposal No
        match = re.search(r'Proposal No\s*:\s*(\S+)', details)
        p['proposal_no'] = match.group(1) if match else ''

        # File No
        match = re.search(r'File No\s*:\s*([^\n]+)', details)
        p['file_no'] = match.group(1).strip() if match else ''

        # Project Name
        match = re.search(
            r'Project Name\s*:\s*(.+?)(?=\n\s*(?:Proposal\s+For|State)\s*:|\Z)',
            details, re.DOTALL
        )
        if match:
            p['project_name'] = ' '.join(match.group(1).split())
        else:
            p['project_name'] = ''

        # Proposal For
        match = re.search(
            r'Proposal\s+For\s*:\s*(.+?)(?=\n\s*(?:Activity|Sector|State)\s*:|\Z)',
            details, re.DOTALL
        )
        p['proposal_for'] = ' '.join(match.group(1).split()) if match else ''

        # Activity
        match = re.search(
            r'Activity\s*:\s*(.+?)(?=\n\s*Sector\s*:|\Z)', details, re.DOTALL
        )
        p['activity'] = ' '.join(match.group(1).split()) if match else ''

        # Sector
        match = re.search(r'Sector\s*:\s*([^\n]+)', details)
        p['sector'] = match.group(1).strip() if match else ''

        # State from Location cell — no .title() fallback; blank stays blank so validation fails
        match = re.search(
            r'State\s*:\s*(.+?)(?=\n\s*District\s*:|\Z)', location, re.DOTALL
        )
        state_raw = match.group(1).strip() if match else ''
        p['state'] = state_raw.upper()

        # District from Location cell
        match = re.search(r'District\s*:\s*(.*)', location, re.DOTALL)
        district_raw = ' '.join(match.group(1).split()) if match else ''
        district_raw = re.sub(r'\s+\d+\s*$', '', district_raw)
        district_raw = re.sub(r'\s*-\s*', '-', district_raw.upper())
        p['district'] = district_raw

        # Meeting date
        p['meeting_date'] = m['meeting_date'].strip() if m['meeting_date'] else ''

        # Proponent
        p['proponent'] = ' '.join(m['proponent'].split()).strip()

        # Include proposal if any Chhattisgarh variant appears anywhere in the row
        full_text = ' '.join([m['details'], m['location'], m['meeting_date'], m['proponent']]).lower()
        if any(v in full_text for v in CHHATTISGARH_VARIANTS):
            results.append(p)

    # Dedup: same content (excluding sr_no) → keep first occurrence
    seen = set()
    deduped = []
    for p in results:
        key = tuple(p.get(k, '') for k in (
            'proposal_no', 'file_no', 'project_name', 'proposal_for',
            'activity', 'sector', 'state', 'district', 'proponent', 'meeting_date'
        ))
        if key not in seen:
            seen.add(key)
            deduped.append(p)
    return deduped





def truncate_pdf(pdf_content: bytes) -> bytes:
    """
    Remove content from a PDF below the first matching cutoff pattern.
    Uses coordinate-based block detection for all stop patterns in priority order.
    If no pattern is found via coordinates, returns the original content unchanged.
    """
    with _fitz_lock:
        doc = fitz.open(stream=pdf_content, filetype="pdf")

        stop_patterns = [
            "Any Other Item(s)",
            "Remarks",
            "List & Correspondence addresses",
            "Composition of Expert Appraisal Committee",
        ]

        best_page = None
        best_y = None
        for i, page in enumerate(doc):
            for b in page.get_text("blocks"):
                for pat in stop_patterns:
                    if pat.lower() in b[4].lower():
                        if best_page is None or i < best_page or (i == best_page and b[1] < best_y):
                            best_page, best_y = i, b[1]
                        break

        if best_page is None:
            doc.close()
            return pdf_content

        # Keep pages up to and including the cutoff page
        doc.select(list(range(best_page + 1)))

        # Redact content below cutoff on the last page
        page = doc[-1]
        rect = fitz.Rect(0, best_y, page.rect.x1, page.rect.y1)
        page.add_redact_annot(rect)
        page.apply_redactions()

        result = doc.tobytes()
        doc.close()
    return result


def extract_agenda_text(pdf_content: bytes) -> str:
    """Extract text from a (pre-truncated) PDF. No cut-off logic — already handled by truncate_pdf."""
    with _fitz_lock:
        doc = fitz.open(stream=pdf_content, filetype="pdf")
        text = "\n".join(page.get_text() for page in doc)
        doc.close()
    return text.strip()


def _proposals_valid(proposals: list[dict]) -> bool:
    """Return True if ALL proposals mention Chhattisgarh (anywhere in row) and have clean district."""
    DATE_PATTERN = re.compile(r'\d{2}/\d{2}/\d{4}')
    BLEED_WORDS = re.compile(r'LIMITED|PVT|CORPORATION|INDUSTRIES|COMPANY|CONSTRUCTIO', re.IGNORECASE)
    for p in proposals:
        check_fields = [str(p.get(k, '')) for k in (
            'proposal_no', 'file_no', 'project_name', 'proposal_for',
            'activity', 'sector', 'state', 'district', 'proponent', 'meeting_date'
        )]
        full_text = ' '.join(check_fields).lower()
        if not any(v in full_text for v in CHHATTISGARH_VARIANTS):
            return False
        district = p.get('district', '').strip()
        if not district:
            return False
        if DATE_PATTERN.search(district):
            return False
        if BLEED_WORDS.search(district):
            return False
    return True


class PariveshScraper:
    BASE_URL = "https://parivesh.nic.in"
    API_URL = f"{BASE_URL}/agendamom/getAgendaMomDocumentByCommitteeV2"

    def __init__(
        self,
        conn_string: str,
        keywords: Iterable[str] = (),
        table_name: str = "agenda_mom",
    ) -> None:
        logger.info("Initializing Scraper...")
        # Use port 6543 for stable pooling
        self.conn = psycopg2.connect(conn_string, port=6543)
        self.cur = self.conn.cursor(cursor_factory=RealDictCursor)
        self.keywords: List[str] = [k.lower() for k in keywords]
        self.keyword_patterns = {
            kw: re.compile(rf"\b{re.escape(kw)}\b", re.IGNORECASE)
            for kw in self.keywords
        }
        self.table_name = f"parivesh.{table_name}"
        self.proposals_table = f"parivesh.{PROPOSALS_TABLE_NAME}"

        self.session = requests.Session()
        retry_strategy = Retry(
            total=5,
            backoff_factor=2,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["POST", "GET"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        
        self._create_table()
        self._create_extracted_table()

    def _create_table(self) -> None:
        # Note: Unquoted identifiers in PostgreSQL become lowercase automatically.
        # Changed pdfFilePath -> pdffilepath for consistency.
        self.cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {self.table_name} (
                id BIGINT PRIMARY KEY, created_on TEXT, updated_on TEXT,
                created_by INTEGER, updated_by INTEGER, vers TEXT, date TEXT,
                ref_id INTEGER, ref_type TEXT, committee_type TEXT, pdffilepath TEXT,
                workgroup_id INTEGER, meeting_start_date TEXT, meeting_end_date TEXT,
                meeting_id TEXT, subject TEXT, sector TEXT, selected_sector INTEGER,
                sector_name TEXT, state TEXT, statename TEXT, statename_derived TEXT,
                is_active INTEGER, is_deleted INTEGER, is_processed INTEGER DEFAULT 0,
                matched_keywords TEXT, processed_on TEXT, pdf_text TEXT,
                norm_subject TEXT
            )
        """)
        self.conn.commit()

    def _create_extracted_table(self) -> None:
        self.cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {self.proposals_table} (
                id SERIAL PRIMARY KEY,
                agenda_id BIGINT NOT NULL REFERENCES {self.table_name}(id),
                sr_no INTEGER,
                proposal_no TEXT,
                file_no TEXT,
                project_name TEXT,
                proposal_for TEXT,
                activity TEXT,
                sector TEXT,
                state TEXT,
                district TEXT,
                proponent TEXT,
                meeting_date TEXT,
                meeting_id TEXT,
                created_on TIMESTAMP DEFAULT NOW()
            )
        """)
        self.conn.commit()

    def _format_date(self, date_str: Optional[str]) -> Optional[str]:
        if not date_str: return None
        try:
            clean_date = date_str.split(".")[0].replace("Z", "")
            return datetime.fromisoformat(clean_date).strftime("%Y-%m-%d %H:%M")
        except: return date_str

    def _derive_state_name(self, state_code: Optional[str | int]) -> Optional[str]:
        if state_code is None: return None
        try: return STATE_MAPPING.get(int(state_code))
        except: return None

    def _normalize_subject(self, subject: Optional[str]) -> Optional[str]:
        if not subject: return None
        # Same logic as database: strip EC/AGENDA/ or EC/MOM/ (case insensitive)
        return re.sub(r'(?i)^EC/(AGENDA|MOM)/', '', subject).strip()

    def fetch_for_committee(self, committee, ref_type: str) -> int:
        logger.info(f"Fetching metadata for {committee} ({ref_type})")
        params = {"committee": committee, "ref_type": ref_type, "workgroupId": "1"}
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "X-Requested-With": "XMLHttpRequest"
        }
        try:
            resp = self.session.post(self.API_URL, params=params, headers=headers, timeout=60)
            resp.raise_for_status()
            data = resp.json().get("data", [])
        except Exception as e:
            logger.error(f"Failed to fetch {committee} {ref_type}: {e}")
            return 0

        if not data: 
            logger.info(f"No documents found for {committee} {ref_type}")
            return 0

        insert_values = []
        for item in data:
            raw_path = item.get("pdfFilePath")
            code = item.get("state")
            subj = item.get("subject")
            insert_values.append((
                item.get("id"), self._format_date(item.get("created_on")),
                self._format_date(item.get("updated_on")), item.get("created_by"),
                item.get("updated_by"), item.get("vers"), self._format_date(item.get("date")),
                item.get("ref_id"), item.get("ref_type"), item.get("committee_type"),
                urljoin(self.BASE_URL, raw_path) if raw_path else None,
                item.get("workgroup_id"), self._format_date(item.get("meeting_start_date")),
                self._format_date(item.get("meeting_end_date")), item.get("meeting_id"),
                subj, self._normalize_subject(subj), item.get("sector"), item.get("selected_sector"),
                item.get("sector_name"), code, item.get("stateName"),
                self._derive_state_name(code), int(bool(item.get("is_active"))),
                int(bool(item.get("is_deleted"))), 0, None
            ))

        sql = f"""
            INSERT INTO {self.table_name} (
                id, created_on, updated_on, created_by, updated_by, vers, date, 
                ref_id, ref_type, committee_type, pdffilepath, workgroup_id, 
                meeting_start_date, meeting_end_date, meeting_id, subject, norm_subject,
                sector, selected_sector, sector_name, state, statename, statename_derived,
                is_active, is_deleted, is_processed, processed_on
            ) VALUES %s ON CONFLICT (id) DO NOTHING
        """
        try:
            execute_values(self.cur, sql, insert_values)
            new_rows = self.cur.rowcount
            self.conn.commit()
            logger.info(f"Bulk inserted {new_rows} new records for {committee}")
            return new_rows
        except Exception as e:
            self.conn.rollback()
            logger.error(f"Error during bulk insert: {e}")
            return 0

    def fetch_all_committees(
        self,
        committees: Iterable[str] = ("SEIAA", "SEAC", "EAC"),
        ref_types: Iterable[str] = ("AGENDA", "MOM"),
    ) -> Generator[Tuple[str, int], None, None]:
        """Fetch metadata for all committees in parallel but with a small pool to be respectful."""
        tasks = []
        for ctype in committees:
            for ref in ref_types:
                tasks.append((ctype, ref))
        
        # Limit to 3 concurrent metadata fetches to be very moderate with Parivesh server
        with ThreadPoolExecutor(max_workers=3) as executor:
            future_to_task = {executor.submit(self.fetch_for_committee, c, r): (c, r) for c, r in tasks}
            for future in as_completed(future_to_task):
                c, r = future_to_task[future]
                try:
                    new_count = future.result()
                    yield f"Finished {c} - {r}", new_count
                except Exception as e:
                    logger.error(f"Error in parallel fetch for {c} {r}: {e}")
                    yield f"Failed {c} - {r}", 0

    def _download_and_extract_text(
        self, rec_id: int, pdfpath: str, meeting_id: str,
        committee_type: str | None = None, statename_derived: str | None = None
    ) -> Tuple[int, str, List[str], List[dict], str]:
        """Worker function for threads: Downloads, extracts text, matches keywords, and parses proposals."""
        try:
            time.sleep(0.1 * (rec_id % 10))

            # SEIAA/SEAC non-CG: skip entirely
            if committee_type in ('SEIAA', 'SEAC') and statename_derived != 'Chhattisgarh':
                logger.debug(f"Skipping non-CG {committee_type} doc ID {rec_id}")
                return rec_id, "", [], [], "Success"

            logger.debug(f"Downloading PDF for ID {rec_id}")
            resp = self.session.get(pdfpath, headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
            resp.raise_for_status()

            # Truncate the PDF to remove content below the first cutoff marker
            truncated_pdf = truncate_pdf(resp.content)

            # Extract clean text for keyword matching
            cleaned_text = extract_agenda_text(truncated_pdf)

            # SEIAA/SEAC Chhattisgarh: default keyword, always extract
            if committee_type in ('SEIAA', 'SEAC') and statename_derived == 'Chhattisgarh':
                matched = ['chhattisgarh']
            else:
                # EAC: keyword matching on the cleaned text
                matched = [kw for kw, pat in self.keyword_patterns.items() if pat.search(cleaned_text.lower())]

            # Parse proposals — try table-based extraction first, validate, then Gemini fallback
            proposals = extract_proposals_via_tables(truncated_pdf) if matched else []
            if matched and (not proposals or not _proposals_valid(proposals)):
                from parivesh_auto.gemini_extractor import extract_proposals_via_gemini
                proposals = extract_proposals_via_gemini(cleaned_text)
            for prop in proposals:
                prop['meeting_id'] = meeting_id

            return rec_id, cleaned_text, matched, proposals, "Success"
        except Exception as e:
            logger.warning(f"Failed to process PDF for ID {rec_id}: {e}")
            return rec_id, "", [], [], f"Error: {str(e)}"

    def process_pdfs_and_update(
        self, limit: int | None = None, max_workers: int | None = None
    ) -> Generator[Dict, None, None]:
        """Parallel processing of PDFs with batch updates. Dynamic worker count based on CPU."""
        if max_workers is None:
            cpu_cores = os.cpu_count() or 4
            max_workers = min(cpu_cores, 8)

        sql = f"""
            SELECT id, pdffilepath, meeting_id, committee_type, statename_derived
            FROM {self.table_name}
            WHERE is_processed = 0 AND ref_type = 'AGENDA'
        """
        if limit:
            self.cur.execute(sql + " LIMIT %s", (limit,))
        else:
            self.cur.execute(sql)

        rows = self.cur.fetchall()
        total = len(rows)
        if total == 0:
            logger.info("No pending PDFs to process.")
            return

        logger.info(f"Starting parallel processing for {total} PDFs using {max_workers} workers...")

        update_sql = f"""
            UPDATE {self.table_name} AS t SET
                is_processed = 1,
                matched_keywords = v.matched,
                processed_on = v.proc_on,
                pdf_text = v.txt
            FROM (VALUES %s) AS v(id, matched, proc_on, txt)
            WHERE v.id = t.id
        """

        proposals_insert_sql = f"""
            INSERT INTO {self.proposals_table}
                (agenda_id, sr_no, proposal_no, file_no, project_name,
                 proposal_for, activity, sector, state, district,
                 proponent, meeting_date, meeting_id)
            VALUES %s
        """

        agenda_batch = []
        proposals_batch = []
        batch_size = 5

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {
                executor.submit(
                    self._download_and_extract_text, r["id"], r["pdffilepath"],
                    r["meeting_id"], r["committee_type"], r["statename_derived"]
                ): r["id"] for r in rows if r["pdffilepath"]
            }

            for i, future in enumerate(as_completed(future_map), 1):
                rec_id, text, keywords, proposals, status = future.result()
                now = datetime.now().strftime("%Y-%m-%d %H:%M")

                if status == "Success":
                    kw_str = ",".join(keywords) if keywords else None
                    agenda_batch.append((rec_id, kw_str, now, text))
                    for prop in proposals:
                        proposals_batch.append((
                            rec_id, prop.get("sr_no"), prop.get("proposal_no"),
                            prop.get("file_no"), prop.get("project_name"),
                            prop.get("proposal_for"), prop.get("activity"),
                            prop.get("sector"), prop.get("state"),
                            prop.get("district"), prop.get("proponent"),
                            prop.get("meeting_date"), prop.get("meeting_id"),
                        ))

                if len(agenda_batch) >= batch_size or i == total:
                    if agenda_batch:
                        try:
                            execute_values(self.cur, update_sql, agenda_batch)
                            if proposals_batch:
                                execute_values(self.cur, proposals_insert_sql, proposals_batch)
                            self.conn.commit()
                            agenda_batch = []
                            proposals_batch = []
                        except Exception as e:
                            self.conn.rollback()
                            logger.error(f"Batch update failed: {e}")
                            status = f"Batch Error"

                yield {"current": i, "total": total, "id": rec_id, "status": status}

    def close(self) -> None:
        logger.info("Closing Scraper connection.")
        self.conn.close()
