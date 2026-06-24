"""Prompt templates for each LLM stage.

Design principles baked into these prompts:
  * One job per prompt (decomposition over one mega-prompt).
  * Force grounding: claims must carry a verbatim quote; cross-source reasoning
    must reference claim_ids, not invent free-floating statements.
  * Strict JSON contracts so stages compose mechanically.
These strings are also surfaced in prompts.md for the submission.
"""
from __future__ import annotations

import json

# Shared system instruction: keeps the model terse, grounded, and JSON-only.
SYSTEM = (
    "You are a meticulous research analyst assembling a cross-source brief. "
    "You are rigorous about attribution, never invent facts, and you clearly "
    "separate what sources actually say from your own inference. "
    "When asked for JSON, you output ONLY valid JSON — no prose, no code fences."
)


def topic_inference_prompt(snippets: list[dict]) -> str:
    """Infer a concise topic from source titles + leading text."""
    blob = "\n\n".join(
        f"[{s['id']}] {s['title']}\n{s['lead']}" for s in snippets
    )
    return (
        "Below are the titles and opening text of several sources a user wants "
        "summarized together. In one clear phrase (max ~12 words), state the common "
        "topic they all address.\n\n"
        f"{blob}\n\n"
        'Return JSON: {"topic": "<the topic phrase>"}'
    )


def claim_extraction_prompt(topic: str, source_id: str, title: str, text: str) -> str:
    """Extract atomic claims from ONE source. The text is pre-truncated by the caller."""
    return (
        f'TOPIC: "{topic}"\n'
        f"SOURCE: {source_id} — {title}\n"
        "------- SOURCE TEXT START -------\n"
        f"{text}\n"
        "------- SOURCE TEXT END -------\n\n"
        "FIRST, judge whether this page is actually about the topic. A page can load "
        "successfully yet be the wrong content — an error/login page, a different-language "
        "page, or an unrelated article served by a redirect. If the text is clearly NOT "
        "about the topic, set on_topic=false, give a one-line reason, and return an empty "
        "claims list.\n\n"
        "THEN, if on_topic, extract the key CLAIMS this source makes that are relevant to "
        "the topic. Rules:\n"
        "  - A claim is a single, self-contained assertion (one proposition). Split "
        "compound sentences into separate claims.\n"
        "  - Capture substantive claims only: facts, statistics, predictions, causal "
        "or normative assertions about the topic. Skip filler, anecdotes, and meta text.\n"
        "  - For each claim, include a VERBATIM supporting_quote copied exactly from the "
        "source text above (<=300 chars). Do not paraphrase the quote.\n"
        "  - claim_type is one of: factual, statistical, predictive, normative.\n"
        "  - tag is a 2-4 word sub-topic label so similar claims across sources can be grouped.\n"
        "  - Extract at most 12 claims; prefer the most important.\n\n"
        "Return JSON of this exact shape:\n"
        '{"on_topic": true, "relevance_note": "", '
        '"claims": [{"text": "...", "supporting_quote": "...", '
        '"claim_type": "factual", "tag": "..."}]}'
    )


def subquestions_prompt(topic: str) -> str:
    """Generate the coverage frame: questions a good brief on this topic must answer."""
    return (
        f'TOPIC: "{topic}"\n\n'
        "List the 6-9 most important sub-questions that a rigorous analyst brief on "
        "this topic should answer. These define what 'good coverage' means — they will "
        "be used to detect gaps where NO source speaks. Make them specific and "
        "non-overlapping.\n\n"
        'Return JSON: {"sub_questions": ["...", "..."]}'
    )


def alignment_prompt(topic: str, sub_questions: list[str], claims: list[dict]) -> str:
    """The core cross-source reasoning step: cluster, compare, find gaps."""
    claims_json = json.dumps(claims, ensure_ascii=False, indent=0)
    sq_json = json.dumps(sub_questions, ensure_ascii=False)
    return (
        f'TOPIC: "{topic}"\n\n'
        "You are given (A) a list of CLAIMS extracted from multiple sources, each "
        "tagged with its source_id and claim_id, and (B) a list of SUB_QUESTIONS that "
        "define good coverage of the topic.\n\n"
        f"SUB_QUESTIONS:\n{sq_json}\n\n"
        f"CLAIMS:\n{claims_json}\n\n"
        "Reason ACROSS sources. Do the following:\n"
        "1. Group claims that speak to the same underlying proposition into THEMES.\n"
        "2. Within each theme, classify the relationships using ONLY the claim_ids given:\n"
        "   - consensus: a proposition supported by claims from >=2 DIFFERENT sources. "
        "List the claim_ids and the source_ids that agree.\n"
        "   - contradiction: two or more claims that genuinely conflict (one asserts X, "
        "another asserts not-X or an incompatible value). Give the opposing sides with "
        "their claim_ids/source_ids and a one-line explanation of the conflict.\n"
        "   - outlier: a substantive claim made by exactly ONE source that others neither "
        "support nor contradict.\n"
        "3. Build a coverage map: for each sub-question, list which source_ids address it "
        "(empty list = a GAP no source covers).\n\n"
        "Be conservative: only call something a contradiction if the claims truly conflict, "
        "not merely differ in emphasis or framing. Every claim_id you cite MUST exist in the "
        "input.\n\n"
        "Return JSON of this exact shape:\n"
        "{\n"
        '  "themes": [{\n'
        '     "title": "...",\n'
        '     "consensus": [{"statement": "...", "claim_ids": ["S1-C1","S2-C3"], "sources": ["S1","S2"]}],\n'
        '     "contradictions": [{"summary": "...", "sides": [{"position": "...", "claim_ids": ["S1-C2"], "sources": ["S1"]}, {"position": "...", "claim_ids": ["S3-C1"], "sources": ["S3"]}]}],\n'
        '     "outliers": [{"statement": "...", "claim_id": "S4-C2", "source": "S4"}]\n'
        "  }],\n"
        '  "coverage": [{"question": "...", "addressed_by": ["S1","S2"], "note": "..."}]\n'
        "}"
    )


def synthesis_prompt(topic: str, alignment: dict, source_index: list[dict]) -> str:
    """Turn the structured alignment into analyst-ready prose sections."""
    align_json = json.dumps(alignment, ensure_ascii=False, indent=0)
    src_json = json.dumps(source_index, ensure_ascii=False)
    return (
        f'TOPIC: "{topic}"\n\n'
        f"SOURCES (id -> title): {src_json}\n\n"
        f"STRUCTURED CROSS-SOURCE ANALYSIS:\n{align_json}\n\n"
        "Write the prose for an analyst brief based STRICTLY on the analysis above. "
        "Cite sources inline using their ids in square brackets, e.g. [S1] or [S1][S3]. "
        "Do not introduce facts that aren't represented in the analysis.\n\n"
        "Return JSON with these fields (all strings unless noted):\n"
        "{\n"
        '  "executive_summary": "3-5 sentence overview of where sources agree and disagree",\n'
        '  "consensus_findings": ["bullet sentence with [S#] citations", "..."],\n'
        '  "contradictions": ["bullet describing the conflict and who is on each side with [S#]", "..."],\n'
        '  "outlier_claims": ["bullet: single-source claim with its [S#]", "..."],\n'
        '  "gaps": ["bullet: a sub-question no source addressed", "..."],\n'
        '  "bottom_line": "1-2 sentence so-what for a decision-maker"\n'
        "}"
    )
