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
import fitz

# Ensure parent 'projects' directory is in sys.path to allow absolute sub-project imports
import sys
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from common.document_processing import extract_agenda_text
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


def extract_proposals_from_text(text: str) -> list[dict]:
    """
    Parse cleaned agenda text and extract individual proposals.

    Only returns proposals where the state is a Chhattisgarh variant.
    Each proposal is identified by the 'Proposal No :' marker.
    """
    blocks = re.findall(
        r'Proposal No\s*:\s*(.*?)(?=Proposal No\s*:|\Z)',
        text, re.DOTALL
    )

    FIELD_PREFIXES = [
        'Proposal No', 'File No', 'Project Name', 'Proposal For',
        'Activity', 'Sector', 'State', 'District',
    ]

    results = []
    for idx, block in enumerate(blocks):
        p: dict = {}

        p['sr_no'] = idx + 1

        # Proposal No (first line of block)
        lines = block.strip().split('\n')
        p['proposal_no'] = lines[0].strip() if lines else ''

        # File No
        m = re.search(r'File No\s*:\s*(.+)', block)
        p['file_no'] = m.group(1).strip() if m else ''

        # Project Name (stop at first of Proposal For or State — handles multi-page proposals)
        m = re.search(
            r'Project Name\s*:\s*(.+?)(?=\n\s*(?:Proposal\s+For|State)\s*:)',
            block, re.DOTALL
        )
        if m:
            p['project_name'] = ' '.join(m.group(1).split())
            p['project_name'] = re.sub(r'\s+\d+\s*$', '', p['project_name'])
        else:
            p['project_name'] = ''

        # Proposal For
        m = re.search(r'Proposal\s+For\s*:\s*(.+)', block)
        p['proposal_for'] = m.group(1).strip() if m else ''

        # Activity
        m = re.search(r'Activity\s*:\s*(.+?)(?=\n\s*Sector\s*:)', block, re.DOTALL)
        p['activity'] = ' '.join(m.group(1).split()) if m else ''

        # Sector
        m = re.search(r'Sector\s*:\s*(.+)', block)
        p['sector'] = m.group(1).strip() if m else ''

        # --- State ---
        # Strategy 1: State: prefix with value before District:
        # Use LAST match to avoid embedded "State:" inside project names
        m = re.findall(r'State\s*:\s*(.*?)(?=\n\s*District\s*:)', block, re.DOTALL)
        state_raw = m[-1].strip() if m else ''
        # If regex grabbed garbage (has field labels), it matched wrong State:
        if state_raw and re.search(r'(File No|Proposal\s+(?:No|For)|Project Name|State\s*:|District\s*:)', state_raw, re.IGNORECASE):
            state_raw = ''
        # Strategy 2: no State: prefix — scan block for a CHHATTISGARH variant.
        # Handles column-layout PDFs where "State:" is on one page with empty
        # value and the actual state name appears later without prefix.
        # Use LAST match to avoid matching state names embedded in project descriptions.
        if not state_raw:
            all_matches = []
            for v in CHHATTISGARH_VARIANTS:
                for m2 in re.finditer(rf'(?<!\w){re.escape(v)}(?!\w)', block, re.IGNORECASE):
                    all_matches.append(m2)
            if all_matches:
                state_raw = all_matches[-1].group(0).upper()
        p['state'] = state_raw

        # --- District ---
        # Strategy 1: normal District: prefix with value before a date, blank line, or trailing number
        # Negative lookahead prevents capturing other field labels (embedded in project names)
        m = re.findall(r'District\s*:\s*((?:(?!\n\s*(?:State|District)\s*:).)*?)(?=\n\s*(?:\d{2}/\d{2}/\d{4}|\n|\d+\s*(?:\n|$))|$)', block, re.DOTALL)
        district_raw = ' '.join(m[-1].split()) if m else ''
        # Strip trailing standalone numbers (next proposal's sr_no bleeding in)
        if district_raw:
            district_raw = re.sub(r'\s+\d+\s*$', '', district_raw)
        # Strategy 2: District: prefix without date/blank-line lookahead
        if not district_raw:
            m = re.search(r'District\s*:\s*(.+)', block, re.DOTALL)
            if m:
                district_raw = ' '.join(m.group(1).split())
                district_raw = re.sub(r'\s+\d+\s*$', '', district_raw)
        p['district'] = district_raw

        # Meeting date (dd/mm/yyyy) — use last valid date to avoid false matches from file numbers
        dates = re.findall(r'(\d{2}/\d{2}/\d{4})', block)
        valid_dates = [d for d in dates if 1 <= int(d[3:5]) <= 12 and 1 <= int(d[0:2]) <= 31]
        p['meeting_date'] = valid_dates[-1] if valid_dates else (dates[-1] if dates else '')

        # --- Proponent ---
        # Strategy: identify line-ranges belonging to known fields and skip them;
        # everything non-field remaining (after filtering dates/numbers/footers)
        # is the proponent.  This handles column-based PDFs where field values
        # and proponent text interleave across columns.
        block_lines = block.split('\n')
        # First line is the proposal number (already extracted) — skip it
        field_ranges = [(0, 1)]
        i = 1
        while i < len(block_lines):
            s = block_lines[i].strip()
            if any(s.startswith(p) for p in FIELD_PREFIXES):
                start = i
                i += 1
                # Skip continuation lines until next field label or terminator
                while i < len(block_lines):
                    ns = block_lines[i].strip()
                    if not ns:
                        break
                    if re.match(r'^(Page\s+\d+|Government of India|Ministry of Environment)', ns, re.IGNORECASE):
                        i += 1
                        continue
                    if any(ns.startswith(p) for p in FIELD_PREFIXES):
                        break
                    if re.match(r'^\d{2}/\d{2}/\d{4}$', ns):
                        break
                    if re.match(r'^\d+$', ns):
                        break
                    i += 1
                field_ranges.append((start, i))
            else:
                i += 1

        extracted_state = p.get('state', '').lower().strip()
        extracted_district = p.get('district', '').lower().strip()

        proponent_lines = []
        for i, line in enumerate(block_lines):
            if any(start <= i < end for start, end in field_ranges):
                continue
            s = line.strip()
            if not s:
                continue
            if re.match(r'^\d{2}/\d{2}/\d{4}$', s):
                continue
            if re.match(r'^\d+$', s):
                continue
            if re.match(r'^(Page\s+\d+|Government of India|Ministry of Environment)', s, re.IGNORECASE):
                continue
            if s.lower().strip() == extracted_state:
                continue
            if s.lower().strip() == extracted_district:
                continue
            if s.lower().strip() in CHHATTISGARH_VARIANTS:
                continue
            proponent_lines.append(s)

        p['proponent'] = ' '.join(proponent_lines)

        # State filter: only keep if state matches a Chhattisgarh variant
        state = p['state'].lower().replace('\n', ' ').strip()
        if any(v in state for v in CHHATTISGARH_VARIANTS):
            results.append(p)

    return results


def extract_proposals_via_tables(pdf_content: bytes) -> list[dict]:
    '''
    Extract proposals using PyMuPDF's table detection.

    Iterates pages, detects 5-column proposal tables via find_tables(),
    merges continuation rows, and parses field values from each cell.
    Falls back to extract_proposals_from_text if no tables found.
    '''
    doc = fitz.open(stream=pdf_content, filetype="pdf")

    all_rows = []
    for page in doc:
        for table in page.find_tables():
            if table.col_count != 5:
                continue
            cells = table.extract()
            if not any("Proposal No" in str(c) for row in cells for c in row if c):
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

        # State from Location cell
        match = re.search(
            r'State\s*:\s*(.+?)(?=\n\s*District\s*:|\Z)', location, re.DOTALL
        )
        state_raw = match.group(1).strip() if match else ''
        if not state_raw:
            for v in CHHATTISGARH_VARIANTS:
                m2 = re.search(rf'(?<!\w){re.escape(v)}(?!\w)', location, re.IGNORECASE)
                if m2:
                    state_raw = m2.group(0).title()
                    break
        p['state'] = state_raw

        # District from Location cell
        match = re.search(r'District\s*:\s*(.*)', location, re.DOTALL)
        district_raw = ' '.join(match.group(1).split()) if match else ''
        district_raw = re.sub(r'\s+\d+\s*$', '', district_raw)
        p['district'] = district_raw

        # Meeting date
        p['meeting_date'] = m['meeting_date'].strip() if m['meeting_date'] else ''

        # Proponent
        p['proponent'] = ' '.join(m['proponent'].split()).strip()

        # State filter: only keep if state matches a Chhattisgarh variant
        state = p['state'].lower().replace('\n', ' ').strip()
        if any(v in state for v in CHHATTISGARH_VARIANTS):
            results.append(p)

    return results


def _group_blocks_by_row(blocks):
    """Group text blocks into table rows based on overlapping y-ranges."""
    if not blocks:
        return []

    sorted_blocks = sorted(blocks, key=lambda b: b[1])
    groups = []
    current_group = [sorted_blocks[0]]
    current_y1 = sorted_blocks[0][3]

    for b in sorted_blocks[1:]:
        y0, y1 = b[1], b[3]
        if y0 < current_y1:
            current_group.append(b)
            current_y1 = max(current_y1, y1)
        else:
            groups.append(current_group)
            current_group = [b]
            current_y1 = y1

    if current_group:
        groups.append(current_group)
    return groups


def _cut_page_at_new_row(text: str) -> tuple[str, str]:
    """
    Split page text into (continuation, main) at the first new-row marker.
    A new row starts with a standalone serial number or 'Proposal No :'.
    """
    lines = text.split('\n')
    for i, line in enumerate(lines):
        stripped = line.strip()
        if re.match(r'^\d+$', stripped) or stripped.startswith('Proposal No'):
            return '\n'.join(lines[:i]), '\n'.join(lines[i:])
    return '', text


def _has_sr_no(group):
    return any(
        b[4].strip().isdigit() and (b[2] - b[0]) < 80
        for b in group
    )


def merge_page_boundaries(pdf_content: bytes) -> str:
    """
    Detect and merge table rows split across page boundaries.

    Uses page.get_text("blocks") position info to detect whether a page
    starts with continuation blocks (no sr_no at the top).  If so, the
    corresponding lines in the page's get_text() are moved to the end of
    the previous page's text so that split rows remain contiguous.
    """
    doc = fitz.open(stream=pdf_content, filetype="pdf")

    page_texts: list[str] = []

    for page_idx in range(len(doc)):
        page = doc[page_idx]
        text = page.get_text()
        blocks = page.get_text("blocks")

        content = [
            b for b in blocks
            if b[4].strip() and not re.match(r'^Page \d+ of \d+', b[4].strip())
        ]

        if page_idx == 0 or not content:
            page_texts.append(text)
            continue

        groups = _group_blocks_by_row(content)

        if _has_sr_no(groups[0]):
            page_texts.append(text)
        else:
            cont_lines, main_lines = _cut_page_at_new_row(text)
            if cont_lines:
                page_texts[-1] += '\n' + cont_lines
            page_texts.append(main_lines)

    doc.close()
    return '\n'.join(page_texts)


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
        self, rec_id: int, pdfpath: str, meeting_id: str
    ) -> Tuple[int, str, List[str], List[dict], str]:
        """Worker function for threads: Downloads, extracts text, matches keywords, and parses proposals."""
        try:
            time.sleep(0.1 * (rec_id % 10))

            logger.debug(f"Downloading PDF for ID {rec_id}")
            resp = self.session.get(pdfpath, headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
            resp.raise_for_status()

            # Extract with built-in cut-off logic (Any Other Item(s) → Remarks → fallback)
            cleaned_text = extract_agenda_text(resp.content)

            # Keyword matching on the cleaned text
            matched = [kw for kw, pat in self.keyword_patterns.items() if pat.search(cleaned_text.lower())]

            # Parse proposals — try table-based extraction first, fall back to text
            proposals = extract_proposals_via_tables(resp.content) if matched else []
            if not proposals and matched:
                # Merge page-split rows, then apply same text-level cut-offs as
                # extract_agenda_text (Remarks and legacy stop patterns)
                merged = merge_page_boundaries(resp.content)
                for sep in ['Remarks', 'List & Correspondence addresses',
                            'Composition of Expert Appraisal Committee']:
                    idx = merged.lower().find(sep.lower())
                    if idx != -1:
                        merged = merged[:idx]
                proposals = extract_proposals_from_text(merged.strip())
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

        sql = f"SELECT id, pdffilepath, meeting_id FROM {self.table_name} WHERE is_processed = 0 AND ref_type = 'AGENDA'"
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
                    self._download_and_extract_text, r["id"], r["pdffilepath"], r["meeting_id"]
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
