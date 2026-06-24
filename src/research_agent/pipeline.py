"""Orchestrates the multi-stage pipeline and persists every intermediate artifact.

The CLI owns I/O and the interactive retry gate; this module owns the staged
reasoning given an already-fetched set of sources.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from . import stages
from .config import Config
from .llm import LLMClient
from .schema import Brief, Claim, SourceDoc


def _log(msg: str) -> None:
    print(f"  · {msg}", flush=True)


def run_pipeline(
    sources: list[SourceDoc],
    *,
    topic: str | None,
    config: Config,
    client: LLMClient,
    run_dir: Path,
) -> Brief:
    """Run stages 1-5 over fetched sources and return a populated Brief."""
    run_dir.mkdir(parents=True, exist_ok=True)
    usable = [s for s in sources if s.status.usable]

    # Stage 1 — topic
    topic_inferred = False
    if topic:
        _log(f"Topic (provided): {topic}")
    else:
        _log("Inferring topic from sources…")
        topic = stages.infer_topic(client, sources)
        topic_inferred = True
        _log(f"Topic (inferred): {topic}")

    # Stage 2 — per-source claim extraction. One source failing (timeout, bad
    # JSON, etc.) must NOT abort the whole run — skip it and carry on, mirroring
    # how fetch degrades gracefully.
    all_claims: list[Claim] = []
    for s in usable:
        try:
            claims = stages.extract_claims(client, topic, s)
        except Exception as exc:  # noqa: BLE001 - resilience over precision here
            _log(f"{s.id}: claim extraction failed ({type(exc).__name__}: {exc}) — skipping")
            continue
        if not s.on_topic:
            _log(f"{s.id}: off-topic content — excluded ({s.relevance_note or 'not about the topic'})")
            continue
        all_claims.extend(claims)
        verified = sum(1 for c in claims if c.verified)
        _log(f"{s.id}: extracted {len(claims)} claims ({verified} quote-verified)")
    _dump(run_dir / "claims.json", [c.to_dict() for c in all_claims])

    # Stage 3 — coverage frame
    _log("Generating coverage sub-questions…")
    sub_questions = stages.generate_sub_questions(client, topic)
    _log(f"{len(sub_questions)} sub-questions")

    # Stage 4 — cross-source alignment (the core reasoning)
    themes, coverage = [], []
    if all_claims:
        _log("Reasoning across sources (themes, contradictions, gaps)…")
        themes, coverage = stages.align_claims(client, topic, sub_questions, all_claims)
        gaps = sum(1 for c in coverage if c.is_gap)
        _log(f"{len(themes)} themes; {gaps} uncovered sub-question(s)")
    else:
        _log("No claims extracted — skipping alignment.")

    # Stage 5 — synthesis prose
    synthesis = {}
    if themes or coverage:
        _log("Synthesizing brief…")
        synthesis = stages.synthesize(client, topic, themes, coverage, sources)

    brief = Brief(
        topic=topic,
        topic_inferred=topic_inferred,
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        provider=config.provider,
        model=config.model,
        sources=sources,
        claims=all_claims,
        sub_questions=sub_questions,
        themes=themes,
        coverage=coverage,
        synthesis=synthesis,
    )
    _log(f"Real LLM calls this run: {client.calls} (rest served from cache)")
    return brief


def _dump(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
