"""Explicit SQLite representation of the real PostgreSQL publication schema.

Only the test metadata converts PostgreSQL arrays to SQLite JSON arrays and
equivalent array-length functions. Production schema requirements stay strict.
"""

from sqlalchemy import JSON, CheckConstraint, MetaData, text
from sqlalchemy.dialects.postgresql import ARRAY

from src.domain import Base

METADATA = MetaData()
for name in (
    "countries",
    "standard_diseases",
    "public_disease_series",
    "country_publish_state",
):
    table = Base.metadata.tables[name].to_metadata(METADATA)
    for column in table.columns:
        if isinstance(column.type, ARRAY):
            column.type = JSON()
    for constraint in table.constraints:
        if isinstance(constraint, CheckConstraint):
            constraint.sqltext = text(
                str(constraint.sqltext)
                .replace("cardinality(", "json_array_length(")
                .replace("char_length(", "length(")
            )

PUBLIC_SERIES = METADATA.tables["public_disease_series"]
PUBLISH_STATE = METADATA.tables["country_publish_state"]


def create_publication_tables(connection):
    METADATA.create_all(connection, tables=[PUBLIC_SERIES, PUBLISH_STATE])
