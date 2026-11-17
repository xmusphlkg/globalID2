# Colombia INS current-source discovery (2026-11-16)

Scope: identify an official national INS source for 2025 and 2026 SIVIGILA observations after the historical `4hyg-wa9d` and `wns8-p38i` extracts. This is a source-discovery result, not a claim about all INS publications.

**Later portal verification on the same date:** the Colombia INS proxy now
reaches the official Sivigila 4.0 microdata search page and its public
`Microdatos` catalogue. The catalogue lists 1,222 XLSX workbooks covering
2007–2025, including **72 workbooks for 2025**. All 1,222 original workbooks
(9,330,171,681 bytes) were archived and independently verified under
`data/raw/co/sivigila-microdatos/` at 17:40 Asia/Shanghai; see the
[archive record](CO_SIVIGILA_MICRODATOS_ARCHIVE.md). The earlier Socrata
findings below still describe those two datasets; their 2024 boundary does
not describe the microdata portal. A reviewed parser and source contract
are still required to import these individual microdata workbooks.

## Verified structured source boundary

- The [official `wns8-p38i` metadata](https://www.datos.gov.co/api/views/wns8-p38i.json) identifies owner `DatosabiertosINS` (`issr-5hya`). Its `rowsUpdatedAt` is 2026-11-04 00:56:14 UTC and `viewLastModified` is 2026-11-04 00:56:51 UTC. Those timestamps describe a dataset update, not the year of the observations.
- The [official grouped `ano` query for `wns8-p38i`](https://www.datos.gov.co/resource/wns8-p38i.json?%24select=ano%2Ccount%28%2A%29%20as%20n&%24group=ano&%24order=ano) returned exactly 2022: 215,209 rows; 2023: 226,179 rows; 2024: 230,378 rows. It returned no 2025 or 2026 year group.
- The [official grouped `ano` query for `4hyg-wa9d`](https://www.datos.gov.co/resource/4hyg-wa9d.json?%24select=ano%2Ccount%28%2A%29%20as%20n&%24group=ano&%24order=ano) returned 2007-2022 only. Its 2022 group overlaps `wns8-p38i` and must not be added twice.
- The [Socrata catalog filtered to the same INS owner](https://api.us.socrata.com/api/catalog/v1?domains=www.datos.gov.co&for_user=issr-5hya&limit=100) returned `resultSetSize=15` and 15 assets. The SIVIGILA assets are `84d4-vfax` (2017), `xd2n-cu8j` (2018), `qvnt-2igj` (2019), `fhc4-jjti` (2021), `4hyg-wa9d` (through 2022), and `wns8-p38i` (through 2024). No 2025/2026 national SIVIGILA table appeared under this INS owner at capture time.

The unmodified response bytes, exact request URLs, byte sizes, SHA-256 hashes and capture time are in [`data/raw/co/current-source-discovery/20261116/manifest.json`](../../data/raw/co/current-source-discovery/20261116/manifest.json). Re-run the owner catalog and grouped `ano` queries during future refresh checks; a later source update may add 2025/2026 observations or another asset.

## Weekly bulletin access

**Later network diagnosis on the same date:** three Colombian Globalping
probes successfully opened the exact archive URL (HTTP 200, valid TLS), and
the official homepage also returned 200. US/Brazil comparison probes timed out
while this ingestion server continued to fail TLS. The site is reachable from
Colombia; the original capture failure is dependent on the source network.
See [network evidence](../acceptance/CO_INS_NETWORK_20261116.md). Probe HTML
responses are truncated diagnostics and do not constitute full report originals.

The [INS Boletín Epidemiológico Semanal archive](https://www.ins.gov.co/buscador-eventos/Paginas/Vista-Boletin-Epidemilogico.aspx) and direct INS PDF paths failed TLS negotiation from this environment on 2026-11-16 (`curl: (35) TLS connect error: unexpected eof while reading`). The same failure occurred for the INS SIVIGILA portal. No official weekly bulletin PDF or spreadsheet was downloaded, so its table layout, week-specific versus year-to-date measures, and enumerability remain unverified. The network failure does **not** imply that INS has not published 2025/2026 bulletins.

Current import decision: use `wns8-p38i` for verifiable 2023/2024 source-native history; do not label the 2026 metadata update as 2026 case coverage or manufacture 2025/2026 rows. A 2025/2026 ingestion connector needs an accessible official original, explicit table/period contract and independent source-row/database acceptance.
