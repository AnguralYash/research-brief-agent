"""The LLM-driven stages of the pipeline.

Each function is one decomposed step. They take plain data in and return plain
data out so the pipeline can persist every intermediate artifact.
"""
from __future__ import annotations

import re

from . import prompts
from .llm import LLMClient
from .schema import Claim, Coverage, SourceDoc, Theme

# How much source text to send to the extractor. Generous enough to capture an
# article's substance, bounded to stay within free-tier context/cost.
MAX_SOURCE_CHARS = 14000


def _norm(s: str) -> str:
    """Lowercase + collapse whitespace, for fuzzy quote verification."""
    return re.sub(r"\s+", " ", s).lower().strip()


# ---- Stage 1: topic inference ------------------------------------------
def infer_topic(client: LLMClient, sources: list[SourceDoc]) -> str:
    usable = [s for s in sources if s.status.usable]
    snippets = [
        {"id": s.id, "title": s.title or s.url, "lead": s.text[:500]}
        for s in usable
    ]
    if not snippets:
        return "Unknown topic (no sources could be read)"
    obj = client.complete_json(prompts.SYSTEM, prompts.topic_inference_prompt(snippets))
    return (obj.get("topic") or "").strip() or "Unknown topic"


# ---- Stage 2: per-source claim extraction ------------------------------
def extract_claims(client: LLMClient, topic: str, source: SourceDoc) -> list[Claim]:
    text = source.text[:MAX_SOURCE_CHARS]
    obj = client.complete_json(
        prompts.SYSTEM,
        prompts.claim_extraction_prompt(topic, source.id, source.title or source.url, text),
    )
    # Capture the relevance judgment and record it on the source (default: on-topic).
    if isinstance(obj, dict):
        source.on_topic = obj.get("on_topic", True) is not False
        source.relevance_note = (obj.get("relevance_note") or "").strip()
    # Off-topic content is excluded from the analysis (the brief flags it instead).
    if not source.on_topic:
        return []
    raw_claims = obj.get("claims", []) if isinstance(obj, dict) else []
    source_norm = _norm(source.text)
    claims: list[Claim] = []
    for i, rc in enumerate(raw_claims, start=1):
        if not isinstance(rc, dict) or not rc.get("text"):
            continue
        quote = (rc.get("supporting_quote") or "").strip()
        verified = bool(quote) and _norm(quote)[:200] in source_norm
        claims.append(
            Claim(
                claim_id=f"{source.id}-C{i}",
                source_id=source.id,
                text=rc["text"].strip(),
                supporting_quote=quote,
                claim_type=(rc.get("claim_type") or "factual").strip(),
                tag=(rc.get("tag") or "").strip(),
                verified=verified,
            )
        )
    return claims


# ---- Stage 3: sub-question generation ----------------------------------
def generate_sub_questions(client: LLMClient, topic: str) -> list[str]:
    obj = client.complete_json(prompts.SYSTEM, prompts.subquestions_prompt(topic))
    qs = obj.get("sub_questions", []) if isinstance(obj, dict) else []
    return [q.strip() for q in qs if isinstance(q, str) and q.strip()]


# ---- Stage 4: cross-source alignment -----------------------------------
def align_claims(
    client: LLMClient, topic: str, sub_questions: list[str], claims: list[Claim]
) -> tuple[list[Theme], list[Coverage]]:
    claim_dicts = [
        {
            "claim_id": c.claim_id,
            "source_id": c.source_id,
            "text": c.text,
            "tag": c.tag,
            "claim_type": c.claim_type,
        }
        for c in claims
    ]
    obj = client.complete_json(
        prompts.SYSTEM, prompts.alignment_prompt(topic, sub_questions, claim_dicts)
    )
    themes: list[Theme] = []
    for i, t in enumerate(obj.get("themes", []) if isinstance(obj, dict) else [], start=1):
        if not isinstance(t, dict):
            continue
        themes.append(
            Theme(
                theme_id=f"T{i}",
                title=(t.get("title") or f"Theme {i}").strip(),
                consensus=_as_list(t.get("consensus")),
                contradictions=_as_list(t.get("contradictions")),
                outliers=_as_list(t.get("outliers")),
            )
        )
    coverage: list[Coverage] = []
    for c in obj.get("coverage", []) if isinstance(obj, dict) else []:
        if not isinstance(c, dict) or not c.get("question"):
            continue
        coverage.append(
            Coverage(
                question=c["question"].strip(),
                addressed_by=[s for s in _as_list(c.get("addressed_by")) if isinstance(s, str)],
                note=(c.get("note") or "").strip(),
            )
        )
    return themes, coverage


# ---- Stage 5: synthesis -------------------------------------------------
def synthesize(
    client: LLMClient, topic: str, themes: list[Theme], coverage: list[Coverage],
    sources: list[SourceDoc]
) -> dict:
    alignment = {
        "themes": [t.to_dict() for t in themes],
        "coverage": [c.to_dict() for c in coverage],
    }
    source_index = [{"id": s.id, "title": s.title or s.url} for s in sources if s.status.usable]
    obj = client.complete_json(
        prompts.SYSTEM, prompts.synthesis_prompt(topic, alignment, source_index)
    )
    return obj if isinstance(obj, dict) else {}


def _as_list(v) -> list:
    return v if isinstance(v, list) else []
