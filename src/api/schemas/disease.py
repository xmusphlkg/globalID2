"""Disease schemas."""

from typing import List, Optional
from pydantic import BaseModel


class DiseaseOut(BaseModel):
    id: int
    name: str
    name_en: Optional[str] = None
    category: str
    icd_10: Optional[str] = None
    icd_11: Optional[str] = None
    aliases: List[str] = []
    keywords: List[str] = []
    is_active: bool = True

    model_config = {"from_attributes": True}


class DiseaseListItem(BaseModel):
    """Lightweight disease item for selectors."""
    code: str
    display_name: str
    display_name_en: Optional[str] = None
