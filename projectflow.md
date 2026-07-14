# Project Flow: Web Automation System

This document provides a comprehensive overview of the architecture, user interface, and data flow of the Web Automation project.

## 1. High-Level Architecture

The system is a multi-project automation hub built with **Streamlit**, **Playwright**, **Gemini LLM**, and **Supabase (PostgreSQL)**.

```mermaid
graph TD
    User((User)) --> Hub[Main Hub: main_app.py]
    
    subgraph MSTC Project
        Hub --> MSTC_UI[MSTC Portal]
        MSTC_UI --> Scraper[Playwright Scraper]
        Scraper --> DB[(Supabase)]
        MSTC_UI --> GH_Action[GitHub Action]
        GH_Action --> Extractor[LLM Extractor]
        Extractor --> MarkItDown[MarkItDown PDF-to-MD]
        MarkItDown --> Gemini[Gemini 3.1 Flash-Lite]
        Gemini --> DB
    end
    
    subgraph Parivesh Project
        Hub --> Parivesh_UI[Parivesh Portal]
        Parivesh_UI --> Parivesh_Sync[Local Scraper/Sync]
        Parivesh_Sync --> DB
        Parivesh_UI --> Parivesh_GH[GitHub Action]
        Parivesh_GH --> Parivesh_Run[run_scraper.py]
        Parivesh_Run --> DB
        DB --> MatView[Materialized View]
        MatView --> Parivesh_UI
    end

    subgraph Bastar Court Project
        Hub --> BDC_UI[Bastar Court Portal]
        BDC_UI --> BDC_Sync[HTTP Scraper & Sync]
        BDC_Sync --> Gemini_Captcha[Gemini CAPTCHA Solver]
        BDC_Sync --> DB
        DB --> BDC_UI
    end

    subgraph Dantewada & Forest CG Project
        Hub --> Dantewada_UI[Dantewada & Forest CG Portal]
        Dantewada_UI --> Dantewada_Sync[Local/CLI Scraper]
        Dantewada_Sync --> Proxy_Fetch[Supabase SQL Proxy]
        Dantewada_Sync --> Direct_Fetch[Direct HTTP Download]
        Dantewada_Sync --> Gemini_Img[Gemini Image Extractor]
        Gemini_Img --> DB
        DB --> Dantewada_UI
    end
```

---

## 2. User Interface Flow

### 2.1 Unified Hub (`main_app.py`)
- **Entry Point**: The landing page displays high-level metrics (Total blocks, processed PDFs, keyword hits, court cases) fetched from both `mstc`, `parivesh`, and `bdc` schemas.
- **Navigation**: Three primary cards allow the user to launch specific project portals:
  - **MSTC Mineral Blocks**: Structured extraction from auction notices.
  - **Parivesh Monitoring**: Environmental clearance tracking.
  - **Bastar Court Cases**: Case status monitoring for Bastar District Court.

### 2.2 MSTC Portal (`projects/mstc_py/app.py`)
- **Metrics Bar**: Displays real-time counts of `Pending`, `Processed`, and `Failed` PDFs.
- **Controls**:
  - **Local Fetch**: Triggers the local Playwright scraper to find new PDF links from MSTC pages and save them to the DB.
  - **Run Pipeline**: Triggers a remote GitHub Action pipeline that runs both the Crawler and the Extractor sequentially.
  - **Extract Only**: Triggers a remote GitHub Action pipeline to only run the Extractor on existing pending PDFs.
- **Data Views**:
  - **Scraped URLs**: Tracks the discovery and status of every PDF.
  - **Mine Block Summaries**: Shows extracted geological and land data.
  - **Tenders (NIT)**: Shows auction schedules and individual mineral blocks.
  - **Corrigendum & Addendum**: Shows document-level corrections with block-level changes.

### 2.3 Parivesh Portal (`projects/parivesh_auto/app.py`)
- **Controls**:
  - **Fetch New Documents**: Runs a local sync process to download metadata and PDFs from the Parivesh server.
  - **Refresh View**: Manually refreshes the PostgreSQL Materialized View for updated consolidation.
- **Agenda Filters**:
  - Committee (multiselect), Meeting Date Range, Meeting Start Date Range, Processed On Date Range
  - MOM Status (All/With/Without), Subject Search, Sector, State
- **Proposal Filters**:
  - State, Sector, Proposal For, District (multiselect dropdowns)
  - Proponent text search, Proposal No text search
- **Metrics Row**: Agendas count, With MOM count, Unprocessed count, Keyword Matches.
- **Export Button** (`⬇ Excel`): Positioned directly above the agenda table for quick access.
- **Agenda Table**: 10 columns — Date, Meeting Start, Meeting ID, Committee, Subject (truncated 80ch), Sector, State, MOM (✓/✗), Keywords, Processed On.
- **Master-Detail Workflow**: Selecting a row shows Agenda Details, Minutes of Meeting (if linked), and Proposal cards below.
- **Excel Export (two sheets)**:
  - **Sheet "Proposal Details"**: Denormalized — one row per proposal with agenda context and linked MOM info merged in.
  - **Sheet "Agendas Summary"**: One row per agenda with proposal count and MOM status.

### 2.4 Bastar Court Portal (`projects/bdc_scrape/app.py`)
- **Metrics Bar**: Displays total cases, pending vs disposed, and the "Data Last Synced" timestamp.
- **Controls**:
  - **Sync**: Triggers the remote GitHub Actions scraper workflow asynchronously to fetch the latest cases.
  - **Refresh**: Reloads the dashboard data from Supabase immediately via a streamlit rerun.
- **Data Views**:
  - **Case List**: Displays a filterable table of court cases. Supports interactive row selection (`on_select="rerun"`) to dynamically load details of the selected case below.
  - **Case Details Viewer**: Continuous vertical dossier docket showing overview, parties, acts & sections, FIR details, hearing history, and PDF order tables in stacked visual cards (enabling seamless page-level Ctrl+F text searches).

### 2.5 Dantewada & Forest CG Portal (`projects/dantewada_scrape/app.py`)
- **Extracted Documents Tab (Primary)**:
  - **Metrics Bar**: Displays Total Documents, Total Area (Hectares) parsed from land hectares text, Districts Involved, and Villages Involved.
  - **Filters Expanders**: Multiselect dropdowns for Sources, Districts, and Villages. Handles filtering for comma-separated items.
  - **Data Table**: Displays extracted columns such as district, village, land hectares, purpose, and PDF links.
- **Scraped PDFs Tab (Secondary)**:
  - **Metrics Bar**: Displays Total, Processed, Pending, and Failed PDFs.
  - **Filter**: Dropdown to filter by Source Website.
  - **Data Table**: Tracks URL, title, discovery date, and processing status.

---

## 3. Data Flow: MSTC Mineral Blocks

### Stage 1: Discovery (Scraping)
1. **User Action**: Clicks "Local Fetch" in the MSTC Portal, or triggers a remote run via "Run Pipeline" on GitHub Actions.
2. **Logic**: `scraper.py` queries MSTC listing pages.
3. **Storage**: Discovered PDF URLs are saved into `mstc.processed_pdfs` with status `pending`.

### Stage 2: Extraction (LLM Processing)
1. **User Action**: Clicks "Run Pipeline" (triggers Crawl then Extract) or "Extract Only" in the MSTC Portal, or execution occurs via weekly automated schedule.
2. **Trigger**: Streamlit calls the GitHub API to dispatch the `extract_pdfs.yml` workflow with either `task: "both"` or `task: "extract"`.
3. **Execution**: The workflow runs `projects/mstc_py/main.py`:
   - **Download**: Downloads the PDF from the stored URL.
    - **Conversion**: `common.document_processing` uses `PyMuPDF` to extract text from the PDF.
   - **Extraction**: `extractor.py` sends the Markdown to **Gemini 3.1 Flash-Lite** with a structured Pydantic schema.
   - **Fallback**: If the primary model fails, it tries `Gemini 2.5 Flash`, then `Gemini 3 Flash-Preview`.
4. **Storage**:
   - Structured data is saved into `mstc.mine_block_summaries` or `mstc.tenders_nit` & `mstc.tender_blocks`.
   - The record in `mstc.processed_pdfs` is updated to `processed` with a timestamp.

---

## 4. Data Flow: Parivesh Monitoring

### Stage 1: Metadata Sync & PDF Extraction (Trigger)
1. **Manual / Scheduled Action**: A weekly GitHub Action cron job (`parivesh_scrape.yml` at 03:00 UTC Mondays) or manual trigger runs `run_scraper.py`. Optionally, the user can click "Fetch New Documents" in the Streamlit Parivesh Portal for local execution.
2. **Logic**: `run_scraper.py` (calling `PariveshScraper` in `utils.py`) queries Parivesh APIs for recent meeting records (SEIAA, SEAC, EAC).
3. **Storage**: Initial metadata is stored in `parivesh.agenda_v3`.

### Stage 2: Document Processing & Consolidation
1. **PDF Sync**: The scraper downloads the associated Agenda and MOM PDFs and uploads them to Supabase Storage (`parivesh-pdfs` bucket).
2. **Keyword Matching**: Subject lines and PDF text are extracted using `PyMuPDF` and scanned for Chhattisgarh-specific monitoring keywords (with multiple spelling variants for each district).
3. **Proposal Extraction**: A two-tier strategy extracts proposal details from agenda PDFs — table-based extraction via `PyMuPDF` `find_tables()`, with **Gemini LLM** fallback for complex layouts. Results are stored in `parivesh.extracted_proposals`.
4. **Consolidation**: A PostgreSQL Materialized View (`parivesh.mv_consolidated_projects`) joins related Agendas and MOMs based on `norm_subject`.
5. **Visualization**: Streamlit fetches from `agenda_v3` and `extracted_proposals` to present a unified master-detail view with exportable Excel reports.

---

## 4b. Data Flow: Bastar Court Cases (BDC Scrape)

### Stage 1: Geoblocking Bypass & HTTP Proxying
To bypass the eCourts Web Application Firewall (WAF) which geoblocks cloud services and public proxies, all scraper HTTP requests are routed through a trusted Indian database server:
1. **Supabase SQL Session (`SupabaseSQLSession`)**: A drop-in custom `requests`-compatible session class.
2. **PostgreSQL Proxy Relay**: Outgoing GET/POST requests are serialized and executed inside PostgreSQL on an AWS Mumbai (`ap-south-1`) instance using the `http` extension.
3. **Binary Handling**: CAPTCHA images and order PDFs are downloaded as binary data through PostgreSQL using `textsend(content)` to prevent null-byte truncation.
4. **Timeouts**: Safe database query connection and execution timeouts (`statement_timeout`, `http.timeout_msec`) prevent hanging on network latency.

### Stage 2: Session Initiation & CAPTCHA Solving
1. **Trigger**: Scraper starts (scheduled or manual button click).
2. **HTTP GET**: Fetches the initial search page via the SQL proxy to extract form tokens (`scid`, `tok_*`).
3. **CAPTCHA Download**: Downloads the CAPTCHA image via the SQL proxy (retaining session cookies in python memory).
4. **Gemini Solve**: Sends the CAPTCHA image data to **Gemini 3.1 Flash-Lite** to solve the text.
5. **Retry Loop**: If verification fails due to an incorrect CAPTCHA code, the scraper re-downloads and re-solves only the CAPTCHA image. It reuses the original search tokens and PHP session cookie, completely bypassing any base page reload.

### Stage 3: Search Request & Dynamic Scraping
1. **HTTP POST**: Submits case search parameters (case type, year, status, solved CAPTCHA text, tokens) to the AJAX search endpoint via the SQL proxy.
2. **Parsing**: Parses the returned HTML to identify CNR numbers (`data-cno`) and establishment codes (`data-est-code`).
3. **HTTP Details POST**: Sequentially queries the details AJAX endpoint via the SQL proxy to fetch the HTML content of each case.
4. **Order PDF Sync**: Checks existing records in `bdc.case_orders` for the given CNR. For each order in the current case, if it was already synced (matching order date), the scraper reuses the S3 URL. Only new/unsynced orders are downloaded through the SQL proxy and uploaded to Supabase Storage, dramatically reducing execution time and proxy overhead.
5. **Print Layout PDF**: Headless Playwright renders the HTML locally to generate an A4 details PDF. To bypass WAF geoblocking of styles and image assets (national emblem, logos, etc.), the generator:
    - Loads a static website template `page_template.html` (constructed from the court's outer page skeleton).
    - Injects the compiled stylesheet (`court_styles.css`) and dynamic case HTML (`details_html`) into placeholders.
    - Dynamically injects a `<base>` tag (pointing to the workspace root) so the browser loads pre-downloaded local logo assets (`projects/bdc_scrape/assets/`).
    - Emulates `screen` media query (`emulate_media(media="screen")`) in Playwright before printing, ensuring all card borders, backgrounds, colors, and layout widths render exactly like the live website view.

### Stage 4: Database Storage
1. **Extraction**: Parses details HTML into structured tables: Case Details, Status, Petitioners, Respondents, Acts, and Hearing History.
2. **Supabase Sync**: Inserts or updates records in `bdc.cases`, `bdc.case_history`, and `bdc.case_orders` schemas.

---

## 4c. Data Flow: Dantewada & Forest CG Scrape

### Stage 1: Discovery (Link Scraping)
1. **Trigger**: Scraper CLI executes the discovery command (`--mode discover`).
2. **Scraping**:
   - **Dantewada Notifications**: Fetches direct pages of the district notifications portal.
   - **Forest CG FCA Cases**: Scrapes the forest department list. Uses the Supabase India SQL proxy with automatic direct HTTP fallback if the database server is blocked.
3. **Database registry**: Discovered links are upserted into `diversions_and_notifications.processed_pdfs` with a status of `pending`.

### Stage 2: PDF Download & Image Rendering
1. **Trigger**: Crawler pipeline runs in extraction mode (`--mode extract`).
2. **Download**: Downloads raw PDF bytes (utilizing proxy with direct fallback).
3. **Image Conversion**: Converts every page of the PDF into high-resolution JPEG images (at 200 DPI) using `PyMuPDF` (`fitz`).

### Stage 3: LLM Multimodal Extraction
1. **Chunking**: Page images are chunked into sets of 20 to comply with API limits.
2. **Gemini Call**: Images are sent to **Gemini 3.1 Flash-Lite** (with fallback to **Gemini 2.5 Flash**) using a structured Pydantic response schema (`DocumentExtraction`).
3. **Information Extraction**:
   - **District**: Extracted with a strict prompt instruction ensuring sub-district divisions (blocks, tehsils like *Ramanujnagar*, or villages) are excluded.
   - **Forest Land Classifications**: Parsed and returned as standardized English key mappings (e.g. `reserved_forest_land`, `protected_forest_land`) regardless of Hindi document terms.
4. **Merge**: Aggregates properties extracted across multi-page chunks.

### Stage 4: Storage & Sync
1. **Database Save**: Saves the consolidated data into `diversions_and_notifications.documents`.
2. **Storage Upload**: Uploads the PDF to Supabase Storage in the `diversions_and_notifications-pdfs` bucket and records the public URL.
3. **Final Mark**: Marks the PDF status as `processed` in `processed_pdfs`.

---

## 5. Technical Infrastructure

### Database Schema (Supabase/PostgreSQL)
- **Schema `mstc`**:
  - `processed_pdfs`: Master registry of all discovered files.
  - `mine_block_summaries`: Detailed geological/resource data.
  - `tenders_nit` & `tender_blocks`: Auction information.
  - `corrigendum_addendum` & `corrigendum_blocks`: Corrigendum documents and their block-level changes.
- **Schema `parivesh`**:
  - `agenda_v3`: Flat table containing metadata (ref_type `AGENDA` or `MOM`), keyword matches, extracted text, PDF storage URLs, and normalized subjects for cross-linking.
  - `extracted_proposals`: Proposals extracted from agenda PDFs, linked to `agenda_v3(id)` via `agenda_id`.
  - `mv_consolidated_projects`: Materialized view joining agendas and MOMs by `norm_subject`.
- **Schema `bdc`**:
  - `cases`: Main case details (CNR, Case Type, Case Year, Petitioner, Respondent, Status, Next Hearing Date).
  - `case_history`: Hearing logs with judge, purpose, and business text.
  - `case_orders`: PDF interim/final orders with storage paths.
- **Schema `diversions_and_notifications`**:
  - `processed_pdfs`: Registry of all discovered Dantewada / Forest CG PDF files, tracking scraping progress, source website, and storage URLs.
  - `documents`: Extracted structured data from notifications (district, date, land area, act, khasra numbers, forest types).

### Shared Logic
- **`common/document_processing.py`**: Standardized PDF-to-text extraction using `PyMuPDF`.
- **`common/gemini_utils.py`**: Shared Gemini LLM extraction with model fallback and key rotation.
- **`common/storage_utils.py`**: Shared Supabase Storage upload utility.

### External Integrations
- **GitHub Actions**: Offloads heavy scraper metadata fetching and PDF processing tasks to GitHub's infrastructure (MSTC, Bastar Court, and Parivesh scrapers) to avoid Streamlit timeout limits.
- **Google Gemini API**: Provides high-reasoning extraction capabilities with deterministic output (`temperature=0.0`) and solves scraper CAPTCHAs.
- **Supabase Storage**: Three buckets (`parivesh-pdfs`, `mstc-pdfs`, `court-documents`) for storing scraped PDFs with public URLs.

### Verification Utilities
- **`supabase/functions/test-waf`**: A utility Edge Function designed to test direct HTTP requests from Supabase cloud environments to the Bastar Court website to verify WAF geoblocking behaviour.

### opencode Project Configuration
- **`opencode.json`**: Registers `AGENTS.md` as project-wide instructions for opencode's LLM agent.
- **`AGENTS.md`**: Documents column name mappings (e.g., `subject` → `raw_subject`) and export sheet field lists to prevent naming mistakes when editing dashboard code.

## 6. Security Architecture

### Row-Level Security (RLS)
The Supabase database is secured using RLS on all tables in the `mstc`, `parivesh`, and `bdc` schemas.
- **Public Access**: Limited to `SELECT` operations only. This allows the Streamlit dashboards to display data without authentication while preventing unauthorized modifications.
- **Backend Access**: Scrapers and GitHub Actions use the **`service_role`** secret key. This key bypasses RLS, allowing these trusted processes to `INSERT`, `UPDATE`, and `DELETE` records as needed.

### Environment Management
- **Local Development**: Sensitive keys are stored in a `.env` file, which is excluded from source control.
- **GitHub Actions**: The `service_role` key and Supabase URL are managed via GitHub Repository Secrets.

---

## 7. Python Imports & Search Path Architecture

In a persistent multi-app monorepo environment (like Streamlit Cloud), the system isolates sub-project imports using absolute package paths rather than dynamic runtime search path modification (`sys.path.append(current_dir)`).

### Search Path Setup
1. **Unified Hub**: The root launcher `main_app.py` appends the `projects/` directory to `sys.path`. This enables importing sub-project dashboards as top-level modules (e.g., `from mstc_py.app import run_mstc`).
2. **Sub-projects**: Each sub-project's entry points (`app.py`, `main.py`, etc.) dynamically insert the parent `projects/` directory to `sys.path` (rather than their own sub-folder directory).
3. **Module Resolution**: All internal module imports within sub-projects specify the package prefix (e.g., `from mstc_py.scraper import ...` or `from parivesh_auto.constants import ...`). This prevents Python's `sys.modules` cache from encountering namespace collisions for duplicate filenames (like `scraper.py`, `constants.py`, and `utils.py`).

