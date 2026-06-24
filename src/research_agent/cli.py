"""Command-line entry point + the interactive fetch-retry gate.

Usage:
  python -m research_agent --urls-file inputs/ai-jobs.txt
  python -m research_agent --url https://a.com --url https://b.com --topic "..."
  python -m research_agent --urls-file inputs/ai-jobs.txt --yes      # non-interactive
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import fetch
from .config import OUTPUTS_DIR, Config
from .llm import LLMClient, LLMError
from .pipeline import run_pipeline
from .render import write_outputs
from .schema import SourceDoc


def _read_urls_file(path: Path) -> list[str]:
    urls = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            urls.append(line)
    return urls


def _print_fetch_table(docs: list[SourceDoc]) -> None:
    print("\nFetch results:")
    for d in docs:
        mark = "ok" if d.status.usable else d.status.value.upper()
        extra = f"  [{d.error}]" if d.error and not d.status.usable else ""
        title = (d.title or d.url)[:70]
        print(f"  {d.id:<3} {mark:<12} {d.word_count:>5}w  {title}{extra}")


def _interactive_retry(docs: list[SourceDoc], *, assume_yes: bool) -> list[SourceDoc]:
    """If any sources failed, report and (optionally) retry with a tougher strategy."""
    failed = [d for d in docs if not d.status.usable]
    if not failed:
        return docs

    _print_fetch_table(docs)
    ids = ", ".join(d.id for d in failed)
    print(f"\n{len(failed)} source(s) could not be read: {ids}")

    if assume_yes:
        print("(--yes) Skipping retry; proceeding with the sources that succeeded.")
        return docs
    if not sys.stdin.isatty():
        print("(non-interactive stdin) Skipping retry; proceeding with what loaded.")
        return docs

    ans = input("Retry the failed sources with a tougher strategy "
                "(Googlebot UA + longer timeout)? [y/N] ").strip().lower()
    if ans not in ("y", "yes"):
        print("Proceeding without retry.")
        return docs

    print("Retrying failed sources…")
    docs = fetch.retry_failed(docs, use_cache=False)
    still = [d for d in docs if not d.status.usable]
    if still:
        print(f"Still unreadable after retry: {', '.join(d.id for d in still)}. "
              "Proceeding with the rest.")
    else:
        print("All sources readable after retry.")
    return docs


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="research_agent",
        description="Research a topic across 3-5 URLs and produce a cited analyst brief.",
    )
    src = p.add_argument_group("input")
    src.add_argument("--urls-file", type=Path, help="file with one URL per line (# comments ok)")
    src.add_argument("--url", action="append", default=[], help="a source URL (repeatable)")
    src.add_argument("--topic", default=None, help="topic; if omitted, it is inferred")
    out = p.add_argument_group("run")
    out.add_argument("--outdir", type=Path, default=None,
                     help="output dir (default: outputs/run-<timestamp>)")
    out.add_argument("--yes", action="store_true", help="non-interactive; never prompt to retry")
    out.add_argument("--no-cache", action="store_true",
                     help="bypass disk cache for fetches AND llm calls")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    urls: list[str] = list(args.url)
    if args.urls_file:
        urls = _read_urls_file(args.urls_file) + urls
    if not urls:
        print("error: provide --urls-file and/or --url", file=sys.stderr)
        return 2
    if len(urls) > 8:
        print(f"note: {len(urls)} URLs provided; the exercise targets 3-5.", file=sys.stderr)

    config = Config()
    config.require_key()
    print(f"Provider: {config.provider} · model: {config.model}")

    # Stage 0 — fetch
    print(f"\nFetching {len(urls)} source(s)…")
    docs = fetch.fetch_all(urls, use_cache=not args.no_cache)
    docs = _interactive_retry(docs, assume_yes=args.yes)

    usable = [d for d in docs if d.status.usable]
    if not usable:
        _print_fetch_table(docs)
        print("\nNo readable sources — cannot build a brief.", file=sys.stderr)
        return 1

    # Run dir
    run_dir = args.outdir or (
        OUTPUTS_DIR / f"run-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    )

    client = LLMClient(config, use_cache=not args.no_cache)
    print(f"\nRunning reasoning pipeline ({len(usable)} usable source(s))…")
    try:
        brief = run_pipeline(
            docs, topic=args.topic, config=config, client=client, run_dir=run_dir
        )
    except LLMError as exc:
        print(f"\nLLM call failed after retries: {exc}", file=sys.stderr)
        print("Tip: the model may be overloaded/rate-limited — retry, or pick another "
              "with LLM_MODEL (e.g. gemini-flash-lite-latest).", file=sys.stderr)
        return 1

    md_path, json_path = write_outputs(brief, run_dir)
    print(f"\n✓ Brief written:\n  {md_path}\n  {json_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
