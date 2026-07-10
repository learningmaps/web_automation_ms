import fitz
from typing import List

from common.gemini_utils import safe_extract_images
from projects.dantewada_scrape.schemas import DocumentExtraction
from projects.dantewada_scrape.constants import EXTRACTION_PROMPT, PDF_DPI, MAX_IMAGES_PER_REQUEST


def pdf_to_images(pdf_bytes: bytes, dpi: int = PDF_DPI) -> List[bytes]:
    """Convert each page of a PDF to a JPEG image.

    Args:
        pdf_bytes: Raw PDF content.
        dpi: Resolution for the rendered images.

    Returns:
        List of JPEG image bytes, one per page.
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        images = []
        zoom = dpi / 72
        matrix = fitz.Matrix(zoom, zoom)
        for page in doc:
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            jpg_bytes = pix.tobytes("jpeg")
            images.append(jpg_bytes)
        return images
    finally:
        doc.close()


def chunk_images(images: List[bytes], max_per_chunk: int = MAX_IMAGES_PER_REQUEST) -> List[List[bytes]]:
    """Split images into chunks to stay within Gemini's per-request limit."""
    return [images[i : i + max_per_chunk] for i in range(0, len(images), max_per_chunk)]


def extract_from_pdf(pdf_bytes: bytes, source_website: str = "") -> dict:
    """Full pipeline: PDF bytes -> images -> Gemini extraction -> dict.

    Args:
        pdf_bytes: Raw PDF content.
        source_website: Identifier for the source ('dantewada' or 'forest_cg').

    Returns:
        Dictionary with extracted fields matching DocumentExtraction schema.
    """
    images = pdf_to_images(pdf_bytes)
    if not images:
        raise ValueError("PDF produced no images")

    print(f"    Converted PDF to {len(images)} page image(s)")

    chunks = chunk_images(images)
    all_results = []

    for chunk_idx, chunk in enumerate(chunks):
        print(f"    Sending chunk {chunk_idx + 1}/{len(chunks)} ({len(chunk)} images) to Gemini...")
        result = safe_extract_images(
            images=chunk,
            response_model=DocumentExtraction,
            prompt=EXTRACTION_PROMPT,
        )
        all_results.append(result)

    merged = _merge_results(all_results)
    merged["source_website"] = source_website
    return merged


def _merge_results(results: list) -> dict:
    """Merge multiple DocumentExtraction results from page chunks.

    Uses the first non-empty value for scalar fields, and combines
    additional_fields dicts.
    """
    merged = {
        "district": "",
        "date_of_issuance": "",
        "location_of_incident": "",
        "land_hectares": "",
        "village_name": "",
        "notification_reference_number": "",
        "authority_issuing_order": "",
        "purpose": "",
        "project_name": "",
        "applicant_name": "",
        "act_mentioned": "",
        "forest_types_involved": {},
        "khasra_numbers_involved": [],
        "additional_fields": {},
    }

    for r in results:
        if r.district and not merged["district"]:
            merged["district"] = r.district
        if r.date_of_issuance and not merged["date_of_issuance"]:
            merged["date_of_issuance"] = r.date_of_issuance
        if r.location_of_incident and not merged["location_of_incident"]:
            merged["location_of_incident"] = r.location_of_incident
        if r.land_hectares and not merged["land_hectares"]:
            merged["land_hectares"] = r.land_hectares
        if r.village_name and not merged["village_name"]:
            merged["village_name"] = r.village_name
        if r.notification_reference_number and not merged["notification_reference_number"]:
            merged["notification_reference_number"] = r.notification_reference_number
        if r.authority_issuing_order and not merged["authority_issuing_order"]:
            merged["authority_issuing_order"] = r.authority_issuing_order
        if r.purpose and not merged["purpose"]:
            merged["purpose"] = r.purpose
        if r.project_name and not merged["project_name"]:
            merged["project_name"] = r.project_name
        if r.applicant_name and not merged["applicant_name"]:
            merged["applicant_name"] = r.applicant_name
        if r.act_mentioned and not merged["act_mentioned"]:
            merged["act_mentioned"] = r.act_mentioned
        if r.forest_types_involved:
            merged["forest_types_involved"].update(r.forest_types_involved)
        if r.khasra_numbers_involved:
            merged["khasra_numbers_involved"] = list(
                dict.fromkeys(merged["khasra_numbers_involved"] + r.khasra_numbers_involved)
            )
        if r.additional_fields:
            merged["additional_fields"].update(r.additional_fields)

    return merged
