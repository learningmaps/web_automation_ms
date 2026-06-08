"""Proposal extraction via Gemini for Parivesh agenda PDF text."""
import os
import sys

# Ensure workspace root is on sys.path for common package imports
_projects_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_root_dir = os.path.dirname(_projects_dir)
for _p in (_projects_dir, _root_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from pydantic import BaseModel, Field
from typing import List
from common.gemini_utils import safe_extract_text


class GeminiProposal(BaseModel):
    sr_no: int = Field(description="Serial number of the proposal in the agenda")
    proposal_no: str = ""
    file_no: str = ""
    project_name: str = ""
    proposal_for: str = ""
    activity: str = ""
    sector: str = ""
    state: str = ""
    district: str = ""
    proponent: str = ""
    meeting_date: str = ""


class GeminiProposalList(BaseModel):
    proposals: list[GeminiProposal]


PROPOSAL_EXTRACTION_PROMPT = """You are given the text of an environmental clearance meeting agenda (from India's Parivesh portal).

Extract every proposal listed in the agenda. Each proposal contains fields such as Proposal No, File No, Project Name, Proposal For, Activity, Sector, State, District, Proponent, and Meeting Date.

Return a JSON object with a single key "proposals" containing an array of extracted proposals.

CRITICAL RULES:
1. Only include proposals whose **State field** value (the field explicitly labeled "State:" in the agenda) is Chhattisgarh or a clear variant (Chattisgarh, CG). Do NOT infer the state from the project name, project description, or the district — only use the explicit "State:" field value.
2. For the "state" field, always use the EXACT uppercase value "CHHATTISGARH".
3. If a field value is missing or unclear from the text, use an empty string.
4. Include ALL proposals from Chhattisgarh — do not skip any.
5. The agenda text may use a columnar layout (table format) or paragraph format — handle both."""


def extract_proposals_via_gemini(agenda_text: str) -> list[dict]:
    """Send agenda text to Gemini, return parsed CG-only proposals as dicts.

    Args:
        agenda_text: The full extracted text of a truncated agenda PDF.

    Returns:
        A list of proposal dicts (keys match extracted_proposals table columns).
    """
    result = safe_extract_text(
        text_content=agenda_text,
        response_model=GeminiProposalList,
        prompt=PROPOSAL_EXTRACTION_PROMPT,
        content_label="Agenda text",
    )
    proposals = [p.model_dump() for p in result.proposals]
    for p in proposals:
        p["state"] = p["state"].upper()
    return proposals
