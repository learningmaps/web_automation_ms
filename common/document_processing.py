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



