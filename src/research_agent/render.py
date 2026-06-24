"""Render a Brief to (a) an analyst-readable Markdown document and (b) JSON.

The Markdown is the artifact a PM/analyst opens; the JSON is the structured
backbone for any downstream use.
"""
from __future__ import annotations

import json
from pathlib import Path

from .schema import Brief, FetchStatus

_STATUS_ICON = {
    FetchStatus.OK: "✅ ok",
    FetchStatus.PAYWALL: "🔒 paywall",
    FetchStatus.EMPTY: "⚠️ empty",
    FetchStatus.JS_REQUIRED: "🧩 js-required",
    FetchStatus.TIMEOUT: "⏱️ timeout",
    FetchStatus.HTTP_ERROR: "🚫 http-error",
    FetchStatus.FETCH_ERROR: "❌ fetch-error",
}


def render_markdown(brief: Brief) -> str:
    s = brief.synthesis or {}
    lines: list[str] = []
    a = lines.append

    a(f"# Research Brief: {brief.topic}")
    a("")
    inferred = " _(topic inferred from sources)_" if brief.topic_inferred else ""
    a(f"_Generated {brief.generated_at} · {brief.provider}/{brief.model}_{inferred}")
    a("")

    # Sources table — fetch status is part of the story (graceful failure).
    a("## Sources")
    a("")
    a("| ID | Source | Status | Words | Claims |")
    a("| --- | --- | --- | ---: | ---: |")
    for src in brief.sources:
        n_claims = sum(1 for c in brief.claims if c.source_id == src.id)
        title = (src.title or src.url).replace("|", "\\|")
        if src.status.usable and not src.on_topic:
            status = "⚠️ off-topic"
        else:
            status = _STATUS_ICON.get(src.status, src.status.value)
        note = f" — {src.error}" if src.error and not src.status.usable else ""
        a(f"| {src.id} | [{title}]({src.url}) | {status}{note} | {src.word_count} | {n_claims} |")
    a("")

    # Surface off-topic sources prominently so the reader doesn't trust them.
    off_topic = [s2 for s2 in brief.sources if s2.status.usable and not s2.on_topic]
    if off_topic:
        a("> ⚠️ **Source relevance warning.** The content fetched for the following "
          "source(s) does not appear to be about the topic and was **excluded from the "
          "analysis** — verify the URL (a redirect or locale may have served the wrong page):")
        for s2 in off_topic:
            why = f" — {s2.relevance_note}" if s2.relevance_note else ""
            a(f"> - **{s2.id}** [{(s2.title or s2.url)}]({s2.url}){why}")
        a("")

    if s.get("executive_summary"):
        a("## Executive Summary")
        a("")
        a(s["executive_summary"])
        a("")

    _bullets(a, "Consensus Findings", s.get("consensus_findings"),
             empty="No multi-source consensus identified.")
    _bullets(a, "Contradictions", s.get("contradictions"),
             empty="No direct contradictions detected across sources.")
    _bullets(a, "Outlier Claims (single source)", s.get("outlier_claims"),
             empty="No notable single-source outliers.")
    _bullets(a, "Gaps & Open Questions", s.get("gaps"),
             empty="All coverage sub-questions were addressed by at least one source.")

    if s.get("bottom_line"):
        a("## Bottom Line")
        a("")
        a(s["bottom_line"])
        a("")

    # Coverage map — explicit which sub-question each source addresses.
    if brief.coverage:
        a("## Coverage Map")
        a("")
        a("| Sub-question | Addressed by |")
        a("| --- | --- |")
        for cov in brief.coverage:
            who = ", ".join(cov.addressed_by) if cov.addressed_by else "**— none (gap)**"
            q = cov.question.replace("|", "\\|")
            a(f"| {q} | {who} |")
        a("")

    # Appendix: claims + verbatim quotes so every citation is checkable.
    a("## Appendix — Extracted Claims & Citations")
    a("")
    for src in brief.sources:
        src_claims = [c for c in brief.claims if c.source_id == src.id]
        if not src_claims:
            continue
        a(f"### {src.id} — {src.title or src.url}")
        a("")
        for c in src_claims:
            flag = "" if c.verified else " _(quote unverified)_"
            a(f"- **[{c.claim_id}]** ({c.claim_type}) {c.text}{flag}")
            if c.supporting_quote:
                a(f"  > {c.supporting_quote}")
        a("")

    a("---")
    a("")
    a("### Methodology & Limitations")
    a("")
    a("- **Pipeline:** fetch → per-source claim extraction → coverage sub-questions "
      "→ cross-source alignment (consensus/contradiction/outlier + gaps) → synthesis.")
    a("- **Claims** are atomic assertions; each carries a verbatim quote, verified by "
      "substring match against the fetched text. Unverified quotes are flagged above.")
    a("- **Relevance:** sources whose fetched content is not about the topic (e.g. a "
      "redirect or locale served the wrong page) are detected, excluded from the "
      "analysis, and flagged in the Sources section.")
    a("- **Consensus** = a proposition supported by ≥2 distinct sources; **outlier** = "
      "asserted by exactly one source; **gap** = a coverage sub-question no source addresses.")
    a("- Reasoning is limited to the supplied URLs and to text statically extractable "
      "from them; paywalled or JS-rendered sources may be partially or wholly missing "
      "(see status column).")
    a("")
    return "\n".join(lines)


def _bullets(a, heading: str, items, *, empty: str) -> None:
    a(f"## {heading}")
    a("")
    if items and isinstance(items, list):
        for it in items:
            if str(it).strip():
                a(f"- {it}")
    else:
        a(f"_{empty}_")
    a("")


def write_outputs(brief: Brief, run_dir: Path) -> tuple[Path, Path]:
    md_path = run_dir / "brief.md"
    json_path = run_dir / "brief.json"
    md_path.write_text(render_markdown(brief), encoding="utf-8")
    json_path.write_text(
        json.dumps(brief.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return md_path, json_path
