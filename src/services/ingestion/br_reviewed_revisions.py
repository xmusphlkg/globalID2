"""Accept individual SINAN corrections proven by retained official source bytes.

This is deliberately separate from normal refresh: neither a revisable source
nor a verified download grants a blanket quality override. Every reviewed cell
must agree with both immutable artifacts and the still-current stored value.
The caller holds the BR mutation lock through this check and normal ingestion.
"""
from __future__ import annotations

import hashlib
import json
import math
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import text

from src.data.crawlers.br import BrazilSINANCrawler, SINANFile
from src.data.processors.br_subdivisions import aggregate_residence_file


def _count(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("Reviewed revision values must be nonnegative finite counts")
    if not math.isfinite(value) or value < 0 or int(value) != value:
        raise ValueError("Reviewed revision values must be nonnegative finite counts")
    return int(value)


async def reviewed_residence_rows(db, rows, receipt, *, root: Path):
    if receipt.get("schema_version") != 1 or not isinstance(receipt.get("entries"), list):
        raise ValueError("Unsupported BR reviewed revision receipt")
    receipt_sha = hashlib.sha256(json.dumps(receipt, sort_keys=True).encode()).hexdigest()
    candidates = {}
    for index, row in enumerate(rows):
        identity = (row["CountryCode"], row["DiseaseCode"], row["Date"])
        if identity in candidates:
            raise ValueError("Reviewed revisions require unique candidate identities")
        candidates[identity] = (index, row)
    approved, seen, parsed = list(rows), set(), {}

    async def source(proof):
        path, digest = proof["relative_path"], proof["sha256"]
        key = (path, digest)
        if key in parsed:
            return parsed[key]
        result = await db.execute(text(
            "SELECT source_url, metadata, created_at FROM source_artifacts "
            "WHERE relative_path=:path AND sha256=:sha AND kind='sinan_dbc' "
            "ORDER BY created_at LIMIT 1"
        ), {"path": path, "sha": digest})
        artifact = result.mappings().first()
        if artifact is None:
            raise ValueError("Reviewed source is not a retained SINAN artifact")
        metadata = dict(artifact["metadata"]["file"])
        if metadata.get("modified_at"):
            metadata["modified_at"] = datetime.fromisoformat(metadata["modified_at"])
        item = SINANFile(**metadata)
        url = urlparse(item.url)
        if (item.url != artifact["source_url"] or url.hostname != "ftp.datasus.gov.br"
                or url.scheme not in {"ftp", "https"}
                or not url.path.startswith("/dissemin/publicos/SINAN/DADOS/")):
            raise ValueError("Reviewed source must be an official SINAN URL")
        location = (root / path).resolve()
        if not location.is_relative_to((root / "data/raw/br").resolve()):
            raise ValueError("Reviewed source must be a retained BR raw artifact")
        if hashlib.sha256(location.read_bytes()).hexdigest() != digest:
            raise ValueError("Reviewed source artifact checksum mismatch")
        with tempfile.TemporaryDirectory(prefix="globalid-reviewed-br-") as directory:
            dbf = Path(directory) / "source.dbf"
            BrazilSINANCrawler._decompress_to_dbf(location, dbf)
            result = aggregate_residence_file(item, dbf)
        parsed[key] = (item, artifact["created_at"], {
            (r["CountryCode"], r["DiseaseCode"], r["Date"]): int(r["Cases"])
            for r in result["rows"]
        })
        return parsed[key]

    for review in receipt["entries"]:
        country, disease = review["country_code"], review["disease_code"]
        day = date.fromisoformat(review["date"])
        identity = (country, disease, day.isoformat())
        if identity in seen or identity not in candidates:
            raise ValueError("Reviewed revision identity is duplicate or absent from candidate")
        seen.add(identity)
        index, row = candidates[identity]
        old, new = _count(review["stored_value"]), _count(review["incoming_value"])
        if old == new or int(row["Cases"]) != new:
            raise ValueError("Reviewed revision does not match candidate value")
        previous, old_at, old_rows = await source(review["previous_artifact"])
        current, new_at, new_rows = await source(review["current_artifact"])
        if (previous.filename != current.filename or previous.url != current.url
                or previous.year != current.year or old_at >= new_at
                or review["previous_artifact"]["sha256"] == review["current_artifact"]["sha256"]):
            raise ValueError("Reviewed revision requires ordered snapshots of the same official file")
        if (old_rows.get(identity) != old or new_rows.get(identity) != new
                or row.get("SourceFiles") != current.filename
                or row.get("SourceURLs") != current.url):
            raise ValueError("Reviewed values are not proven by exact source contributions")
        geography = f"country:{country}:national"
        if not country.startswith("BR-") or row.get("GeographyKey") != geography:
            raise ValueError("Reviewed revisions require exact residence geography")
        result = await db.execute(text(
            "SELECT value, raw_data FROM disease_series_observations "
            "WHERE series_code=:series AND geography_key=:geography "
            "AND time=:time AND dimension_key='all' AND NOT suppressed"
        ), {"series": review["series_code"], "geography": geography,
            "time": datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)})
        stored = result.mappings().first()
        if (stored is None or stored["value"] != old
                or stored["raw_data"].get("DiseaseCode") != disease
                or stored["raw_data"].get("SourceFiles") != previous.filename
                or stored["raw_data"].get("SourceURLs") != previous.url):
            raise ValueError("Reviewed revision no longer matches stored source observation")
        approved[index] = {**row, "AuthoritativeRevision": True,
            "RevisionSemantics": "source_correction", "RevisionEvidence": {
                "receipt_sha256": receipt_sha, "previous_artifact": review["previous_artifact"],
                "current_artifact": review["current_artifact"], "stored_value": old,
                "incoming_value": new, "series_code": review["series_code"],
            }}
    return approved
