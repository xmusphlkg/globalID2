"""Shared API limits for country and ISO 3166-2 region codes."""

COUNTRY_REGION_CODE_MAX_LENGTH = 10
PUBLIC_COUNTRY_REGION_CODE_DB_PATTERN = r"^[A-Z]{2}(-[A-Z0-9]{1,3})?$"
