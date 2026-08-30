"""Country schemas."""

from typing import Optional
from pydantic import BaseModel


class CountryOut(BaseModel):
    id: int
    code: str
    name: str
    name_en: str
    name_zh: Optional[str] = None
    name_local: Optional[str] = None
    language: str
    timezone: str
    is_active: bool
    location_type: str = "country"
    parent_code: Optional[str] = None

    model_config = {"from_attributes": True}
