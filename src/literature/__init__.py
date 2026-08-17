"""GIDS Research Radar ingestion and classification package."""

from .pipeline import LiteraturePipeline
from .knowledge_graph import build_knowledge_graph

__all__ = ["LiteraturePipeline", "build_knowledge_graph"]
