"""Small helpers for normalizing API enum inputs."""

from enum import Enum
from typing import TypeVar

from fastapi import HTTPException

EnumT = TypeVar("EnumT", bound=Enum)


def parse_enum_member(enum_cls: type[EnumT], raw: object, field_name: str) -> EnumT:
    """Accept either enum name or value, case-insensitively."""
    if isinstance(raw, enum_cls):
        return raw

    text = str(raw or "").strip()
    lowered = text.lower()
    for member in enum_cls:
        if lowered in {member.name.lower(), str(member.value).lower()}:
            return member

    allowed = ", ".join(str(member.value) for member in enum_cls)
    raise HTTPException(
        status_code=422,
        detail=f"Invalid {field_name}: {text or '<empty>'}. Allowed values: {allowed}",
    )


def parse_enum_csv(enum_cls: type[EnumT], raw: str | None, field_name: str) -> list[EnumT]:
    if not raw:
        return []

    values = [item.strip() for item in raw.split(",") if item.strip()]
    return [parse_enum_member(enum_cls, value, field_name) for value in values]
