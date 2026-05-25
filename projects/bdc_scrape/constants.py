BASE_URL = "https://bastar.dcourts.gov.in/case-status-search-by-case-type/"
AJAX_URL = "https://bastar.dcourts.gov.in/wp-admin/admin-ajax.php"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive"
}

# AJAX-specific headers representing a jQuery request
AJAX_HEADERS = HEADERS.copy()
AJAX_HEADERS.update({
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Referer": BASE_URL,
    "Origin": "https://bastar.dcourts.gov.in"
})

# Scraping configuration parameters
COURT_COMPLEX_CODE = "CGBA01,CGBA02,CGBA03,CGBA04,CGLC17" # District And Sessions Court Bastar
CASE_TYPE_NIA = "61" # NIA Anexure crime

STATUSES = {
    "Pending": "P",
    "Disposed": "D"
}

YEARS = ["2024", "2025", "2026"]
