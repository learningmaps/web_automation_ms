from pydantic import BaseModel, Field
from typing import List, Optional, Dict

# 1. Mine Block Summary Schema
class LandBreakdown(BaseModel):
    forestLandArea: str = Field(description="Area of forest land in hectares. Use 'Not specified' if not found.")
    revenueLandArea: str = Field(description="Area of revenue land in hectares. Use 'Not specified' if not found.")
    private_land_area: str = Field(alias="privateLandArea", description="Area of private land in hectares. Use 'Not specified' if not found.")
    governmentLandArea: str = Field(description="Area of government land in hectares. Use 'Not specified' if not found.")
    totalAreaHectares: str = Field(description="The total area of the block in hectares.")

class Resources(BaseModel):
    totalResourcesMT: str = Field(description="Total estimated resources in Million Tonnes (MT).")
    averageGrade: str = Field(description="The average grade of the mineral (e.g., % of Al2O3, Fe, etc.).")

class MineBlockSummary(BaseModel):
    blockName: str = Field(description="The formal name of the mineral block.")
    state: str = Field(description="The state where the block is located.")
    district: str = Field(description="The district(s) where the block is located.")
    tehsilTaluka: str = Field(description="The Tehsil or Taluka mentioned in the summary.")
    villages: str = Field(description="Comma-separated list of all villages mentioned.")
    mineralCommodity: str = Field(description="The primary mineral(s) found in the block.")
    explorationStage: str = Field(description="The stage of exploration (e.g., G2, G3, G4).")
    landBreakdown: LandBreakdown
    resources: Resources
    geologicalSetting: str = Field(description="Brief description of the geological setting or mineralized zone.")
    toposheetNumber: str = Field(description="The Survey of India toposheet number.")
    geographicCoordinates: str = Field(description="The boundary coordinates (latitude/longitude) of the block.")

# 2. Notice Inviting Tender (NIT) Schema
class TenderBlock(BaseModel):
    slNo: Optional[str] = Field(description="Serial number if available")
    state: str
    district: str = Field(description="The district where the block is located")
    blockName: str
    mineral: str
    licenseType: str = Field(description="Mining Lease (ML) or Composite Licence (CL)")
    reservePrice: str = Field(description="The reserve price or percentage specified in the table")

class NIT(BaseModel):
    nitNumber: str = Field(description="The reference number of the NIT")
    tranche: str = Field(description="The round or phase of the auction (e.g., Tranche VII)")
    tenderDate: str = Field(description="Date of issue of the tender")
    bidSubmissionDeadline: str = Field(description="Last date and time for bid submission")
    tenderFee: str = Field(description="Non-refundable tender document fee")
    bidSecurityEMD: str = Field(description="Earnest Money Deposit / Bid Security amount")
    blocks: List[TenderBlock] = Field(description="The complete list of mineral blocks listed in the auction table")

# Mapping object for the extractor
PAGE_SCHEMA_MAP = {
    'Mine Block Summary': {
        'model': MineBlockSummary,
        'prompt': 'Extract detailed geological and land particulars. For "geographicCoordinates", prioritize the "Annexure" or the table showing "Corner Points" (Points A, B, C, etc.). If only a range is available, use that, but corner points are preferred. Ensure the "toposheetNumber" is extracted from the "Location" or "General Information" section (e.g., SOI Toposheet Number).'
    },
    'Notice Inviting Tender': {
        'model': NIT,
        'prompt': 'Extract tender details including dates, fees, and the full list of mineral blocks listed in the document. Ensure all columns in the block table are captured correctly.'
    }
}
