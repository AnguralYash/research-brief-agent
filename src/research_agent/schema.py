"""Dataclasses describing the pipeline's data at every stage.

These are the contract between stages and the backbone of `brief.json`.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class FetchStatus(str, Enum):
    OK = "ok"
    PAYWALL = "paywall"
    EMPTY = "empty"
    JS_REQUIRED = "js_required"
    TIMEOUT = "timeout"
    HTTP_ERROR = "http_error"
    FETCH_ERROR = "fetch_error"

    @property
    def usable(self) -> bool:
        return self is FetchStatus.OK


@dataclass
class SourceDoc:
    """One fetched source. `id` is a stable short handle like 'S1' used in citations."""
    id: str
    url: str
    status: FetchStatus = FetchStatus.FETCH_ERROR
    title: str = ""
    author: str = ""
    date: str = ""
    text: str = ""
    word_count: int = 0
    error: str = ""
    # Set during claim extraction: did the fetched content actually match the topic?
    # A 200 response can still be the wrong page (geolocated redirect, error page,
    # wrong-language content), so we flag it rather than silently trusting it.
    on_topic: bool = True
    relevance_note: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        d.pop("text", None)  # text is large; excluded from the summary dict
        return d


@dataclass
class Claim:
    """An atomic assertion a single source makes about the topic."""
    claim_id: str            # e.g. "S1-C3"
    source_id: str           # e.g. "S1"
    text: str                # normalized one-sentence assertion
    supporting_quote: str    # verbatim span from the source
    claim_type: str = "factual"   # factual | predictive | normative | statistical
    tag: str = ""            # short sub-topic label
    verified: bool = False   # supporting_quote actually found in source text?

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Theme:
    """A cluster of claims about the same underlying proposition."""
    theme_id: str
    title: str
    consensus: list[dict[str, Any]] = field(default_factory=list)
    contradictions: list[dict[str, Any]] = field(default_factory=list)
    outliers: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Coverage:
    """A sub-question and which sources (if any) address it."""
    question: str
    addressed_by: list[str] = field(default_factory=list)  # source ids
    note: str = ""

    @property
    def is_gap(self) -> bool:
        return len(self.addressed_by) == 0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["is_gap"] = self.is_gap
        return d


@dataclass
class Brief:
    """The full structured result — serialized verbatim to brief.json."""
    topic: str
    topic_inferred: bool
    generated_at: str
    provider: str
    model: str
    sources: list[SourceDoc] = field(default_factory=list)
    claims: list[Claim] = field(default_factory=list)
    sub_questions: list[str] = field(default_factory=list)
    themes: list[Theme] = field(default_factory=list)
    coverage: list[Coverage] = field(default_factory=list)
    synthesis: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "topic_inferred": self.topic_inferred,
            "generated_at": self.generated_at,
            "provider": self.provider,
            "model": self.model,
            "sources": [s.to_dict() for s in self.sources],
            "claims": [c.to_dict() for c in self.claims],
            "sub_questions": self.sub_questions,
            "themes": [t.to_dict() for t in self.themes],
            "coverage": [c.to_dict() for c in self.coverage],
            "synthesis": self.synthesis,
        }
