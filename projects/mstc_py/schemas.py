from pydantic import BaseModel, Field
from typing import List, Optional, Dict

# 1. Mine Block Summary Schema
class LandBreakdown(BaseModel):
    forestLandArea: str
    revenueLandArea: str
    private_land_area: str = Field(alias="privateLandArea")
    governmentLandArea: str
    totalAreaHectares: str

class Resources(BaseModel):
    totalResourcesMT: str
    averageGrade: str

class MineBlockSummary(BaseModel):
    blockName: str
    state: str
    district: str
    tehsilTaluka: str
    villages: str
    mineralCommodity: str
    explorationStage: str
    landBreakdown: LandBreakdown
    resources: Resources
    geologicalSetting: str
    toposheetNumber: str
    geographicCoordinates: str

# 2. Notice Inviting Tender (NIT) Schema
class TenderBlock(BaseModel):
    slNo: Optional[str] = None
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
        'prompt': 'Extract detailed geological and land particulars from this Mine Block Summary.'
    },
    'Notice Inviting Tender': {
        'model': NIT,
        'prompt': 'Extract tender dates, fees, and block names from this Notice Inviting Tender (NIT).'
    }
}
