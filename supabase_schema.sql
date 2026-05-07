-- 1. Table for tracking all discovered PDFs
CREATE TABLE processed_pdfs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    file_id TEXT UNIQUE NOT NULL,
    source_page TEXT NOT NULL, -- 'mine_block_summary' or 'nit'
    pdf_url TEXT NOT NULL,
    discovered_at TIMESTAMPTZ DEFAULT NOW(),
    extracted_at TIMESTAMPTZ, -- Null until processed
    status TEXT DEFAULT 'pending' -- 'pending', 'processed', or 'failed'
);

-- 2. Table for Mine Block Summaries (Page 1)
CREATE TABLE mine_block_summaries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pdf_id UUID REFERENCES processed_pdfs(id) ON DELETE CASCADE,
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

-- 3. Table for NITs (Page 2 - Parent)
CREATE TABLE tenders_nit (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pdf_id UUID REFERENCES processed_pdfs(id) ON DELETE CASCADE,
    nit_number TEXT UNIQUE,
    tranche TEXT,
    tender_date DATE,
    bid_submission_deadline TIMESTAMPTZ,
    tender_fee TEXT,
    bid_security_emd TEXT
);

-- 4. Table for Individual Blocks within NITs (Page 2 - Children)
CREATE TABLE tender_blocks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    nit_id UUID REFERENCES tenders_nit(id) ON DELETE CASCADE,
    sl_no TEXT,
    state TEXT,
    district TEXT,
    block_name TEXT,
    mineral TEXT,
    license_type TEXT,
    reserve_price TEXT
);

-- Enable RLS (Optional, but recommended if you use the Supabase UI/API)
ALTER TABLE processed_pdfs ENABLE ROW LEVEL SECURITY;
ALTER TABLE mine_block_summaries ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenders_nit ENABLE ROW LEVEL SECURITY;
ALTER TABLE tender_blocks ENABLE ROW LEVEL SECURITY;

-- Create policy to allow all for now (adjust as needed for security)
CREATE POLICY "Allow all" ON processed_pdfs FOR ALL USING (true);
CREATE POLICY "Allow all" ON mine_block_summaries FOR ALL USING (true);
CREATE POLICY "Allow all" ON tenders_nit FOR ALL USING (true);
CREATE POLICY "Allow all" ON tender_blocks FOR ALL USING (true);
