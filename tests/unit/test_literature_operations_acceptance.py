import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.control_plane.schedule_state import schedule_state_repository
from src.core.config import LiteratureSettings
from src.core.task_manager import task_manager
from src.domain import (
    Base,
    Country,
    LiteratureArticle,
    LiteratureCountryLink,
    LiteratureDiseaseLink,
    LiteratureIngestRun,
    LiteratureStatusEvent,
    LiteratureTopicLink,
    StandardDisease,
    Task,
    TaskStatus,
)
from src.literature.clients.crossref import CrossrefClient
from src.services import task_executor
from src.services.data_release_service import data_release_service
from src.services.literature_service import LiteratureScheduleState, literature_service


ROOT = Path(__file__).resolve().parents[2]
_ISSN_RE = re.compile(r"^\d{4}-\d{3}[\dX]$")


def test_literature_journal_catalogue_has_31_unique_valid_issns():
    payload = json.loads((ROOT / "configs/literature/journals.json").read_text(encoding="utf-8"))
    journals = payload["journals"]
    issns = [str(item["issn"]).strip().upper() for item in journals]

    assert payload["schema_version"] == 1
    assert len(journals) == 31
    assert len(set(issns)) == len(issns)
    assert all(item.get("name", "").strip() for item in journals)
    assert all(_ISSN_RE.fullmatch(issn) for issn in issns)
    for issn in issns:
        compact = issn.replace("-", "")
        checksum = (11 - sum(int(value) * weight for value, weight in zip(compact[:7], range(8, 1, -1))) % 11) % 11
        expected = "X" if checksum == 10 else str(checksum)
        assert compact[-1] == expected, f"Invalid ISSN checksum: {issn}"


class _AsyncSqliteSession:
    def __init__(self, session: Session, engine) -> None:
        self._session = session
        self.bind = engine

    async def execute(self, statement):
        return self._session.execute(statement)

    async def get(self, model, identity):
        return self._session.get(model, identity)

    async def commit(self) -> None:
        self._session.commit()

    async def rollback(self) -> None:
        self._session.rollback()

    async def flush(self) -> None:
        self._session.flush()

    async def refresh(self, value) -> None:
        self._session.refresh(value)

    def add(self, value) -> None:
        self._session.add(value)

    def add_all(self, values) -> None:
        self._session.add_all(values)


class _AsyncSqliteContext:
    def __init__(self, session_factory, engine) -> None:
        self._session_factory = session_factory
        self._engine = engine
        self._session = None

    async def __aenter__(self):
        self._session = self._session_factory()
        return _AsyncSqliteSession(self._session, self._engine)

    async def __aexit__(self, exc_type, _exc, _traceback):
        assert self._session is not None
        if exc_type is None:
            self._session.commit()
        else:
            self._session.rollback()
        self._session.close()
        return False


class _AsyncSqliteFactory:
    def __init__(self, engine) -> None:
        self.engine = engine
        self.session_factory = sessionmaker(engine, expire_on_commit=False)

    def __call__(self):
        return _AsyncSqliteContext(self.session_factory, self.engine)


def _crossref_record(doi: str, suffix: str) -> dict:
    return {
        "DOI": doi,
        "title": [f"Dengue surveillance in Japan {suffix}"],
        "container-title": ["Acceptance Journal"],
        "ISSN": ["1111-1111"],
        "type": "journal-article",
        "published-online": {"date-parts": [[2026, 8, 12]]},
        "indexed": {"date-time": "2026-08-13T00:00:00Z"},
        "abstract": "Dengue surveillance evidence from Japan.",
    }


class _AcceptanceCrossrefClient(CrossrefClient):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        article_a = _crossref_record("10.1000/acceptance-a", "A")
        article_b = _crossref_record("10.1000/acceptance-b", "B")
        self.records = {
            "1111-1111": [article_a, article_b],
            "2222-2222": [article_a],
        }

    async def get_json(self, _client, path, *, params):
        issn = path.split("/")[2]
        records = self.records[issn]
        offset = 0 if params["cursor"] == "*" else int(params["cursor"])
        items = records[offset : offset + int(params["rows"])]
        return {
            "message": {
                "items": items,
                "next-cursor": str(offset + len(items)),
                "total-results": len(records),
            }
        }


def _suffix_number(doi: str) -> str:
    return "1" if doi.endswith("-a") else "2"


class _AcceptanceEuropePmcClient:
    def __init__(self, **_kwargs) -> None:
        pass

    async def enrich_by_dois(self, dois):
        return {
            doi: {
                "doi": doi,
                "pmid": f"100{_suffix_number(doi)}",
                "pmcid": f"PMC100{_suffix_number(doi)}",
                "isOpenAccess": "Y",
                "abstractText": f"Europe PMC abstract for {doi}",
            }
            for doi in dois
        }


class _AcceptanceUnpaywallClient:
    def __init__(self, **_kwargs) -> None:
        pass

    async def enrich_by_dois(self, dois, **_kwargs):
        return {
            doi: {
                "doi": doi,
                "is_oa": True,
                "best_oa_location": {
                    "url": f"https://unpaywall.example.org/{_suffix_number(doi)}",
                },
            }
            for doi in dois
        }


class _AcceptanceOpenAlexClient:
    def __init__(self, **_kwargs) -> None:
        pass

    async def enrich_by_dois(self, dois, **_kwargs):
        return {
            doi: {
                "id": f"https://openalex.org/W100{_suffix_number(doi)}",
                "doi": f"https://doi.org/{doi}",
                "open_access": {
                    "is_oa": True,
                    "oa_url": f"https://openalex.example.org/{_suffix_number(doi)}",
                },
            }
            for doi in dois
        }


async def test_scheduled_task_runs_literature_pipeline_twice_against_sqlite(
    monkeypatch,
    tmp_path,
):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables = [
        Country.__table__,
        StandardDisease.__table__,
        Task.__table__,
        LiteratureArticle.__table__,
        LiteratureDiseaseLink.__table__,
        LiteratureCountryLink.__table__,
        LiteratureTopicLink.__table__,
        LiteratureStatusEvent.__table__,
        LiteratureIngestRun.__table__,
    ]
    Base.metadata.create_all(engine, tables=tables)
    with Session(engine) as session:
        session.add(Country(code="JP", name="Japan", name_en="Japan", is_active=True))
        session.add(
            StandardDisease(
                disease_id="D021",
                standard_name_en="Dengue",
                standard_name_zh="登革热",
                is_active=True,
            )
        )
        session.commit()

    journals_path = tmp_path / "journals.json"
    taxonomy_path = tmp_path / "taxonomy.json"
    aliases_path = tmp_path / "aliases.json"
    journals_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "journals": [
                    {"name": "Acceptance One", "issn": "1111-1111"},
                    {"name": "Acceptance Two", "issn": "2222-2222"},
                ],
            }
        ),
        encoding="utf-8",
    )
    taxonomy_path.write_text(
        json.dumps({"topics": {"Surveillance": ["surveillance"]}, "study_types": {}}),
        encoding="utf-8",
    )
    aliases_path.write_text(json.dumps({"aliases": {"D021": ["Dengue"]}}), encoding="utf-8")
    config = LiteratureSettings(
        _env_file=None,
        enabled=True,
        schedule_enabled=True,
        journals_path=journals_path,
        taxonomy_path=taxonomy_path,
        disease_aliases_path=aliases_path,
        max_records_per_run=1,
        controlled_discovery_enabled=False,
        official_guidance_enabled=False,
        max_europe_pmc_records=10,
        max_openalex_records=10,
        max_unpaywall_records=10,
        source_concurrency=2,
        metadata_enrichment_concurrency=2,
        metadata_enrichment_min_interval_seconds=0,
        request_timeout_seconds=5,
        max_retries=1,
        autopilot_enabled=False,
    )
    database = _AsyncSqliteFactory(engine)
    monkeypatch.setattr("src.core.task_manager.get_db", database)
    monkeypatch.setattr("src.literature.pipeline.get_db", database)
    monkeypatch.setattr("src.services.literature_service.get_database", database)
    monkeypatch.setattr("src.services.task_executor.get_database", database)
    monkeypatch.setattr("src.literature.pipeline.CrossrefClient", _AcceptanceCrossrefClient)
    monkeypatch.setattr("src.literature.pipeline.EuropePmcClient", _AcceptanceEuropePmcClient)
    monkeypatch.setattr("src.literature.pipeline.UnpaywallClient", _AcceptanceUnpaywallClient)
    monkeypatch.setattr("src.literature.pipeline.OpenAlexClient", _AcceptanceOpenAlexClient)
    monkeypatch.setattr(literature_service, "_config", lambda: config)

    async def no_schedule_state_write(*_args, **_kwargs):
        return None

    async def no_release_trigger(*_args, **_kwargs):
        return None

    monkeypatch.setattr(schedule_state_repository, "save", no_schedule_state_write)
    monkeypatch.setattr(data_release_service, "maybe_trigger_after_task_completion", no_release_trigger)
    previous_state = literature_service._state
    previous_lock = literature_service._lock
    literature_service._state = LiteratureScheduleState()
    literature_service._lock = asyncio.Lock()

    try:
        first_job = await literature_service.trigger_job(literature_service.JOB_ID, manual=False)
        first_output = await task_executor.execute_task(first_job["task_uuid"])
        second_job = await literature_service.trigger_job(literature_service.JOB_ID, manual=False)
        second_output = await task_executor.execute_task(second_job["task_uuid"])

        assert first_job["status"] == second_job["status"] == "queued"
        assert first_output["inserted"] == 1
        assert first_output["source_truncated"] == 1
        assert second_output["inserted"] == 1
        assert second_output["source_truncated"] == 0
        assert second_output["from_indexed_at"] == "2026-08-13T00:00:00+00:00"

        with Session(engine) as session:
            tasks = session.execute(select(Task).order_by(Task.id)).scalars().all()
            runs = session.execute(select(LiteratureIngestRun).order_by(LiteratureIngestRun.id)).scalars().all()
            articles = session.execute(select(LiteratureArticle).order_by(LiteratureArticle.doi)).scalars().all()

            assert len(tasks) == 2
            assert all(task.status == TaskStatus.COMPLETED for task in tasks)
            assert all(task.output_data["normalized"] == 1 for task in tasks)
            assert len(runs) == 2
            assert runs[0].checkpoint["resume_after"] == {
                "indexed_at": "2026-08-13T00:00:00+00:00",
                "record_ids": ["doi:10.1000/acceptance-a"],
            }
            assert runs[1].checkpoint["resume_after"] is None
            assert len(articles) == 2
            assert [article.doi for article in articles] == [
                "10.1000/acceptance-a",
                "10.1000/acceptance-b",
            ]
            assert [article.pmid for article in articles] == ["1001", "1002"]
            assert [article.pmcid for article in articles] == ["PMC1001", "PMC1002"]
            assert [article.openalex_id for article in articles] == ["W1001", "W1002"]
            assert all(article.open_access_status == "open" for article in articles)
            assert [article.open_access_url for article in articles] == [
                "https://europepmc.org/articles/PMC1001",
                "https://europepmc.org/articles/PMC1002",
            ]
            assert len({article.article_id for article in articles}) == 2
    finally:
        task_executor._running.clear()
        literature_service._state = previous_state
        literature_service._lock = previous_lock
        engine.dispose()
