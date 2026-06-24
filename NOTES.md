# NOTES

## Approach
I treated the headline criterion — *reasoning across sources* — as the thing to
protect, and built a **6-stage pipeline** around it rather than one mega-prompt:
fetch → topic → per-source claim extraction → coverage sub-questions →
cross-source alignment → synthesis → render. Decomposition buys two things:
(1) each LLM call has one job and a strict JSON contract, so a weak/free model
(Gemini Flash) stays reliable; (2) every intermediate artifact is inspectable,
so you can see *why* the brief says what it says.

The reasoning is kept **grounded**: claims must carry a verbatim quote (verified
by substring match against the source — hallucinated quotes are flagged), and the
cross-source step may only reference `claim_id`s that actually exist, so it can't
invent agreement or conflict.

## Key design decisions
- **Atomic claim + verified quote** as the unit. Makes consensus/contradiction a
  claim-to-claim comparison and makes every citation checkable.
- **Gaps via a coverage model.** To flag "what *no* source addresses" you need a
  reference frame, so the agent first generates the sub-questions a good brief
  should answer, then marks which are unanswered.
- **Pluggable LLM over plain REST (httpx), no SDKs.** Gemini/Groq/OpenAI/
  Anthropic/Ollama swap via env vars. Keeps deps to httpx + trafilatura and makes
  the model choice a non-decision.
- **Failures are data.** Each source is classified and the run continues on the
  survivors; an interactive gate offers a tougher retry. (Yale 403s even with a
  Googlebot UA in testing — a real graceful-degradation case, not a contrived one.)
- **Disk cache** for fetches and LLM calls → free, deterministic reruns for reviewers.

## Assumptions (the brief left these to me)
- Closed source set — reason only over supplied URLs; no open-web discovery.
- Claim granularity = one proposition each; ≤12 claims/source.
- Consensus = ≥2 distinct sources; outlier = exactly one; gap = sub-question no
  source addresses.
- Topic is inferred when not passed (`links.txt` had URLs only).

## If this were going to production
- **JS/paywall fetching:** add a headless-browser fallback (Playwright) + Readability,
  and per-domain adapters; static extraction loses JS-rendered and hard-paywalled pages.
- **Content-relevance / locale validation:** a fetched 200 can still be the *wrong*
  content — geolocated redirects can serve a different-language or off-topic page for
  the requested URL (observed in testing: a Conversation article URL returned an
  Indonesian health page). *Implemented* a basic guard — the extraction step judges
  `on_topic` and the brief excludes + flags off-topic sources. It is precise when a
  specific `--topic` is given (correctly excluded 4/5 unrelated sources in testing),
  but with *inferred* topics on an incoherent URL set, inference can produce a vague
  umbrella broad enough that nothing is flagged — though the reasoning layer still
  honestly reports "no consensus / no contradictions, seek more sources." Next: a
  source-coherence check (cluster claims; warn when sources form disjoint clusters)
  plus language detection running *before* topic inference.
- **Verification:** embedding-based claim de-duplication and an NLI model to
  corroborate the LLM's contradiction calls; confidence scores per finding.
- **Eval harness:** a labeled set of source bundles with known contradictions to
  measure precision/recall on flagging, plus regression tests on prompts.
- **Robustness:** schema validation (pydantic), retries/backoff + rate-limit handling,
  concurrency for fetch and per-source extraction, observability on cost/tokens.

## Tradeoffs made for the ~2-hour budget
- Static fetching only (no headless browser) — accepted graceful degradation instead.
- JSON contracts are parsed defensively but not schema-validated.
- Stages run sequentially (no async) — fine at 3–5 sources.
- Contradiction detection leans on a well-constrained prompt rather than a separate
  NLI verifier — the highest-leverage place I'd add rigor next.
