"""Bounded reconciliation queries; transactions remain in the service."""
from __future__ import annotations
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Load
from src.domain import LiteratureArticle, LiteratureSignalArticleLink, LiteratureSummary
from src.literature.summary_contracts import SUMMARY_FIELDS

def _link_reconciliation_statement(
    *, after_id: int = 0, batch_size: int = 100, article_ids: tuple[str, ...] | None = None,
):
    statement = (
        select(LiteratureSignalArticleLink, LiteratureArticle)
        .join(
            LiteratureArticle,
            LiteratureArticle.article_id == LiteratureSignalArticleLink.article_id,
        )
        .options(
            Load(LiteratureArticle).load_only(
                LiteratureArticle.id,
                LiteratureArticle.article_id,
                LiteratureArticle.title,
                LiteratureArticle.doi,
                LiteratureArticle.pmid,
                LiteratureArticle.pmcid,
                LiteratureArticle.journal,
                LiteratureArticle.authors,
                LiteratureArticle.published_at,
                LiteratureArticle.peer_review_status,
                LiteratureArticle.integrity_status,
                LiteratureArticle.publication_status,
            )
        )
        .where(LiteratureSignalArticleLink.id > after_id)
        .order_by(LiteratureSignalArticleLink.id)
        .limit(batch_size)
    )
    if article_ids is not None:
        # Read every link for a touched gap so an untouched, published article
        # can still keep that gap covered. Only scoped links are mutated below.
        touched_gaps = select(LiteratureSignalArticleLink.gap_id).where(
            LiteratureSignalArticleLink.article_id.in_(article_ids),
            LiteratureSignalArticleLink.gap_id.is_not(None),
        ).correlate(None)
        statement = statement.where(or_(
            LiteratureSignalArticleLink.article_id.in_(article_ids),
            LiteratureSignalArticleLink.gap_id.in_(touched_gaps),
        ))
    return statement

def _article_reconciliation_statement(
    *, after_id: int = 0, batch_size: int = 100, article_ids: tuple[str, ...] | None = None,
):
    statement = (
        select(LiteratureArticle)
        .options(
            Load(LiteratureArticle).load_only(
                LiteratureArticle.id,
                LiteratureArticle.article_id,
                LiteratureArticle.title,
                LiteratureArticle.doi,
                LiteratureArticle.pmid,
                LiteratureArticle.pmcid,
                LiteratureArticle.journal,
                LiteratureArticle.authors,
                LiteratureArticle.published_at,
                LiteratureArticle.peer_review_status,
                LiteratureArticle.integrity_status,
                LiteratureArticle.discovery_score,
                LiteratureArticle.publication_status,
                LiteratureArticle.metadata_,
            )
        )
        .where(
            LiteratureArticle.publication_status.in_(("review", "published")),
            or_(
                LiteratureArticle.publication_status == "published",
                func.coalesce(LiteratureArticle.metadata_["initial_screen"]["status"].as_string(), "") != "needs_more_evidence",
                func.length(func.trim(func.coalesce(LiteratureArticle.abstract_text, ""))) >= 80,
            ),
            LiteratureArticle.id > after_id,
        )
        .order_by(LiteratureArticle.id)
        .limit(batch_size)
    )
    return statement.where(LiteratureArticle.article_id.in_(article_ids)) if article_ids is not None else statement

def _summary_reconciliation_statement(
    *, after_id: int = 0, batch_size: int = 100, article_ids: tuple[str, ...] | None = None,
):
    statement = (
        select(LiteratureSummary, LiteratureArticle)
        .join(
            LiteratureArticle,
            LiteratureArticle.article_id == LiteratureSummary.article_id,
        )
        .options(
            Load(LiteratureArticle).load_only(
                LiteratureArticle.article_id,
                LiteratureArticle.title,
                LiteratureArticle.doi,
                LiteratureArticle.journal,
                LiteratureArticle.published_at,
                LiteratureArticle.abstract_text,
                LiteratureArticle.integrity_status,
                LiteratureArticle.publication_status,
            )
        )
        .where(
            LiteratureSummary.status.in_(("review", "published", "archived")),
            LiteratureSummary.id > after_id,
        )
        .order_by(LiteratureSummary.id)
        .limit(batch_size)
    )
    return statement.where(LiteratureSummary.article_id.in_(article_ids)) if article_ids is not None else statement

def _english_summary_fingerprint_statement(
    *, after_id: int = 0, batch_size: int = 100, article_ids: tuple[str, ...] | None = None,
):
    statement = (
        select(
            LiteratureSummary.id,
            LiteratureSummary.article_id,
            *(getattr(LiteratureSummary, field) for field in SUMMARY_FIELDS),
        )
        .where(
            LiteratureSummary.language == "en",
            LiteratureSummary.status.in_(("review", "published", "archived")),
            LiteratureSummary.id > after_id,
        )
        .order_by(LiteratureSummary.id)
        .limit(batch_size)
    )
    return statement.where(LiteratureSummary.article_id.in_(article_ids)) if article_ids is not None else statement
