"""Stage 0: fetch + parse sources, with graceful failure classification.

Strategy:
  * httpx GET with browser-like headers, redirects, sane timeout + one retry.
  * trafilatura extracts the main article text + metadata from messy HTML.
  * Raw HTML is cached to .cache/html/ keyed by URL hash so reruns are offline/free.
  * Every source is classified (OK / PAYWALL / EMPTY / JS_REQUIRED / TIMEOUT /
    HTTP_ERROR / FETCH_ERROR) and we NEVER raise out — a failed source is data,
    not a crash. The caller decides whether to retry.
"""
from __future__ import annotations

import hashlib
import re

import httpx
import trafilatura

from .config import CACHE_DIR
from .schema import FetchStatus, SourceDoc

# A realistic desktop-Chrome UA gets us past the laziest bot filters.
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Googlebot UA is a pragmatic second attempt — many soft paywalls whitelist it.
RETRY_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Heuristic signals that we hit a paywall / consent wall rather than content.
PAYWALL_MARKERS = (
    "subscribe to continue",
    "subscribe to read",
    "create a free account",
    "this content is for subscribers",
    "already a subscriber",
    "to continue reading",
    "sign in to read",
    "metered",
    "you have reached your",
)

MIN_WORDS_OK = 120  # below this we treat extraction as failed/partial


def url_hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def _html_cache_path(url: str):
    CACHE_DIR.joinpath("html").mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / "html" / f"{url_hash(url)}.html"


def _classify(text: str, raw_html: str) -> FetchStatus:
    """Decide a status from extracted text + raw HTML signals."""
    words = len(text.split())
    lowered = (text[:4000] + " " + raw_html[:4000]).lower()
    has_paywall = any(m in lowered for m in PAYWALL_MARKERS)

    if words >= MIN_WORDS_OK:
        # Got real content. Still flag a paywall if markers dominate a short body.
        return FetchStatus.OK
    if has_paywall:
        return FetchStatus.PAYWALL
    if words == 0 and raw_html:
        # HTML arrived but yielded no article text. If it's script-heavy, it's
        # almost certainly a client-rendered (JS) page we can't parse statically.
        script_ratio = raw_html.lower().count("<script")
        if script_ratio >= 5 or "__next_data__" in raw_html.lower() or "window.__" in raw_html.lower():
            return FetchStatus.JS_REQUIRED
        return FetchStatus.EMPTY
    # Some text, but too little to be useful.
    return FetchStatus.EMPTY


def _extract(raw_html: str, url: str) -> tuple[str, dict]:
    """Return (clean_text, metadata) from raw HTML via trafilatura."""
    text = trafilatura.extract(
        raw_html,
        url=url,
        include_comments=False,
        include_tables=True,
        favor_recall=True,
    ) or ""
    meta = {"title": "", "author": "", "date": ""}
    try:
        md = trafilatura.extract_metadata(raw_html, default_url=url)
        if md:
            meta["title"] = md.title or ""
            meta["author"] = md.author or ""
            meta["date"] = md.date or ""
    except Exception:
        pass
    return text.strip(), meta


def _normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def fetch_one(
    source_id: str,
    url: str,
    *,
    timeout: float = 20.0,
    headers: dict | None = None,
    use_cache: bool = True,
) -> SourceDoc:
    """Fetch and parse a single URL. Never raises; returns a classified SourceDoc."""
    doc = SourceDoc(id=source_id, url=url)
    cache_path = _html_cache_path(url)
    raw_html = ""

    if use_cache and cache_path.exists():
        raw_html = cache_path.read_text(encoding="utf-8", errors="ignore")
    else:
        try:
            with httpx.Client(
                follow_redirects=True, timeout=timeout, headers=headers or DEFAULT_HEADERS
            ) as client:
                resp = client.get(url)
            if resp.status_code >= 400:
                doc.status = FetchStatus.HTTP_ERROR
                doc.error = f"HTTP {resp.status_code}"
                return doc
            raw_html = resp.text
            cache_path.write_text(raw_html, encoding="utf-8")
        except httpx.TimeoutException:
            doc.status = FetchStatus.TIMEOUT
            doc.error = f"timed out after {timeout}s"
            return doc
        except Exception as exc:  # network/DNS/SSL/etc.
            doc.status = FetchStatus.FETCH_ERROR
            doc.error = f"{type(exc).__name__}: {exc}"
            return doc

    text, meta = _extract(raw_html, url)
    doc.text = text
    doc.word_count = len(text.split())
    doc.title = meta["title"]
    doc.author = meta["author"]
    doc.date = meta["date"]
    doc.status = _classify(text, raw_html)
    if not doc.status.usable and not doc.error:
        doc.error = f"only {doc.word_count} words extracted"
    return doc


def fetch_all(urls: list[str], *, use_cache: bool = True) -> list[SourceDoc]:
    """Fetch every URL with the default strategy. Returns docs in input order."""
    docs = []
    for i, url in enumerate(urls, start=1):
        docs.append(fetch_one(f"S{i}", url, use_cache=use_cache))
    return docs


def retry_failed(docs: list[SourceDoc], *, use_cache: bool = False) -> list[SourceDoc]:
    """Re-fetch any non-OK docs with a tougher strategy (Googlebot UA + longer timeout).

    Returns a new list with retried docs replaced. Cache is bypassed on retry so
    we actually hit the network again.
    """
    out = []
    for d in docs:
        if d.status.usable:
            out.append(d)
            continue
        retried = fetch_one(
            d.id, d.url, timeout=40.0, headers=RETRY_HEADERS, use_cache=use_cache
        )
        out.append(retried if retried.status.usable else _merge_attempt(d, retried))
    return out


def _merge_attempt(original: SourceDoc, retried: SourceDoc) -> SourceDoc:
    """Keep whichever attempt extracted more text; preserve the most telling error."""
    best = retried if retried.word_count >= original.word_count else original
    if not best.error:
        best.error = retried.error or original.error
    return best
