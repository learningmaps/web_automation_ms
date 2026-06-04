import fitz

def convert_pdf_to_markdown(pdf_content: bytes) -> str:
    """
    Standardized PDF to text extraction used across all projects.
    Uses PyMuPDF for fast, accurate positional text extraction.
    """
    doc = fitz.open(stream=pdf_content, filetype="pdf")
    try:
        return "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()


def extract_agenda_text(pdf_content: bytes) -> str:
    """
    Extract text from an agenda/meeting PDF, cutting off at the first of:
    1. 'Any Other Item(s)' — coordinate-based positional cut
    2. 'Remarks' — first occurrence in extracted text
    3. Fallback stop patterns (from legacy Parivesh scraper)

    Returns cleaned text containing only the meeting header + list of proposals.
    """
    doc = fitz.open(stream=pdf_content, filetype="pdf")

    # --- Strategy 1: Coordinate-based cut on "Any Other Item(s)" ---
    found_page = found_y = None
    for i, page in enumerate(doc):
        for b in page.get_text("blocks"):
            if "Any Other Item(s)" in b[4]:
                found_page, found_y = i, b[1]
                break
        if found_page is not None:
            break

    if found_page is not None:
        parts = []
        for i, page in enumerate(doc):
            if i < found_page:
                parts.append(page.get_text())
            elif i == found_page:
                blocks = sorted(page.get_text("blocks"), key=lambda b: (b[1], b[0]))
                page_text = ""
                for b in blocks:
                    if b[1] < found_y:
                        page_text += b[4] + "\n"
                parts.append(page_text.rstrip())
            else:
                break
        doc.close()
        text = "\n".join(parts).strip()
        # Secondary trim inside result if "Remarks" still appears
        if "Remarks" in text:
            text = text.split("Remarks")[0].strip()
        return text

    # --- Fallback: extract full text ---
    text = "\n".join(page.get_text() for page in doc)
    doc.close()

    # --- Strategy 2: Cut at first "Remarks" ---
    idx = text.find("Remarks")
    if idx != -1:
        text = text[:idx]

    # --- Strategy 3: Legacy stop patterns ---
    stop_patterns = [
        "List & Correspondence addresses",
        "Composition of Expert Appraisal Committee",
    ]
    lower_text = text.lower()
    stop_idx = len(text)
    for p in stop_patterns:
        idx = lower_text.find(p.lower())
        if idx != -1 and idx < stop_idx:
            stop_idx = idx
    text = text[:stop_idx]

    return text.strip()
