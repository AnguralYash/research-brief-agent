# Prompts & AI-tool workflow

Two parts, as the brief asks: (1) the prompts the **agent itself** uses at
runtime, and (2) how I **worked with the AI tool** (Claude Code) to build it.

---

## Part 1 — The agent's runtime prompts

All five LLM stages share one system instruction and use strict-JSON task prompts
(source of truth: [`src/research_agent/prompts.py`](src/research_agent/prompts.py)).

**System instruction (all stages):**
> You are a meticulous research analyst assembling a cross-source brief. You are
> rigorous about attribution, never invent facts, and you clearly separate what
> sources actually say from your own inference. When asked for JSON, you output
> ONLY valid JSON — no prose, no code fences.

**Stage 1 — Topic inference.** Given source titles + leads → `{"topic": "..."}`.

**Stage 2 — Per-source claim extraction.** Given the topic + one source's text:
**first judge whether the page is actually about the topic** (a 200 response can
still be an error/login page, a wrong-language page, or an unrelated article served
by a redirect) — if not, set `on_topic=false` with a reason and return no claims.
Otherwise extract atomic claims, each a single proposition, each with a **verbatim**
`supporting_quote` (≤300 chars) copied from the text; typed factual/statistical/
predictive/normative; tagged with a 2–4 word sub-topic; ≤12 claims. Returns
`{"on_topic": true, "relevance_note": "", "claims": [...]}`. Off-topic sources are
excluded from the analysis and flagged in the brief.

**Stage 3 — Coverage sub-questions.** Given the topic: list the 6–9 most important
sub-questions a rigorous brief should answer (the frame for gap detection).

**Stage 4 — Cross-source alignment (core reasoning).** Given all claims (with
`claim_id`/`source_id`) + sub-questions: cluster into themes; classify
**consensus** (≥2 distinct sources), **contradiction** (genuinely conflicting
claims, conservative), **outlier** (single source); map sub-question coverage
(empty = gap). Must reference only `claim_id`s present in the input.

**Stage 5 — Synthesis.** Given the structured alignment + source index: write
exec summary, consensus, contradictions, outliers, gaps, bottom line — inline
`[S#]` citations, no new facts.

> Full, exact prompt text lives in `prompts.py`. Design intent: **one job per
> prompt**, strict JSON contracts so stages compose, and forced grounding
> (verbatim quotes + claim_id references) so even a free/fast model stays honest.

---

## Part 2 — How I built it with Claude Code

> Paste/attach your actual Claude Code session export here before submitting.
> The skeleton of the working session, in order:

1. **Framed the task before coding.** Had the model read the brief + sample
   `links.txt`, then deliberately ran a **requirements-analysis pass** instead of
   jumping to code — enumerating functional vs. evaluation criteria and the
   decisions the brief left open.
2. **Forced the open questions to the surface.** Resolved the genuinely
   architecture-shaping choices explicitly (and documented them as assumptions):
   - topic = hybrid (infer if not provided)
   - gaps = sub-question coverage model
   - fetch = graceful degradation + interactive retry gate
   - reproducibility = disk cache + committed sample
   - model = pluggable, free Gemini default
3. **Locked a plan, then implemented in dependency order:** scaffold → fetch →
   LLM client → stages/pipeline → render → CLI → docs.
4. **Verified continuously against reality:** ran the fetch layer on the real 5
   URLs early (caught Yale's 403 and the `lxml_html_clean` packaging split), then
   ran the whole pipeline end-to-end with a **mock LLM** to prove the wiring
   before spending any API quota.

### Prompting principles I applied to the agent design
- Decompose; don't one-shot. Each call has a single responsibility.
- Constrain the output (JSON shape) and constrain the *inputs the model may cite*
  (claim_ids) — this is what turns "summarize" into "reason across".
- Make the model's job falsifiable: verbatim quotes are checked in code.
