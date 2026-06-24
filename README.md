# Research-and-Summarize Agent

A multi-stage agent that takes a **topic + 3–5 source URLs** and produces a
structured analyst brief: it fetches and parses messy real-world pages, extracts
**atomic claims** from each source, then reasons **across** sources to surface
**consensus**, **contradictions**, single-source **outliers**, and coverage
**gaps** — every claim backed by a verbatim, verified citation.

The hard part isn't summarization; it's cross-source reasoning and knowing what
to flag. That's where the design effort went.

---

## Quick start

```bash
# 1. Install (httpx + trafilatura only)
python3 -m pip install -r requirements.txt

# 2. Get a FREE Gemini API key (no credit card): https://aistudio.google.com/apikey
cp .env.example .env        # then paste your key into GEMINI_API_KEY
#   …or just:  export GEMINI_API_KEY=...

# 3. Run on the sample inputs
PYTHONPATH=src python3 -m research_agent --urls-file inputs/ai-jobs.txt
```

Outputs land in `outputs/run-<timestamp>/`:

| File | What |
| --- | --- |
| `brief.md` | The analyst-readable brief (open this) |
| `brief.json` | Full structured result — sources, claims, themes, coverage |
| `claims.json` | Intermediate per-source extracted claims (for inspection) |

Pre-generated examples are committed at [`outputs/sample_brief.md`](outputs/sample_brief.md)
and [`outputs/sample_brief.json`](outputs/sample_brief.json) (generated with
`gemini-3-flash-preview` over the 5 sample URLs).

> **If you hit `503 UNAVAILABLE` or `429`:** free-tier model availability fluctuates.
> The agent already retries transient errors with backoff; if a model stays
> unavailable, pick another with `LLM_MODEL`, e.g.
> `LLM_MODEL=gemini-3-flash-preview` or `LLM_MODEL=gemini-flash-lite-latest`.

### Common flags

```bash
--urls-file FILE      # one URL per line (# comments allowed)
--url URL             # a single URL (repeatable); combinable with --urls-file
--topic "..."         # set the topic explicitly; omit to have it inferred
--yes                 # non-interactive: never prompt to retry failed sources
--no-cache            # bypass the disk cache for fetches AND LLM calls
--outdir DIR          # choose the output directory
```

---

## Swapping the model (it's pluggable)

The LLM layer is one thin interface over several providers' REST APIs — no SDKs.
Switch with two env vars; **no code change**:

| Provider | `LLM_PROVIDER` | Key env | Free? |
| --- | --- | --- | --- |
| Google Gemini *(default)* | `gemini` | `GEMINI_API_KEY` | ✅ free tier, no card |
| Groq | `groq` | `GROQ_API_KEY` | ✅ free tier, no card |
| Anthropic | `anthropic` | `ANTHROPIC_API_KEY` | paid |
| OpenAI | `openai` | `OPENAI_API_KEY` | paid |
| Ollama (local) | `ollama` | — | ✅ fully local |

```bash
export LLM_PROVIDER=groq   LLM_MODEL=llama-3.3-70b-versatile  GROQ_API_KEY=...
export LLM_PROVIDER=ollama LLM_MODEL=llama3.1                 # no key, runs locally
```

---

## How it works — a 6-stage pipeline

The agent deliberately **decomposes** the problem rather than one-shotting a giant
prompt. Each stage persists its output to the run directory so you can inspect the
reasoning.

```
URLs ─► [0] Fetch & parse ──► [retry gate] ──► [1] Resolve topic
                                                     │
        [2] Per-source claim extraction  ◄───────────┘
                 │ (atomic claims + verbatim quotes, verified vs source text)
        [3] Generate coverage sub-questions   (defines "good coverage")
                 │
        [4] Cross-source alignment            ◄── the core reasoning step
                 │ consensus (≥2 sources) · contradictions · outliers · gaps
        [5] Synthesis ──► [6] Render brief.md + brief.json
```

- **Stage 0 — Fetch & parse.** httpx with browser-like headers, redirects,
  timeout + retry; `trafilatura` extracts main content + metadata. Each source is
  classified `ok / paywall / empty / js_required / timeout / http_error`. Failures
  are **data, not crashes**.
- **Retry gate.** If any source fails, the agent prints a status table and asks
  whether to retry the failures with a tougher strategy (Googlebot UA + longer
  timeout). `--yes` skips the prompt.
- **Stage 1 — Topic.** Uses `--topic` if given, else infers it from the sources.
- **Stage 2 — Claim extraction.** Per source *independently*: first a **relevance
  check** (a 200 can still be the wrong page — a redirect/locale can serve unrelated
  content; off-topic sources are excluded and flagged), then atomic assertions, each
  with a verbatim `supporting_quote` that is **verified by substring match** against
  the fetched text (hallucinated quotes get flagged).
- **Stage 3 — Coverage sub-questions.** The model lists what a rigorous brief
  *should* answer — this is the reference frame for detecting true gaps.
- **Stage 4 — Cross-source alignment.** All claims + sub-questions go in; the model
  clusters claims into themes and, **referencing only the given claim_ids**,
  classifies consensus / contradiction / outlier and maps which sources cover each
  sub-question (empty = gap).
- **Stage 5 — Synthesis.** Prose sections with inline `[S#]` citations, grounded
  strictly in the structured analysis.
- **Stage 6 — Render.** `brief.md` (sources table, exec summary, consensus,
  contradictions, outliers, gaps, coverage map, claim appendix with quotes) +
  `brief.json`.

---

## Reproducibility / cost

Fetched HTML **and** LLM responses are cached on disk (`.cache/`). Re-running the
same inputs makes **zero** network/API calls and is deterministic — so reviewers
can re-run for free. Use `--no-cache` to force fresh calls.

## Project layout

```
src/research_agent/
  cli.py        # entry point + interactive retry gate
  config.py     # env loading, provider defaults
  fetch.py      # stage 0: fetch + parse + classify (graceful failure)
  llm.py        # pluggable multi-provider client (httpx REST) + caching
  prompts.py    # the prompt templates (one job each)
  stages.py     # stages 1-5 (the LLM steps)
  pipeline.py   # orchestration + artifact persistence
  render.py     # stage 6: markdown + json
  schema.py     # dataclasses (the inter-stage contract)
```

## Tests

Fast, no-network unit tests cover the robustness-critical pure logic (JSON
extraction from messy model output, fetch-status classification, quote
verification):

```bash
python3 -m pip install pytest
PYTHONPATH=src python3 -m pytest -q
```

See [`NOTES.md`](NOTES.md) for approach, assumptions, and tradeoffs.
