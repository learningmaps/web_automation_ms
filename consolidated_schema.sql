-- ─── SCHEMA SETUP ───
CREATE SCHEMA IF NOT EXISTS mstc;
CREATE SCHEMA IF NOT EXISTS parivesh;

-- ─── MSTC SCHEMA TABLES ───

-- 1. Table for tracking all discovered PDFs
CREATE TABLE IF NOT EXISTS mstc.processed_pdfs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    file_id TEXT UNIQUE NOT NULL,
    source_page TEXT NOT NULL, -- 'mine_block_summary' or 'nit'
    pdf_url TEXT NOT NULL,
    discovered_at TIMESTAMPTZ DEFAULT NOW(),
    extracted_at TIMESTAMPTZ, -- Null until processed
    status TEXT DEFAULT 'pending' -- 'pending', 'processed', or 'failed'
);

-- 2. Table for Mine Block Summaries
CREATE TABLE IF NOT EXISTS mstc.mine_block_summaries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pdf_id UUID UNIQUE REFERENCES mstc.processed_pdfs(id) ON DELETE CASCADE,
    block_name TEXT,
    state TEXT,
    district TEXT,
    tehsil_taluka TEXT,
    villages TEXT,
    mineral_commodity TEXT,
    exploration_stage TEXT,
    forest_land_area DECIMAL,
    revenue_land_area DECIMAL,
    private_land_area DECIMAL,
    government_land_area DECIMAL,
    total_area_hectares DECIMAL,
    total_resources_mt DECIMAL,
    average_grade TEXT,
    geological_setting TEXT,
    toposheet_number TEXT,
    geographic_coordinates TEXT
);

-- 3. Table for NITs (Parent)
CREATE TABLE IF NOT EXISTS mstc.tenders_nit (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pdf_id UUID UNIQUE REFERENCES mstc.processed_pdfs(id) ON DELETE CASCADE,
    nit_number TEXT,
    tranche TEXT,
    tender_date DATE,
    bid_submission_deadline TIMESTAMPTZ,
    tender_fee TEXT,
    bid_security_emd TEXT
);

-- 4. Table for Individual Blocks within NITs (Children)
CREATE TABLE IF NOT EXISTS mstc.tender_blocks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    nit_id UUID REFERENCES mstc.tenders_nit(id) ON DELETE CASCADE,
    sl_no TEXT,
    state TEXT,
    district TEXT,
    block_name TEXT,
    mineral TEXT,
    license_type TEXT,
    reserve_price TEXT
);

-- ─── PARIVESH SCHEMA TABLES ───

-- 1. Main Agenda Table
CREATE TABLE IF NOT EXISTS parivesh.agenda_v3 (
    id BIGINT PRIMARY KEY, 
    created_on TEXT, 
    updated_on TEXT,
    created_by INTEGER, 
    updated_by INTEGER, 
    vers TEXT, 
    date TEXT,
    ref_id INTEGER, 
    ref_type TEXT, 
    committee_type TEXT, 
    pdffilepath TEXT,
    workgroup_id INTEGER, 
    meeting_start_date TEXT, 
    meeting_end_date TEXT,
    meeting_id TEXT, 
    subject TEXT, 
    sector TEXT, 
    selected_sector INTEGER,
    sector_name TEXT, 
    state TEXT, 
    statename TEXT, 
    statename_derived TEXT,
    is_active INTEGER, 
    is_deleted INTEGER, 
    is_processed INTEGER DEFAULT 0,
    matched_keywords TEXT, 
    processed_on TEXT, 
    pdf_text TEXT,
    norm_subject TEXT
);

-- ─── SECURITY (RLS) ───

ALTER TABLE mstc.processed_pdfs ENABLE ROW LEVEL SECURITY;
ALTER TABLE mstc.mine_block_summaries ENABLE ROW LEVEL SECURITY;
ALTER TABLE mstc.tenders_nit ENABLE ROW LEVEL SECURITY;
ALTER TABLE mstc.tender_blocks ENABLE ROW LEVEL SECURITY;
ALTER TABLE parivesh.agenda_v3 ENABLE ROW LEVEL SECURITY;

-- Secure Policies: Public can read, Service Role bypasses to write
CREATE POLICY "Public Read Access" ON mstc.processed_pdfs FOR SELECT USING (true);
CREATE POLICY "Public Read Access" ON mstc.mine_block_summaries FOR SELECT USING (true);
CREATE POLICY "Public Read Access" ON mstc.tenders_nit FOR SELECT USING (true);
CREATE POLICY "Public Read Access" ON mstc.tender_blocks FOR SELECT USING (true);
CREATE POLICY "Public Read Access" ON parivesh.agenda_v3 FOR SELECT USING (true);
