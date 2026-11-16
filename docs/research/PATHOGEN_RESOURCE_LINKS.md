# Disease-page pathogen resource links

The first iteration adds a static, multilingual resource panel to selected
disease pages. It links to NCBI Nucleotide / GenBank, NCBI Taxonomy, the GISAID
homepage where relevant, and WHO's International Pathogen Surveillance Network
(IPSN). It does not import sequence records or calculate variant prevalence.

## Curated mappings

`astro-site/src/lib/pathogen-resources.ts` owns the explicit mappings below.
Clinical disease names and slugs are never used to guess a pathogen. Concepts
without a reviewed mapping show neither the panel nor its navigation anchor.

| Disease concept | Pathogen search | NCBI Taxonomy ID | GISAID entrance |
| --- | --- | --- | --- |
| D001 — Plague | Yersinia pestis | 632 | No |
| D004 — COVID-19 | SARS-CoV-2 | 2697049 | Yes |
| D014 — Human influenza A(H5N1) infection | H5N1 subtype | 102793 | Yes |
| D017 — Measles | Measles morbillivirus | 11234 | No |
| D021 — Dengue | Dengue virus | 12637 | No |
| D028 — Pertussis | Bordetella pertussis | 520 | No |

The IDs and scientific names were checked against the official NCBI Taxonomy
EFetch response on 2026-11-15. Sequence searches use the explicit
`txid<ID>[Organism:exp]` query, including descendants. The H5N1 search remains
subtype-specific. The broad influenza concept D038 is deliberately unmapped
until its intended pathogen coverage is reviewed.

The external search is not constrained to the human hosts, geographic scope or
reporting period of a GIDS surveillance series. The panel explains that
sequence counts are not case counts or variant prevalence. These links do not
claim that a sequence originated from any outbreak shown by GIDS.

## Access and maintenance

- [NCBI GenBank](https://www.ncbi.nlm.nih.gov/genbank/) documents nucleotide
  records, search and programmatic access.
- [GISAID](https://gisaid.org/) is linked only as a general entrance. The panel
  explains that sequence-database access requires an account and is subject to
  [GISAID's terms](https://gisaid.org/terms-of-use/). No restricted records,
  metadata or summaries are copied.
- [WHO IPSN](https://www.who.int/initiatives/international-pathogen-surveillance-network)
  is described as a genomic surveillance network with public resources. Its
  link is not presented as a disease-specific sequence query.

To extend coverage, check the disease ontology's concept definition, confirm
the pathogen or subtype in NCBI Taxonomy, add the explicit mapping, and run:

```bash
cd astro-site
node --experimental-strip-types --test src/lib/pathogen-resources.test.ts
```

The EN/ZH/FR disease routes share the same page and panel. This is template
content; it becomes public through the normal website release. A JSON data
publication alone does not add the panel to previously built HTML. No database
migration or sequence synchronization job is required.

## Local verification

- Four focused tests cover pathogen-specific searches, unknown concepts,
  official hosts, resource-access distinctions and language coverage.
- Both changed Astro templates compile using `@astrojs/compiler`.
- The existing style-boundary check passes.

These checks establish local implementation readiness. They do not establish
a production website release, account access to GISAID or availability of
every external search from every user's network.
