# NOTES

## Approach
I protected the headline criterion — *reasoning across sources* — with a **6-stage
pipeline** (fetch → topic → per-source claim extraction → coverage sub-questions →
cross-source alignment → synthesis → render) rather than one mega-prompt. Each LLM
call has one job and a strict JSON contract, so even a free/fast model stays
reliable, and every intermediate artifact is inspectable. Reasoning is **grounded**:
each claim carries a verbatim quote verified in code, and the cross-source step may
only cite `claim_id`s that exist — so it can't invent agreement or conflict.

## Key decisions
- **Atomic claim + verified quote** as the unit → consensus/contradiction is a
  claim-to-claim comparison, and every citation is checkable.
- **Gaps via a coverage model** — generate the sub-questions a good brief should
  answer, then flag the unanswered ones ("what no source addresses").
- **Pluggable LLM over plain REST** (Gemini/Groq/OpenAI/Anthropic/Ollama via env) —
  deps stay `httpx` + `trafilatura`; model choice is a non-decision.
- **Failures are data** — sources are classified and the run continues on the
  survivors, with an interactive retry gate; off-topic/wrong-content sources are
  excluded and flagged. Disk cache makes reruns free and deterministic.

## If this were going to production
- Headless-browser fallback (Playwright) for JS-heavy / paywalled pages.
- NLI or embeddings to corroborate contradiction calls + per-finding confidence, and
  an eval harness with labeled contradiction sets.
- Source-coherence + language checks *before* topic inference; schema validation,
  concurrent fetch/extraction, and cost/token observability.

## Tradeoffs for the ~2-hour budget
- Static fetch only (no JS rendering) — chose graceful degradation over a half-built
  browser layer.
- Sequential stages; JSON parsed defensively but not schema-validated.
- Contradiction detection via a tightly-constrained prompt rather than a separate
  verifier — the first place I'd add rigor.
