import tempfile
import os
from markitdown import MarkItDown

def convert_pdf_to_markdown(pdf_content: bytes) -> str:
    """
    Standardized PDF to Markdown conversion used across all projects.
    Handles temp file lifecycle and consistent MarkItDown configuration.
    """
    md = MarkItDown()
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(pdf_content)
            temp_path = tmp.name
        
        result = md.convert(temp_path)
        return result.text_content
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)
