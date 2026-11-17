# Ontology source documents

`configs/disease_ontology.json` owns concepts, relationships and shared metadata.
Its `source_documents` array names reviewed source contract files relative to
the root configuration. Each file owns complete `sources`, `source_series` and
`availability` arrays for its source IDs. IDs must be unique across files.
Nested includes and paths outside `configs/` are rejected.

Use `src.ontology.read_ontology_document()` when inspecting or modifying the
explicit registry. Use `load_disease_ontology()` for the validated runtime view,
including the existing source-template expansion. Both load all source files.
`ontology_document_paths()` returns every physical input for dependency hashes.

Configuration editors must save through `write_ontology_document()`, which
retains the existing source ownership. The high-frequency configuration merger
already uses this API. Preparation commands continue to write independent
review patches such as `high_frequency_mexico_ontology.json`; they do not modify
the active source documents until the reviewed patch is applied.

Keep each JSON file below the repository's 5 MiB object limit. New large source
families should receive their own reviewed source document and manifest entry.
