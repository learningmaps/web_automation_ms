from pydantic import BaseModel, Field
from typing import Any


class DocumentExtraction(BaseModel):
    district: str = Field(
        default="",
        description="The district name mentioned in the document (e.g., Dantewada, Bastar, Korba). Strictly exclude sub-district divisions like blocks, tehsils, or villages.",
    )
    date_of_issuance: str = Field(
        default="",
        description="The date the letter/notification was issued. Use DD/MM/YYYY or YYYY-MM-DD format.",
    )
    village_name: str = Field(
        default="",
        description="The name of the village where the incident or diversion event is taking place.",
    )
    location_of_incident: str = Field(
        default="",
        description="The specific location (village, tehsil, block) of the incident or event described in the document.",
    )
    land_hectares: str = Field(
        default="",
        description="The area of land involved, in hectares. Return the number with unit (e.g., '15.5 ha').",
    )
    notification_reference_number: str = Field(
        default="",
        description="The official notification or reference number of the document.",
    )
    authority_issuing_order: str = Field(
        default="",
        description="The authority or officer who issued the order/notification (e.g., Collector, PCCF).",
    )
    purpose: str = Field(
        default="",
        description="The purpose of the land acquisition or forest diversion described in the document.",
    )
    project_name: str = Field(
        default="",
        description="The name of the project requiring the land/diversion (e.g., BharatNet Phase-II). Empty if not a project-specific document.",
    )
    applicant_name: str = Field(
        default="",
        description="The name of the applicant or proponent entity. Empty if not applicable.",
    )
    act_mentioned: str = Field(
        default="",
        description="The specific Act or law under which the notification is issued (e.g., Land Acquisition Act 2013, Forest Conservation Act 1980).",
    )
    forest_types_involved: dict = Field(
        default_factory=dict,
        description="Breakdown of forest land by classification with area in hectares. Keys like 'reserved_forest_land', 'protected_forest_land', etc. Empty dict if not applicable.",
    )
    khasra_numbers_involved: list = Field(
        default_factory=list,
        description="List of khasra/land parcel numbers mentioned in the document. Empty list if not applicable.",
    )
    additional_fields: dict = Field(
        default_factory=dict,
        description="Any other notable fields identified in the document that don't fit the above categories.",
    )
