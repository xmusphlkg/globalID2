# GlobalID V2 — Data Pipeline

`src/data/` is the core data-acquisition and normalization layer.  
It turns raw HTML/CSV/RSS from national health authorities into standardised,
database-ready `disease_records` rows.

---

## Architecture

```
HTTP Sources
    │
    ▼
┌───────────────────────────────────────────┐
│  crawlers/          Phase 1 + 3           │
│  ─────────────────────────────────────    │
│  BaseCrawler        (abstract)            │
│  ChinaCDCCrawler    CN  – HTML + RSS      │
│  JapanIDWRCrawler   JP  – weekly CSV      │
│  AustraliaNINDSS    AU  – NINDSS feed     │
│  USNNDSSCrawler     US  – CDC API         │
└─────────────────┬─────────────────────────┘
                  │  List[CrawlerResult]
                  ▼
┌───────────────────────────────────────────┐
│  parsers/           Phase 2               │
│  ─────────────────────────────────────    │
│  BaseParser         (abstract)            │
│  HTMLTableParser    Disease/Cases/Deaths  │
└─────────────────┬─────────────────────────┘
                  │  ParseResult (DataFrame)
                  ▼
┌───────────────────────────────────────────┐
│  normalizers/       Phase 2               │
│  ─────────────────────────────────────    │
│  DiseaseMapper      CSV-backed (offline)  │
│  DiseaseMapperDB    PostgreSQL (async)    │
│  DiseaseMapperDBSync  sync wrapper        │
└─────────────────┬─────────────────────────┘
                  │  DataFrame (disease_id resolved)
                  ▼
┌───────────────────────────────────────────┐
│  processors/        Phase 2               │
│  ─────────────────────────────────────    │
│  DataProcessor      orchestrator          │
└─────────────────┬─────────────────────────┘
                  │  DataFrame (validated)
                  ▼
┌───────────────────────────────────────────┐
│  storage/           Phase 3               │
│  ─────────────────────────────────────    │
│  RecordStore        batch upsert          │
└─────────────────┬─────────────────────────┘
                  │
                  ▼
         PostgreSQL / TimescaleDB
         disease_records  (hypertable)
```

---

## Three-Phase Crawl Pattern

Every crawler follows the same three-phase contract to minimise network I/O
and avoid redundant re-processing:

| Phase | Method | Purpose |
|-------|--------|---------|
| 1 | `fetch_list()` | Lightweight: fetch only titles, URLs, dates |
| 2 | `check_new_data(list_results)` | Compare against DB; return only new/missing |
| 3 | (DataProcessor) `process_crawler_results(new)` | Heavyweight: fetch full HTML, parse, normalise, store |

The `crawl()` method on each crawler wraps all three phases.
Pass `force=True` to skip Phase 2 and reprocess everything.
Pass `fill_missing=True` to also back-fill months that exist in the listing
but are absent from the database.

---

## Directory Structure

```
src/data/
├── __init__.py               Public re-exports for the whole layer
├── README.md                 This file
│
├── crawlers/
│   ├── base.py               BaseCrawler + CrawlerResult dataclass
│   ├── cn.py                 China CDC / NHC / PubMed crawler
│   ├── jp.py                 Japan NIID IDWR weekly CSV crawler
│   ├── au.py                 Australia NINDSS crawler
│   ├── us.py                 US CDC NNDSS API crawler
│   └── __init__.py
│
├── parsers/
│   ├── base.py               BaseParser + ParseResult dataclass
│   ├── html_parser.py        3-column HTML table parser (EN + ZH)
│   ├── ai_layout_parser.py   AI-assisted PDF/image layout parser
│   └── __init__.py
│
├── normalizers/
│   ├── disease_mapper.py     CSV-backed offline mapper
│   ├── disease_mapper_db.py  Async PostgreSQL mapper + sync wrapper
│   ├── english_mapper.py     Multi-language mapper (EnglishDiseaseMapper)
│   └── __init__.py
│
├── processors/
│   ├── cn.py                 CN pipeline orchestrator (DataProcessor)
│   ├── jp.py                 JP weekly pipeline (JPWeeklyUpdater)
│   ├── au.py                 AU monthly pipeline (AUMonthlyUpdater)
│   ├── us.py                 US weekly pipeline (USWeeklyUpdater)
│   └── __init__.py
│
└── storage/
    ├── record_store.py       Batch upsert into disease_records
    └── __init__.py
```

---

## Supported Countries

| Code | Crawler | Processor | Data Sources | Language | Notes |
|------|---------|-----------|-------------|----------|-------|
| CN | `ChinaCDCCrawler` (`crawlers/cn.py`) | `DataProcessor` (`processors/cn.py`) | CDC Weekly, NHC Gov API, PubMed RSS | EN + ZH | Three sources, HTML tables |
| JP | `JapanIDWRCrawler` (`crawlers/jp.py`) | `JPWeeklyUpdater` (`processors/jp.py`) | NIID IDWR weekly CSV | JA / EN | Merges `zensu` + `teiten` formats |
| AU | `AustraliaNINDSSCrawler` (`crawlers/au.py`) | `AUMonthlyUpdater` (`processors/au.py`) | NINDSS surveillance reports | EN | — |
| US | `USNNDSSCrawler` (`crawlers/us.py`) | `USWeeklyUpdater` (`processors/us.py`) | CDC NNDSS weekly API | EN | Socrata paginated CSV |

---

## Adding a New Country (Step-by-Step)

### Step 1 — Update `configs/standard_diseases.csv`

Ensure every disease the new country reports is present in the global standard
disease library. If a disease is missing, append a row:

```
disease_id,standard_name_en,standard_name_zh,category,icd_10,icd_11,description
D142,Mpox,猴痘,Viral,B04,1E71,Monkeypox virus infection
```

`disease_id` must be globally unique (format: `D` + 3-digit number).

---

### Step 2 — Create `configs/mapping/<cc>.csv`

Map every local disease name the source uses to a `disease_id`:

```
disease_id,local_name,local_code,category,aliases
D001,Cholera,CHOL,Bacterial,Cholera
D004,COVID-19,COVID,Viral,Coronavirus|SARS-CoV-2|Novel coronavirus
D142,Mpox,MPOX,Viral,Monkeypox
```

- `local_name` must exactly match the string that appears in the source HTML/CSV.
- `aliases` is a `|`-separated list of alternative spellings (optional).

---

### Step 3 — Add Bootstrap Config `configs/country_bootstrap.json`

```json
{
  "XX": {
    "name": "Country Name",
    "code": "XX",
    "mapping_file": "configs/mapping/xx.csv",
    "sources": ["source_a"],
    "language": "en",
    "reporting_area": "national"
  }
}
```

---

### Step 4 — Implement the Crawler

Create `src/data/crawlers/xx.py` (name it after the country code, not the data system):

```python
from src.data.crawlers.base import BaseCrawler, CrawlerResult
from src.core import get_logger

logger = get_logger(__name__)

class XXCrawler(BaseCrawler):  # e.g. BrazilCrawler, IndiaCrawler

    SOURCE_URL = "https://health.example.xx/data"

    def __init__(self):
        super().__init__(timeout=30, max_retries=3, delay=1.0)

    # ── Phase 1: lightweight index ────────────────────────────────────────
    async def fetch_list(self, **kwargs) -> list[CrawlerResult]:
        response = self.get(self.SOURCE_URL)
        return self._parse_index(response)

    def _parse_index(self, response) -> list[CrawlerResult]:
        # Parse titles + URLs + dates only — no full content fetching here.
        results = []
        # ... your parsing logic ...
        return results

    # ── Phase 2: DB comparison (inherited helper available) ──────────────
    async def check_new_data(self, list_results, *, fill_missing=False):
        # Use the standard pattern from ChinaCDCCrawler as reference.
        ...

    # ── Phase 3: full detail fetch ───────────────────────────────────────
    async def crawl(self, source="all", force=False, fill_missing=False, **kwargs):
        logger.info("[XX-Source] Phase 1/3 — Fetching index")
        candidates = await self.fetch_list(**kwargs)
        if not candidates:
            logger.warning("[XX-Source] No candidates found")
            return []

        if force:
            new_results = candidates
        else:
            logger.info("[XX-Source] Phase 2/3 — Checking new data")
            check = await self.check_new_data(candidates, fill_missing=fill_missing)
            new_results = check["new"]

        logger.info(f"[XX-Source] Phase 3/3 | new={len(new_results)}")
        return new_results

    # Required by BaseCrawler (used only if you call parse() directly)
    def parse(self, response):
        return []
```

---

### Step 5 — Implement the Processor (if HTML pipeline)

If the source is HTML tables, reuse `DataProcessor` directly:

```python
from src.data.processors.cn import DataProcessor
from src.data.crawlers.xx import XXCrawler

async def run_xx_pipeline():
    crawler = XXCrawler()
    results = await crawler.crawl(force=False)

    processor = DataProcessor(country_code="XX")
    dfs = await processor.process_crawler_results(results)
    return dfs
```

If the format differs (CSV, JSON, etc.), create `src/data/processors/xx.py` following
the structure of `processors/us.py` or `processors/jp.py`.

---

### Step 6 — Register in `CrawlService`

In `src/services/crawl_service.py`, add `XX` to the country dispatcher
so the CLI (`python main.py crawl --country XX`) works.

---

### Step 7 — Run Initial Backfill

```bash
python scripts/full_rebuild_database.py --country XX --force
```

Then verify with:

```bash
python main.py crawl --country XX --source all --fill-missing
```

---

## Configuration Files

| File | Purpose |
|------|---------|
| `configs/standard_diseases.csv` | Global disease library; shared by all countries |
| `configs/mapping/<cc>.csv` | Per-country local-name → `disease_id` mapping |
| `configs/country_bootstrap.json` | Per-country meta (name, sources, language) |
| `configs/sources/<cc>_sources.json` | Per-country data source URLs and parameters |
| `configs/prompts/` | LLM prompts for AI-assisted disease mapping |

---

## Key Class APIs

### `CrawlerResult` (dataclass)

```python
@dataclass
class CrawlerResult:
    title: str
    url: Optional[str]          # Full page URL
    content: Optional[str]      # Pre-fetched HTML (if available)
    date: Optional[datetime]    # Report date object
    year_month: Optional[str]   # "2025 January" (canonical format)
    metadata: Dict[str, Any]    # Must contain "language" and "source"
    raw_data: Dict[str, Any]    # Source-specific raw data
```

### `ParseResult` (dataclass)

```python
@dataclass
class ParseResult:
    source_url: str
    source_title: str
    parse_date: datetime
    data: Optional[pd.DataFrame]    # Columns: Diseases, DiseasesCN, Cases, Deaths, ...
    raw_content: Optional[str]
    metadata: Dict[str, Any]
    success: bool
    error_message: Optional[str]
```

### `HTMLTableParser`

```python
parser = HTMLTableParser()

# Parse HTML string (SRP: no network I/O)
result = parser.parse(html_string, url=url, language="zh", year_month="2025 January")

# Fetch and parse in one call
result = parser.fetch_and_parse("https://example.com/report.html", language="en")
```

### `DiseaseMapperDB` (async)

```python
mapper = DiseaseMapperDB(country_code="CN")

disease_id = await mapper.map_local_to_id("新型冠状病毒感染")  # → "D004"
info = await mapper.get_standard_info("D004")                   # → DiseaseInfo
df = await mapper.map_dataframe(df, disease_col="DiseasesCN")   # batch
```

### `RecordStore`

```python
store = RecordStore()
upserted, skipped, dedup_deleted = await store.save_dataframe(df, "CN")
```

Required DataFrame columns:  
`Date`, `disease_id`, `Cases`, `Deaths`, `Incidence`, `Mortality`,  
`Source`, `Diseases`, `DiseasesCN`, `Province`, `ProvinceCN`, `YearMonth`

---

## CLI Usage

```bash
# Crawl new data for a country
python main.py crawl --country CN --source all

# Force re-crawl everything
python main.py crawl --country CN --force

# Back-fill missing months
python main.py crawl --country CN --fill-missing

# Generate an epidemiological report
python main.py generate-report --country CN --year 2025 --month 1
```

---

## Structured Log Format

All components emit logs in the format:

```
[Component][Country]  Action  |  key=value  key=value
```

Example complete crawl sequence:

```
[CN-CDC] Phase 1/3 — Fetching index
[CN-CDC] Phase 1/3 Done | candidates=52 elapsed=1.4s
[CN-CDC] Phase 2/3 — Checking new data | max_date=2025-11-01
[CN-CDC] Phase 2/3 Done | new=3 existing=49 missing_months=0
[CN-CDC] Phase 3/3 — Processing | reports=3
[DataProcessor][CN] [1/3] Parse OK | period="2025 December" rows_raw=47
[Normalizer][CN] Mapping done | rows=47 mapped=46 skipped=1 unknown=1
[RecordStore][CN] Upsert done | upserted=46 skipped=1 dedup_deleted=0
[DataProcessor][CN] [2/3] Parse OK | period="2025 November" rows_raw=45
[Normalizer][CN] Mapping done | rows=45 mapped=45 skipped=0 unknown=0
[RecordStore][CN] Upsert done | upserted=45 skipped=0 dedup_deleted=2
[DataProcessor][CN] [3/3] Parse OK | period="2025 October" rows_raw=44
[Normalizer][CN] Mapping done | rows=44 mapped=44 skipped=0 unknown=0
[RecordStore][CN] Upsert done | upserted=44 skipped=0 dedup_deleted=0
[CN-CDC] Crawl complete | processed=3 errors=0
```

Unknown diseases are automatically recorded to `disease_learning_suggestions`
and can be reviewed via the dashboard or the `DiseaseMapperDB.get_unknown_diseases()` API.

---

## Database Schema (relevant tables)

| Table | Key Columns | Notes |
|-------|------------|-------|
| `disease_records` | `time`, `disease_id`, `country_id` | TimescaleDB hypertable; composite PK |
| `standard_diseases` | `disease_id`, `standard_name_en`, `standard_name_zh`, `category` | Global disease library |
| `disease_mappings` | `country_code`, `local_name`, `disease_id`, `is_primary`, `is_alias` | Per-country name mappings |
| `disease_learning_suggestions` | `country_code`, `local_name`, `occurrence_count`, `status` | Unmapped names for review |
| `countries` | `id`, `code`, `name` | Country lookup |
| `crawl_runs` | `id`, `country_code`, `started_at` | Audit trail |
| `crawl_raw_pages` | `run_id`, `url`, `content_path`, `content_hash` | Raw page archive |

---

## Development Notes

- All async DB access uses SQLAlchemy 2.0 async sessions via `src.core.database.get_db()`.
- `DiseaseMapperDBSync` is a thin sync wrapper for legacy synchronous callers;
  it uses `ThreadPoolExecutor` to avoid event-loop deadlocks.
- `RecordStore` uses a single PostgreSQL `INSERT … ON CONFLICT DO UPDATE` per batch
  to eliminate N+1 DB round-trips.
- Rate incidence/mortality values of `-10` are legacy sentinels; they are
  normalised to `NULL` by `src.core.missing_values.normalize_rate_value()`.
