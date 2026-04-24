"""Shared structured types for the generic agent workflow."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional


@dataclass
class PlanNode:
    step_key: str
    step_type: str
    title: str
    instruction: str = ""
    depends_on: list[str] = field(default_factory=list)
    search_queries: list[str] = field(default_factory=list)
    target_tables: list[str] = field(default_factory=list)
    action: Optional[str] = None
    parameters: dict[str, Any] = field(default_factory=dict)
    max_results: int = 5
    confidence: float = 0.6
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EvidenceRef:
    evidence_type: str
    source_type: str
    source_name: str
    title: str
    url: Optional[str] = None
    resolved_url: Optional[str] = None
    content_snippet: str = ""
    content_hash: str = ""
    confidence: float = 0.5
    weight: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ActionRequest:
    action: str
    parameters: dict[str, Any] = field(default_factory=dict)
    rationale: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ActionResult:
    action: str
    success: bool
    summary: str
    output: dict[str, Any] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[EvidenceRef] = field(default_factory=list)
    error: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["evidence"] = [item.to_dict() for item in self.evidence]
        return payload


@dataclass
class AgentFinalResult:
    summary: str
    findings: list[dict[str, Any]] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)
    actions_taken: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    run_log_digest: str = ""
    risk_level: str = "medium"
    status: str = "completed"
    confidence: float = 0.5
    evidence_count: int = 0
    step_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
