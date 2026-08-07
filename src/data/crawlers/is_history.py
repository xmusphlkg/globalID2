"""Download the Directorate of Health's historical Iceland workbooks.

The public page is a catalogue rather than a data API.  Its files cover three
different reporting bases and must not be collapsed while crawling:

* the annual notifiable-disease registry table (1997--2021),
* fourteen disease-specific monthly workbooks, and
* the legacy monthly registered-diagnosis tables (1997--2020).

The built-in catalogue is intentional.  Contentful asset URLs are immutable,
while the landing page markup and mojibake filenames have changed over time.
Discovery is still recorded and can replace a matching catalogue URL, but a
temporary landing-page redesign cannot silently drop historical files.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, List
from urllib.parse import unquote, urljoin, urlparse

from src.data.crawlers.base import BaseCrawler, CrawlerResult


DEFAULT_LANDING_URL = "https://island.is/en/smitsjukdomar-tolur"
DEFAULT_RAW_DIR = Path("data/raw/is/history")
MANIFEST_SCHEMA = "globalid.iceland-history-raw-manifest.v1"


@dataclass(frozen=True)
class IcelandHistoryWorkbookSpec:
    """One official workbook and its source-grain contract."""

    key: str
    source_kind: str
    filename: str
    url: str
    disease_key: str = ""
    validation_only: bool = False


@dataclass(frozen=True)
class IcelandHistoryRawFile:
    """A downloaded immutable raw artifact."""

    key: str
    source_kind: str
    filename: str
    path: str
    source_url: str
    sha256: str
    size_bytes: int
    media_type: str
    disease_key: str = ""
    validation_only: bool = False


@dataclass(frozen=True)
class IcelandHistoryDownloadResult:
    """Download result consumed by :mod:`src.data.processors.is_history`."""

    raw_files: List[IcelandHistoryRawFile]
    manifest_path: Path
    landing_url: str
    landing_sha256: str


def _spec(
    key: str,
    source_kind: str,
    filename: str,
    url: str,
    *,
    disease_key: str = "",
    validation_only: bool = False,
) -> IcelandHistoryWorkbookSpec:
    return IcelandHistoryWorkbookSpec(
        key=key,
        source_kind=source_kind,
        filename=filename,
        url=url,
        disease_key=disease_key,
        validation_only=validation_only,
    )


# URLs copied from the Directorate of Health landing page.  The duplicate
# 2011--2015 annual workbook is kept as a validation-only catalogue member and
# is not downloaded by default because the 1997--2021 annual table supersedes
# it and the monthly 2011--2015 workbook carries the useful temporal detail.
OFFICIAL_WORKBOOKS: tuple[IcelandHistoryWorkbookSpec, ...] = (
    _spec("registry_annual_1997_2021", "registry_annual", "Tilkynningarskyldir_1997_2021.xlsx", "https://assets.ctfassets.net/8k0h54kbe6bj/1vEGMtTT5NYtTICdsfXg1g/93105d399172762c50c7f36145ca6032/Tilkynningarskyldir_1997_2021.xlsx"),
    _spec("disease_esbl", "registry_disease_monthly", "ESBL_2012-2018.xlsx", "https://assets.ctfassets.net/8k0h54kbe6bj/6VhnCZEmyYcAmCN93C76mx/8835ed22351af3da41e2f7809bfecd55/ESBL_2012-2018.xlsx", disease_key="esbl_ampc"),
    _spec("disease_giardiasis", "registry_disease_monthly", "Giardia_1999-2021.xlsx", "https://assets.ctfassets.net/8k0h54kbe6bj/4xbrznz4mXejsNqhVY6DrW/6e7634a338dee5c97ec01e93849fdc9d/Giardia_1999-2021.xlsx", disease_key="giardiasis"),
    _spec("disease_hiv", "registry_disease_monthly", "HIV_1997-2021.xlsx", "https://assets.ctfassets.net/8k0h54kbe6bj/TSoQ539MJCti6ir6HOnvS/15e68352b8bd7b9fe96ce55371a803b9/HIV_1997-2021.xlsx", disease_key="hiv"),
    _spec("disease_campylobacteriosis", "registry_disease_monthly", "Kampylobakter_1997-2021.xlsx", "https://assets.ctfassets.net/8k0h54kbe6bj/7wLCBY8agk0aSi1TrBzLm3/65ed2bae67631e32cf66b8a0d986c8a8/Kamp__l__bakter_1997-2021.xlsx", disease_key="campylobacteriosis"),
    _spec("disease_pertussis", "registry_disease_monthly", "Kikhosti_1998-2021.xlsx", "https://assets.ctfassets.net/8k0h54kbe6bj/5BKeGTk9EClSKXrta0onlW/1427733f5da14a804d5cd16e0391f0ed/Kikh__sti_1998-2021.xlsx", disease_key="pertussis"),
    _spec("disease_chlamydia", "registry_disease_monthly", "Klamydia_1997-2021.xlsx", "https://assets.ctfassets.net/8k0h54kbe6bj/9AQZ1HMcMu9Ev6BGIiK50/fcb77ab2814b7df3948b8830868982de/Klamydia_1997-2021.xlsx", disease_key="chlamydia"),
    _spec("disease_gonorrhea", "registry_disease_monthly", "Lekandi_1997-2021.xlsx", "https://assets.ctfassets.net/8k0h54kbe6bj/6rQTjWGE73YC9HLbsxlP16/f1f67fb3505aad6d815f0a2a6fc3affe/Lekandi_1997-2021.xlsx", disease_key="gonorrhea"),
    _spec("disease_hepatitis_a", "registry_disease_monthly", "Lifrarbolga_A_2007-2021.xlsx", "https://assets.ctfassets.net/8k0h54kbe6bj/4OW6e8WGDTIuEgZXCaVpOq/9d153e3ccfa2c1887ae6bb8853cc43b1/Lifrarb__lga_A_2007-2021.xlsx", disease_key="hepatitis_a"),
    _spec("disease_hepatitis_b", "registry_disease_monthly", "Lifrarbolga_B_1997-2021.xlsx", "https://assets.ctfassets.net/8k0h54kbe6bj/7Ks8NxsGR0rrMup0IyWLnR/53eae63c3657b0f21b0a966cf45e53d9/Lifrarb__lga_B_1997-2021_uppfaert.xlsx", disease_key="hepatitis_b_combined"),
    _spec("disease_hepatitis_c", "registry_disease_monthly", "Lifrarbolga_C_1997-2021.xlsx", "https://assets.ctfassets.net/8k0h54kbe6bj/789RYH6VbTpDg3puqJspC7/2cdcf01a4db53027b3b335d94108bdb6/Lifrarb__lga_C_1997-2021.xlsx", disease_key="hepatitis_c"),
    _spec("disease_invasive_pneumococcal", "registry_disease_monthly", "Ifarandi_pneumokokkar_2009-2021.xlsx", "https://assets.ctfassets.net/8k0h54kbe6bj/549a7TsVq2PhAvofaif9uD/6e028386079ebac3f640b3caffdd5e9d/__farandi_pneum__kokkas__king_2009-2021.xlsx", disease_key="invasive_pneumococcal"),
    _spec("disease_salmonellosis", "registry_disease_monthly", "Salmonella_1997-2021.xlsx", "https://assets.ctfassets.net/8k0h54kbe6bj/4jolFijsfpD2O4ao1TDPvO/359ad1fc6d70df2fd7b8bba0540bc84d/Salmonella_1997-2021.xlsx", disease_key="salmonellosis"),
    _spec("disease_syphilis", "registry_disease_monthly", "Sarasott_2014-2019.xlsx", "https://assets.ctfassets.net/8k0h54kbe6bj/3pPokssAoITRKwR7IpMumP/96bf371dfaf8ce603262d8e709e86afe/S__ras__tt_2014-2018.xlsx", disease_key="syphilis"),
    _spec("disease_vre", "registry_disease_monthly", "VRE_2012-2019.xlsx", "https://assets.ctfassets.net/8k0h54kbe6bj/39dKxIhCY2fKKCzsO5jAr4/caa185f7e56cd922c92233cb3eb26b7a/V__E_2012-2018.xlsx", disease_key="vre"),
    _spec("legacy_icd_2020", "legacy_icd_monthly", "Skraningarskyldir2020.xlsx", "https://assets.ctfassets.net/8k0h54kbe6bj/2SJhWHI12JKltb1z25Ei96/fca6dd4edb141034ff0326e9b0089239/Skraningarskyldir2020.xlsx"),
    _spec("legacy_icd_2019", "legacy_icd_monthly", "Skraningarskyldir2019.xlsx", "https://assets.ctfassets.net/8k0h54kbe6bj/3CNdg4ZPDcNYCsajbGr8OY/a174f5f9603c091afed1cfcd5f889f6c/Skraningarskyldir2019.xlsx"),
    _spec("legacy_icd_2018", "legacy_icd_monthly", "Skraningarskyldir2018.xlsx", "https://assets.ctfassets.net/8k0h54kbe6bj/3dgxb4tqd7vYxgePlPInjK/3a3ac0309ff13a7cd5e2d3279d64c4a8/Skraningarskyldir2018.xlsx"),
    _spec("legacy_icd_2017", "legacy_icd_monthly", "Skraningarskyldir2017.xlsx", "https://assets.ctfassets.net/8k0h54kbe6bj/1BB3mTfElfVgcgZykp5t95/6f883b233da16104422b82556711679b/Skraningarskyldir2017.xlsx"),
    _spec("legacy_icd_2016", "legacy_icd_monthly", "Skraningarskyldir2016.xlsx", "https://assets.ctfassets.net/8k0h54kbe6bj/6vxZihv6m6ByuSPxhCV5vR/e7760ec1a9e72fea5adbb491b83f34f2/Skr__ningarskyldir_sj__kd__mar_eftir_m__nu__um_2016.xlsx"),
    _spec("legacy_icd_2011_2015", "legacy_icd_monthly", "Skraningarskyldir_manudir_2011_2015.xlsx", "https://assets.ctfassets.net/8k0h54kbe6bj/2GwnbcHKOmmS7YdFxMHRFx/906a200bd9f3f192eb0b0669ec462e72/Tafla_skraningarskyldir_manudir_2011_2015.xlsx"),
    _spec("legacy_icd_1997_2010", "legacy_icd_monthly", "Skraningarskyldir_1997_2010.xls", "https://assets.ctfassets.net/8k0h54kbe6bj/ZhTZAlcQ1UcJVNHi3Ttkv/4e55aae6e60e55ededd645392a15654d/Skr__ningarskyldir_1997_2010.xls"),
    _spec("validation_annual_2011_2015", "validation_annual", "Skraningarskyldir_annual_2011_2015.xlsx", "https://assets.ctfassets.net/8k0h54kbe6bj/7ncvpQHybNC6nOhr6DIpO2/b8607ab800958024e1e304040ca40b3b/Tafla_skraningarskyldir_2011_2015.xlsx", validation_only=True),
)


class _ExcelLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "a":
            return
        href = dict(attrs).get("href")
        if href and re.search(r"\.xlsx?(?:$|[?#])", href, re.IGNORECASE):
            self.hrefs.append(href)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _asset_basename(url: str) -> str:
    return unquote(Path(urlparse(url).path).name).casefold()


def _validate_excel(payload: bytes, filename: str) -> None:
    suffix = Path(filename).suffix.casefold()
    if suffix == ".xlsx" and not payload.startswith(b"PK\x03\x04"):
        raise ValueError(f"Downloaded file is not an OOXML workbook: {filename}")
    if suffix == ".xls" and not payload.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        raise ValueError(f"Downloaded file is not an OLE Excel workbook: {filename}")


class IcelandHistoryCrawler(BaseCrawler):
    """Fetch the official historical workbooks with reproducible provenance."""

    def __init__(
        self,
        *,
        landing_url: str = DEFAULT_LANDING_URL,
        raw_dir: Path | str = DEFAULT_RAW_DIR,
        **kwargs,
    ) -> None:
        kwargs.setdefault("delay", 0.05)
        super().__init__(**kwargs)
        self.landing_url = landing_url
        self.raw_dir = Path(raw_dir)

    @staticmethod
    def catalogue(*, include_validation: bool = False) -> list[IcelandHistoryWorkbookSpec]:
        return [
            spec
            for spec in OFFICIAL_WORKBOOKS
            if include_validation or not spec.validation_only
        ]

    @staticmethod
    def discover_links(html: str, landing_url: str = DEFAULT_LANDING_URL) -> list[str]:
        parser = _ExcelLinkParser()
        parser.feed(html)
        return list(dict.fromkeys(urljoin(landing_url, href) for href in parser.hrefs))

    @classmethod
    def _catalogue_with_discovery(
        cls,
        html: str,
        *,
        landing_url: str,
        include_validation: bool,
    ) -> list[IcelandHistoryWorkbookSpec]:
        discovered = cls.discover_links(html, landing_url)
        by_tail = {_asset_basename(url): url for url in discovered}
        resolved: list[IcelandHistoryWorkbookSpec] = []
        for spec in cls.catalogue(include_validation=include_validation):
            live_url = by_tail.get(_asset_basename(spec.url), spec.url)
            resolved.append(
                IcelandHistoryWorkbookSpec(**{**asdict(spec), "url": live_url})
            )
        return resolved

    def parse(self, response) -> list[CrawlerResult]:
        """Parse a landing-page response into catalogue entries."""

        html = response.text
        discovered = self.discover_links(html, getattr(response, "url", self.landing_url))
        return [
            CrawlerResult(
                title=Path(urlparse(url).path).name,
                url=url,
                metadata={"source_kind": "historical_excel_link"},
            )
            for url in discovered
        ]

    async def crawl(self, **kwargs) -> list[CrawlerResult]:
        result = await asyncio.to_thread(self.download_history, **kwargs)
        return [
            CrawlerResult(
                title=item.filename,
                url=item.source_url,
                metadata={
                    "key": item.key,
                    "source_kind": item.source_kind,
                    "sha256": item.sha256,
                    "path": item.path,
                },
            )
            for item in result.raw_files
        ]

    def download_history(
        self,
        *,
        output_dir: Path | str | None = None,
        include_validation: bool = False,
        discover: bool = True,
        specs: Iterable[IcelandHistoryWorkbookSpec] | None = None,
    ) -> IcelandHistoryDownloadResult:
        """Download workbooks and atomically publish a raw manifest.

        Existing identical files are retained.  A changed upstream asset is
        overwritten only after its workbook signature has been validated; the
        old and new SHA-256 values remain observable through version control or
        the surrounding raw archive workflow.
        """

        target = Path(output_dir) if output_dir is not None else self.raw_dir
        target.mkdir(parents=True, exist_ok=True)

        landing_payload = b""
        landing_error = ""
        catalog = list(specs) if specs is not None else self.catalogue(
            include_validation=include_validation
        )
        if discover:
            try:
                response = self.get(self.landing_url)
                landing_payload = response.content
                catalog = self._catalogue_with_discovery(
                    response.text,
                    landing_url=getattr(response, "url", self.landing_url),
                    include_validation=include_validation,
                ) if specs is None else catalog
            except Exception as exc:  # built-in catalogue is the availability fallback
                landing_error = f"{type(exc).__name__}: {exc}"

        fetched_at = datetime.now(timezone.utc).isoformat()
        raw_files: list[IcelandHistoryRawFile] = []
        for spec in catalog:
            response = self.get(spec.url)
            payload = response.content
            _validate_excel(payload, spec.filename)
            digest = _sha256(payload)
            path = target / spec.filename
            if not path.exists() or _sha256(path.read_bytes()) != digest:
                temporary = path.with_suffix(path.suffix + ".part")
                temporary.write_bytes(payload)
                temporary.replace(path)
            raw_files.append(
                IcelandHistoryRawFile(
                    key=spec.key,
                    source_kind=spec.source_kind,
                    filename=spec.filename,
                    path=str(path.resolve()),
                    source_url=spec.url,
                    sha256=digest,
                    size_bytes=len(payload),
                    media_type=mimetypes.guess_type(spec.filename)[0]
                    or "application/octet-stream",
                    disease_key=spec.disease_key,
                    validation_only=spec.validation_only,
                )
            )

        # Keep the persisted manifest relocatable.  ``raw_files`` continues to
        # expose resolved paths to the caller running this download, while a
        # copied archive can be replayed on another host using only the
        # manifest and its neighbouring workbooks.
        manifest_files: list[dict[str, object]] = []
        resolved_target = target.resolve()
        for item in raw_files:
            document = asdict(item)
            item_path = Path(item.path).resolve()
            try:
                document["path"] = item_path.relative_to(resolved_target).as_posix()
            except ValueError:
                # Downloads are expected to live directly below ``target``;
                # retaining the basename is the safest portable fallback if a
                # custom filesystem resolves the paths differently.
                document["path"] = item.filename
            manifest_files.append(document)

        manifest = {
            "schema": MANIFEST_SCHEMA,
            "created_at": fetched_at,
            "landing_url": self.landing_url,
            "landing_sha256": _sha256(landing_payload) if landing_payload else "",
            "landing_fetch_error": landing_error,
            "catalogue_file_count": len(catalog),
            "files": manifest_files,
        }
        manifest_path = target / "raw_manifest.json"
        temporary_manifest = manifest_path.with_suffix(".json.part")
        temporary_manifest.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary_manifest.replace(manifest_path)
        return IcelandHistoryDownloadResult(
            raw_files=raw_files,
            manifest_path=manifest_path.resolve(),
            landing_url=self.landing_url,
            landing_sha256=manifest["landing_sha256"],
        )


__all__ = [
    "DEFAULT_LANDING_URL",
    "DEFAULT_RAW_DIR",
    "IcelandHistoryCrawler",
    "IcelandHistoryDownloadResult",
    "IcelandHistoryRawFile",
    "IcelandHistoryWorkbookSpec",
    "MANIFEST_SCHEMA",
    "OFFICIAL_WORKBOOKS",
]
