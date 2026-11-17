"""Public API for the configuration-driven disease ontology."""

from .disease import (
    DEFAULT_ONTOLOGY_PATH,
    NO_AUTO_ROLLUP,
    DiseaseOntology,
    OntologyValidationError,
    load_disease_ontology,
)
from .documents import ontology_document_paths, read_ontology_document, write_ontology_document

__all__ = [
    "DEFAULT_ONTOLOGY_PATH",
    "NO_AUTO_ROLLUP",
    "DiseaseOntology",
    "OntologyValidationError",
    "load_disease_ontology",
    "ontology_document_paths",
    "read_ontology_document",
    "write_ontology_document",
]
