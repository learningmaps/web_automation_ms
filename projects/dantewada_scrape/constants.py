import os

# ─── Common Headers ───
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}

# ─── Source: Dantewada District Notifications ───
DANTEWADA_BASE_URL = "https://dantewada.nic.in/en/document-category/notification/"
DANTEWADA_PAGE_URL = "https://dantewada.nic.in/en/document-category/notification/page/{page}/"

# CSS selectors for the HTML table
DANTEWADA_TABLE_SELECTOR = ".distTableContent table tbody tr"
DANTEWADA_PAGINATION_SELECTOR = ".pegination ul li a"
DANTEWADA_PDF_SELECTOR = "td:last-child span.pdf-downloads a"

# ─── Source: Chhattisgarh Forest Department FCA Diversion Cases ───
FOREST_CG_BASE_URL = "https://forest.cg.gov.in/posts/fca-diversion-cases"
FOREST_CG_PDF_SELECTOR = "ul li a[href$='.pdf']"

# ─── Supabase Storage ───
STORAGE_BUCKET = "diversions_and_notifications-pdfs"
STORAGE_PATH_DANTEWADA = "dantewada/{filename}"
STORAGE_PATH_FOREST_CG = "forest_cg/{filename}"

# ─── Database ───
SCHEMA_NAME = "diversions_and_notifications"

# ─── Gemini Extraction Settings ───
PDF_DPI = 200
MAX_IMAGES_PER_REQUEST = 20

EXTRACTION_PROMPT = """You are given pages of a scanned Hindi government document (notification or FCA diversion case letter).
Extract the following fields from the document:

1. **district**: The district name mentioned in the document (e.g., "Dantewada", "Bastar", "Korba"). Use Hindi or English as found. CRITICAL: Strictly exclude sub-district divisions like blocks, tehsils, or villages from this field (e.g. "Ramanujnagar" is a block/tehsil, not a district, and must not be included).
2. **date_of_issuance**: The date the letter/notification was issued. Return in DD/MM/YYYY or YYYY-MM-DD format if possible. If only Hindi date is found, transliterate it.
3. **location_of_incident**: The specific location (village, tehsil, block, etc.) of the incident or event described. Be as specific as possible.
4. **land_hectares**: The area of land involved in hectares. Return the number with unit (e.g., "15.5 ha"). If multiple parcels, list them.
5. **village_name**: The name of the village where the incident or diversion event is taking place. Extract the village name specifically (not tehsil or block).
6. **notification_reference_number**: The official notification or reference number of the document. Extract any file number, case number, or notification ID.
6. **authority_issuing_order**: The authority or officer who issued the order/notification (e.g., Collector, PCCF, Divisional Forest Officer).
7. **purpose**: The purpose of the land acquisition or forest diversion described in the document.
8. **project_name**: The name of the project requiring the land/diversion (e.g., "BharatNet Phase-II"). Use empty string if not a project-specific document.
9. **applicant_name**: The name of the applicant or proponent entity. Use empty string if not applicable.
10. **act_mentioned**: The specific Act or law under which the notification is issued (e.g., "Land Acquisition Act 2013", "Forest Conservation Act 1980").
11. **forest_types_involved**: A JSON object with keys for each forest type classification and values being the area in hectares. Example: {"reserved_forest_land": "5.2 ha", "protected_forest_land": "2.1 ha", "unclassed_forest_land": "0.5 ha"}. Use empty dict {} if not applicable.
12. **khasra_numbers_involved**: A JSON array of khasra/land parcel numbers mentioned. Example: ["123/1", "123/2"]. Use empty array [] if not applicable.
14. **additional_fields**: A JSON object with any other notable fields you identify that don't fit the above categories.

CRITICAL RULES:
- The document is in Hindi. Read the Devanagari script carefully.
- If a field is not found or unclear, use an empty string (or empty dict/array for fields 12, 13).
- For land_hectares, always try to extract a numeric value. Convert units if needed (e.g., acres to hectares: 1 acre ≈ 0.4047 hectares).
- Preserve original Hindi names for places and people.
- Return the result as a JSON object matching the provided schema."""
