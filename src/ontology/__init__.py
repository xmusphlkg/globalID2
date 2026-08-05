"""Public API for the configuration-driven disease ontology."""

from .disease import (
    DEFAULT_ONTOLOGY_PATH,
    NO_AUTO_ROLLUP,
    DiseaseOntology,
    OntologyValidationError,
    load_disease_ontology,
)

__all__ = [
    "DEFAULT_ONTOLOGY_PATH",
    "NO_AUTO_ROLLUP",
    "DiseaseOntology",
    "OntologyValidationError",
    "load_disease_ontology",
]
