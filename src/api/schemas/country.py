"""Country schemas."""

from typing import Optional
from pydantic import BaseModel


class CountryOut(BaseModel):
    id: int
    code: str
    name: str
    name_en: str
    name_local: Optional[str] = None
    language: str
    timezone: str
    is_active: bool

    model_config = {"from_attributes": True}
