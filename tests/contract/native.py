"""Compare actual production registry and holding-series observation builders."""

import asyncio
import hashlib
from types import SimpleNamespace

from src.data.storage.series_observation_store import SeriesObservationStore
from src.domain import DiseaseSurveillanceSeries, SourceDiseaseCategory
from src.services.disease_mapping_registry_service import _category_key
from tests.contract.compare import assert_observations_match


class RegistryFixtureDB:
    """Provide persisted category identities while exercising the real builder.

    The category identity is calculated by the production discovery helper;
    holding series are created by the production store and retained by this
    transaction-local fixture session.
    """

    def __init__(self):
        self.series = {}

    async def execute(self, statement):
        entity = statement.column_descriptions[0]["entity"]
        params = statement.compile().params
        result = None
        if entity is SourceDiseaseCategory:
            source, code, version = (
                params[key]
                for key in ("source_id_1", "source_code_1", "definition_version_1")
            )
            key = _category_key(source, code, version)
            result = SimpleNamespace(
                id=int(hashlib.sha256(key.encode()).hexdigest()[:8], 16),
                category_key=key,
                source_id=source,
                source_code=code,
                definition_version=version,
                canonical_source_label=code,
                source_definition=None,
                source_definition_uri=None,
            )
        elif entity is DiseaseSurveillanceSeries:
            result = self.series.get(params["series_code_1"])
        else:
            raise AssertionError(f"Unexpected fixture entity {entity}")
        return SimpleNamespace(scalar_one_or_none=lambda: result)

    def add(self, series):
        self.series[series.series_code] = series

    async def flush(self):
        pass


def assert_native_observations_match(actual, expected, updater):
    store = SeriesObservationStore()
    options = {
        "source_id": updater.ontology_source_id,
        "geography_key": getattr(updater, "series_geography_key", None),
    }
    country = getattr(updater, "series_country_code", updater.country_code)

    async def build(rows):
        regular = store.build_observations(rows, country, **options)
        holding, _ = await store._build_holding_observations(
            RegistryFixtureDB(), rows, country, value_field="Cases", **options
        )
        return regular.observations + holding

    before = asyncio.run(build(expected))
    after = asyncio.run(build(actual))
    assert before, "Contract fixture must exercise production observations"
    assert_observations_match(after, before)
