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
  - **Fetch**: Triggers the Playwright scraper to find new PDF links from MSTC pages.
  - **Extract**: Triggers a remote GitHub Action to perform LLM-based data extraction.
- **Data Views**:
  - **Scraped URLs**: Tracks the discovery and status of every PDF.
  - **Mine Block Summaries**: Shows extracted geological and land data.
  - **Tenders (NIT)**: Shows auction schedules and individual mineral blocks.

### 2.3 Parivesh Portal (`projects/parivesh_auto/app.py`)
- **Controls**:
  - **Fetch New Documents**: Runs a local sync process to download metadata and PDFs from the Parivesh server.
  - **Refresh View**: Manually refreshes the PostgreSQL Materialized View for updated consolidation.
- **Filters**: Advanced filtering by State, Committee Type, Keywords, and Date ranges.
- **Data Table**: Displays a consolidated view of Agendas and Minutes of Meetings (MOM).

### 2.4 Bastar Court Portal (`projects/bdc_scrape/app.py`)
- **Metrics Bar**: Displays total cases, pending vs disposed, and the "Data Last Synced" timestamp.
- **Controls**:
  - **Sync**: Triggers the remote GitHub Actions scraper workflow asynchronously to fetch the latest cases.
  - **Refresh**: Reloads the dashboard data from Supabase immediately via a streamlit rerun.
- **Data Views**:
  - **Case List**: Displays a paginated, filterable table of court cases.
  - **Case Details Viewer**: Detail view showing full dynamic case history, business/orders, and transfers.

---

## 3. Data Flow: MSTC Mineral Blocks

### Stage 1: Discovery (Scraping)
1. **User Action**: Clicks "Fetch" in the MSTC Portal.
2. **Logic**: `scraper.py` uses Playwright to navigate MSTC listing pages.
3. **Storage**: Discovered PDF URLs are saved into `mstc.processed_pdfs` with status `pending`.

### Stage 2: Extraction (LLM Processing)
1. **User Action**: Clicks "Extract" in the MSTC Portal.
2. **Trigger**: Streamlit calls the GitHub API to dispatch the `extract_pdfs.yml` workflow.
3. **Execution**: The workflow runs `projects/mstc_py/main.py`:
   - **Download**: Downloads the PDF from the stored URL.
   - **Conversion**: `common.document_processing` uses `markitdown` to convert the PDF to Markdown.
   - **Extraction**: `extractor.py` sends the Markdown to **Gemini 3.1 Flash-Lite** with a structured Pydantic schema.
   - **Fallback**: If the primary model fails, it tries `Gemini 2.5 Flash`, then `Gemini 3 Flash-Preview`.
4. **Storage**:
   - Structured data is saved into `mstc.mine_block_summaries` or `mstc.tenders_nit` & `mstc.tender_blocks`.
   - The record in `mstc.processed_pdfs` is updated to `processed` with a timestamp.

---

## 4. Data Flow: Parivesh Monitoring

### Stage 1: Metadata Sync
1. **User Action**: Clicks "Fetch New Documents" in the Parivesh Portal.
2. **Logic**: `utils.py` (`PariveshScraper`) queries Parivesh APIs for recent meeting records (SEIAA, SEAC, EAC).
3. **Storage**: Initial metadata is stored in `parivesh.agenda_v3`.

### Stage 2: Document Processing & Consolidation
1. **PDF Sync**: The scraper downloads the associated Agenda and MOM PDFs.
2. **Keyword Matching**: Subject lines and PDF text are scanned for specific monitoring keywords.
3. **Consolidation**: A PostgreSQL Materialized View (`parivesh.mv_consolidated_projects`) joins related Agendas and MOMs based on meeting IDs.
4. **Visualization**: Streamlit fetches from this view to present a unified project timeline.

---

## 4b. Data Flow: Bastar Court Cases (BDC Scrape)

### Stage 1: Session Initiation & CAPTCHA Solving
1. **Trigger**: Scraper starts (scheduled or manual button click).
2. **HTTP GET**: Fetches the initial page to extract the `scid` token and dynamic token name/value.
3. **CAPTCHA Download**: Fetches the CAPTCHA image using the same session. This sets the `PHPSESSID` cookie.
4. **Gemini Solve**: Sends the CAPTCHA image to **Gemini 3.1 Flash-Lite** to extract the alphanumeric text.

### Stage 2: Search Request & Dynamic Scraping
1. **HTTP POST**: Submits the search parameters (case type, year, status, solved CAPTCHA text, and tokens) to the AJAX endpoint.
2. **Parsing**: Parses the returned HTML table to extract the CNR number (`data-cno`) and establishment code (`data-est-code`) for each matching case.
3. **HTTP Details POST**: Sequentially queries the AJAX details endpoint using the CNR number to fetch the complete HTML case details page (without needing CAPTCHA).

### Stage 3: Database Storage
1. **Extraction**: Parses details HTML into structured fields: Petitioner vs Respondent, Case Stage, Next Hearing Date, Business/Orders.
2. **Supabase Sync**: Inserts or updates records in the `bdc.cases` and `bdc.case_history` tables.

---

---

## 5. Technical Infrastructure

### Database Schema (Supabase/PostgreSQL)
- **Schema `mstc`**:
  - `processed_pdfs`: Master registry of all discovered files.
  - `mine_block_summaries`: Detailed geological/resource data.
  - `tenders_nit` & `tender_blocks`: Auction-related information.
- **Schema `parivesh`**:
  - `agenda_v3`: Flat table containing metadata, keyword matches, and raw text.
  - `mv_consolidated_projects`: Materialized view for cross-referencing documents.
- **Schema `bdc`**:
  - `cases`: Main case details (CNR, Case Type, Case Year, Establishment Code, Petitioner, Respondent, Status, Next Hearing Date).
  - `case_history`: Detailed logs of hearings, stages, and business history.

### Shared Logic
- **`common/document_processing.py`**: Standardized PDF-to-Markdown conversion using the `markitdown` library.
- **`GEMINI.md`**: Project-wide mandates for extraction models, visual identity (Streamlit Red `#ff4b4b`), and directory structure.

### External Integrations
- **GitHub Actions**: Offloads heavy LLM processing tasks to GitHub's infrastructure to avoid Streamlit timeout limits.
- **Google Gemini API**: Provides high-reasoning extraction capabilities with deterministic output (`temperature=0.0`) and solves scraper CAPTCHAs.

## 6. Security Architecture

### Row-Level Security (RLS)
The Supabase database is secured using RLS on all tables in the `mstc`, `parivesh`, and `bdc` schemas.
- **Public Access**: Limited to `SELECT` operations only. This allows the Streamlit dashboards to display data without authentication while preventing unauthorized modifications.
- **Backend Access**: Scrapers and GitHub Actions use the **`service_role`** secret key. This key bypasses RLS, allowing these trusted processes to `INSERT`, `UPDATE`, and `DELETE` records as needed.

### Environment Management
- **Local Development**: Sensitive keys are stored in a `.env` file, which is excluded from source control.
- **GitHub Actions**: The `service_role` key and Supabase URL are managed via GitHub Repository Secrets.
