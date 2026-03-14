"""Common schemas: pagination, sorting, standard responses."""

from datetime import datetime
from typing import Generic, List, Optional, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PaginationParams(BaseModel):
    page: int = Field(1, ge=1, description="Page number")
    per_page: int = Field(50, ge=1, le=500, description="Items per page")

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.per_page


class PaginatedResponse(BaseModel, Generic[T]):
    data: List[T]
    total: int
    page: int
    per_page: int
    pages: int


class ErrorResponse(BaseModel):
    detail: str
    code: Optional[str] = None
