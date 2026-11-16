# Chile MINSAL ENO historical notifications

Source: [MINSAL's official datos.gob.cl CKAN package](https://datos.gob.cl/dataset/notificaciones-eno),
`cl_eno_historical` / `SRC_CL_ENO`. The collector captures the complete CSV,
variable dictionary and methodology PDF before offline aggregation. The CKAN
package identifies the publisher as Ministerio de Salud and the license as CC0.
Public release remains disabled pending a verified site snapshot and review of
the source's changed publication scope.

## Actual source extent and contract

The 2026-11-16 CSV response was 68,280,694 bytes, SHA-256
`85d037b8824eae95743af436c9a547fc49d5bc07f1ee28bd0d4f68aad1f0777d`.
It contains 333,300 case rows, 38 distinct source `ENO` labels, and notification
years 2007–2024. Its `etapa_clinica` column has 333,275 `CONFIRMADA` and 25
`PROBABLE` rows. The parsed national output has 442 annual category/classification
observations. Their counts sum to all 333,300 captured case rows. Absent
disease/year combinations are unknown and are never written as zero.

The CKAN description and 2024 methodology PDF still describe 2007–2022 and an
older disease inventory. The current CSV additionally contains 2023/2024 and
categories such as cholera and leishmaniasis that the old PDF described as
excluded for privacy. This is a material upstream contract difference, retained
in the audit and requiring review before any public release. No patient-level
rows are written to source-series observations or the review CSV.

`anho_notificacion` is the notification year, not symptom onset or infection
date. Each row is one MINSAL-validated notification; the methodology says
deduplication occurred before publication. `CONFIRMADA` and `PROBABLE` remain
separate measures. The CSV's `region` is the **notifying health facility's**
region, whereas `codigo_comuna_residencia` describes residence. The initial
adapter aggregates only to the verified Chile-wide notification total; it does
not equate facility region with residence or map imported infections to local
acquisition. Files from 2007–2018 used FileMaker and later years used
Epivigila; the source-system period is retained in `DefinitionVersion`.

The source may revise its CSV. A 24-hour integrity-checked cache avoids repeated
network download, and a changed CKAN modification date forces revalidation.
Every staged run still writes a complete, checksum-verified source artifact.
Organizing uses only captured artifacts and a parser-versioned cache; it never
reads the live source. Existing observations can be upserted with revised
values. Deletion of a previously published disease/year cell is **not**
authorized automatically, because the source has not provided a complete
withdrawal contract.

## Verification

```bash
venv/bin/python -m pytest -q tests/unit/test_cl_eno.py
```

The source audit records captured artifact hashes, source row count, observed
years/classifications, public-release gate, and catalog-versus-CSV discrepancy.
