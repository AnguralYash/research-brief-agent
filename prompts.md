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

I built this with Claude Code (Opus). The thing I'd want a reader to take away is
that I spent the first chunk of the session *not coding* — pinning down
requirements and the decisions the brief deliberately left open — and the rest in
a tight build → run-against-reality → fix loop.

Two views below: **(A) the actual prompts I gave, verbatim and in order**, so you
can see how I actually drove the model; and **(B) a short narrative** of what each
phase produced.

### A. The prompts I gave, in order

> Lightly tidied for typos/casing — wording and intent unchanged. The bold note
> after each is the reasoning behind the prompt.

1. *"Review the document in the downloads/S3 folder and check out the task."*
   **Started by orienting on the brief + sample `links.txt`, not by coding.**
2. *"Before proceeding, let's do the requirements analysis and plan the
   implementation. Gather the requirements and surface the questions that need
   clarification."*
   **The pivotal steer: requirements and open decisions before a line of code.**
3. *"I don't have API keys for any provider right now — which one is configurable
   for free?"*
   **Turned a constraint into a design decision: a pluggable LLM layer with a free
   Gemini default, so the model choice is swappable, not load-bearing.**
4. On the architecture-shaping choices (decided explicitly, documented as assumptions):
   - topic: *"Would the hybrid approach still work on a model like Gemini rather than
     a frontier one? If so, let's go hybrid."* — **checked the design held on a weak
     model before committing.**
   - gaps: chose the **sub-question coverage model** — the only principled way to
     detect "what *no* source addresses."
   - fetch: *"Let's keep it simple — the agent should flag failed sources and ask
     whether to retry them."* — **became the interactive retry gate.**
   - reproducibility: chose **disk cache + a committed sample**.
5. *"Approve the plan and implement it in dependency order."*
   **Locked an agreed plan before building, then went scaffold → fetch → LLM → stages
   → render → CLI.**
6. *"Here's the API key: [REDACTED]."*
   **First real end-to-end run, on the actual sample URLs.**
7. *"How do I test this locally before proceeding?"*
   **Insisted on verifying against a real run before moving on — the live runs are
   exactly what exposed the fatal LLM-timeout and per-source-failure bugs, which I
   then had fixed.**
8. *"Let's test it on other URL sets too, to check robustness."*
   **Didn't stop at the happy path — drove stress tests on a fresh topic and a
   deliberately incoherent mix of unrelated URLs.**
9. *"Can we improve the case where fetched content doesn't match the topic? Add a
   note in the output so the user knows, rather than silently relying on it."*
   **Spotted a real gap from testing and turned it into the relevance guard.**
10. *"Does this cover everything in the task doc — good to go?"*
    **A deliberate checkpoint: audit the build against the brief's requirements
    before calling it done.**

### B. What each phase produced

#### 1. Frame the task before touching code
My first instruction was essentially *"review the brief and figure out the task"* —
not *"build it."* I had the model read the brief + the sample `links.txt`, then asked
it to do a **requirements-analysis pass first**: separate the functional
requirements (fetch/parse, extract claims, find contradictions + the three gap
types, consensus-vs-outlier with citations) from the evaluation criteria
(multi-step design, real tool use, cross-source reasoning, graceful failure, scope
judgment), and surface the choices the brief refuses to make for you.

> My exact steer: *"before proceeding, let's do the requirement analysis and plan
> the implementation accordingly, gather the requirements and put questions that
> need clarification."*

#### 2. Force the open questions to the surface, then lock them
The brief is explicit that the design choices are the test. So I resolved the
architecture-shaping ones up front and recorded them as documented assumptions:

| Decision | Choice | Why |
| --- | --- | --- |
| Language / stack | Python, `httpx` + `trafilatura` only | best parse ecosystem; minimal deps |
| Model | pluggable, free Gemini default | I had no paid keys; wanted model to be a non-decision |
| Topic | **hybrid** — use `--topic`, else infer | sample had URLs but no topic |
| Gap detection | **sub-question coverage model** | the only principled way to flag "what *no* source addresses" |
| Fetch robustness | graceful degradation + **interactive retry gate** | failure handling is itself graded |
| Reproducibility | disk cache + committed sample | reviewers can re-run for free |
| Output | Markdown brief + structured JSON | human- and machine-readable |

I also pushed back on myself on scope: deliberately a **complete static-fetch
pipeline** rather than a half-built headless-browser one.

#### 3. Plan, then implement in dependency order
I had the model write a plan to a file and approve it before coding, then built in
order: scaffold (config/schema) → fetch layer → pluggable LLM client → prompts →
stages → pipeline → render → CLI → docs + tests.

#### 4. Run against reality early and often
This is where most of the real work happened — the model and I iterated on failures
the moment they appeared, rather than after a big-bang build:

- **`lxml.html.clean` packaging split** — `trafilatura` import blew up; fixed by
  adding `lxml_html_clean` to requirements.
- **Fetched the real 5 URLs before spending any API quota** — found Yale hard-403s
  even with a Googlebot UA (kept it as a genuine graceful-degradation case), the
  other four parse cleanly.
- **Proved the pipeline wiring with a mock LLM** before using a real key, so the
  first paid run wasn't also the first integration test.
- **Gemini model availability churn** — `gemini-2.5-flash` returned 503 (overloaded)
  and `gemini-2.0-flash` had a literal `limit: 0` free quota; I probed the models
  endpoint and switched to `gemini-3-flash-preview`. This is exactly *why* the LLM
  client is pluggable, and it's my "one thing that didn't work" for the Loom.
- **A fatal LLM read-timeout** surfaced on a live `--no-cache` run (the big
  Brookings extraction). Made network timeouts/transport errors **retryable** with
  backoff instead of fatal.
- **Per-source resilience** — a 429 on one source's extraction was aborting the
  whole run; changed Stage 2 to skip a failing source and continue, mirroring how
  fetch already degrades. Added a clean top-level exit for unrecoverable LLM errors.

#### 5. Stress-test on unfamiliar topics
I didn't stop at the sample. I ran fresh topics (four-day-week / remote work, then a
deliberately *incoherent* mix of coffee/nuclear/black-holes/cake/Vikings) to probe
robustness. Two findings came out of this:

- A source can return **HTTP 200 with the wrong content** — a geolocated redirect
  served an Indonesian health article for a four-day-week URL. The agent extracted
  it faithfully but the result was misleading.
- On a deliberately unrelated URL set, the reasoning layer **correctly refused to
  fabricate** consensus/contradictions and reported "no overlap, seek more sources."

In response I added a **relevance guard**: the extraction step now first judges
whether the page is actually on-topic and, if not, excludes it and flags it in the
brief — verified live (it correctly excluded 4/5 unrelated sources when given a
specific topic). I deliberately stopped there rather than building full
source-coherence clustering — that's a documented production improvement, and
knowing where to stop is part of the scope judgment the brief grades.

### Prompting principles I applied to the agent's own design
- **Decompose; don't one-shot.** Each LLM call has a single responsibility and a
  strict JSON contract, which is also what keeps a free/fast model reliable.
- **Constrain what the model may cite.** The cross-source step may only reference
  `claim_id`s that exist in its input — this is what turns "summarize" into
  "reason across" and stops it inventing agreement or conflict.
- **Make the model's claims falsifiable.** Every claim carries a verbatim quote
  that is verified in code against the source text; unverifiable quotes are flagged.
