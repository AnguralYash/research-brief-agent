"""Pluggable LLM client — one interface, many providers, all over httpx REST.

Why no SDKs: every provider here exposes a simple JSON REST endpoint, so a thin
adapter keeps dependencies to just httpx and avoids version-pinning five SDKs.

Public surface:
    client = LLMClient(config)
    text = client.complete(system, prompt)            # raw text
    obj  = client.complete_json(system, prompt)        # parsed dict/list

Responses are cached on disk by hash(provider+model+system+prompt) so that
reruns are free and deterministic — important for reviewers without a key budget.
"""
from __future__ import annotations

import hashlib
import json
import re
import time

import httpx

# Status codes worth retrying: rate-limit + transient server/overload errors.
_RETRYABLE = {429, 500, 502, 503, 504}

from .config import CACHE_DIR, Config


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(self, config: Config, *, use_cache: bool = True, temperature: float = 0.2):
        self.cfg = config
        self.use_cache = use_cache
        self.temperature = temperature
        self._cache_dir = CACHE_DIR / "llm"
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self.calls = 0  # observability: how many real API calls this run made

    # ---- caching ---------------------------------------------------------
    def _cache_key(self, system: str, prompt: str) -> str:
        h = hashlib.sha256()
        h.update(f"{self.cfg.provider}\0{self.cfg.model}\0{system}\0{prompt}".encode())
        return h.hexdigest()[:24]

    def _cache_get(self, key: str) -> str | None:
        p = self._cache_dir / f"{key}.txt"
        return p.read_text(encoding="utf-8") if p.exists() else None

    def _cache_put(self, key: str, value: str) -> None:
        (self._cache_dir / f"{key}.txt").write_text(value, encoding="utf-8")

    # ---- public API ------------------------------------------------------
    def complete(self, system: str, prompt: str) -> str:
        if self.use_cache:
            key = self._cache_key(system, prompt)
            cached = self._cache_get(key)
            if cached is not None:
                return cached
        text = self._dispatch(system, prompt)
        self.calls += 1
        if self.use_cache:
            self._cache_put(self._cache_key(system, prompt), text)
        return text

    def complete_json(self, system: str, prompt: str):
        """Complete and parse JSON, with one repair retry on malformed output."""
        raw = self.complete(system, prompt)
        try:
            return _parse_json(raw)
        except ValueError:
            repair = (
                "Your previous reply was not valid JSON. Return ONLY the corrected "
                "JSON value, no prose, no code fences.\n\nPrevious reply:\n" + raw
            )
            fixed = self._dispatch(system, repair)
            self.calls += 1
            return _parse_json(fixed)

    # ---- provider dispatch ----------------------------------------------
    def _dispatch(self, system: str, prompt: str) -> str:
        p = self.cfg.provider
        if p == "gemini":
            return self._gemini(system, prompt)
        if p == "anthropic":
            return self._anthropic(system, prompt)
        if p in ("openai", "groq"):
            return self._openai_compatible(system, prompt)
        if p == "ollama":
            return self._ollama(system, prompt)
        raise LLMError(f"Unsupported provider {p!r}")

    def _post(self, url: str, *, headers: dict, payload: dict, max_retries: int = 4) -> dict:
        """POST with exponential backoff on rate-limit / transient server errors AND
        on network timeouts/connection errors — a slow or dropped call is transient,
        not fatal."""
        last_err = ""
        for attempt in range(max_retries):
            retryable = False
            try:
                with httpx.Client(timeout=httpx.Timeout(180.0, connect=15.0)) as client:
                    resp = client.post(url, headers=headers, json=payload)
                if resp.status_code < 400:
                    return resp.json()
                last_err = f"{self.cfg.provider} HTTP {resp.status_code}: {resp.text[:300]}"
                reason = f"{resp.status_code} {resp.reason_phrase}"
                retryable = resp.status_code in _RETRYABLE
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_err = f"{self.cfg.provider} network error: {type(exc).__name__}: {exc}"
                reason = type(exc).__name__
                retryable = True
            if retryable and attempt < max_retries - 1:
                wait = 2.0 * (2 ** attempt)  # 2s, 4s, 8s
                print(f"    (retrying in {wait:.0f}s — {reason})", flush=True)
                time.sleep(wait)
                continue
            raise LLMError(last_err)
        raise LLMError(last_err)

    # Gemini (Google AI Studio) ------------------------------------------
    def _gemini(self, system: str, prompt: str) -> str:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.cfg.model}:generateContent?key={self.cfg.api_key}"
        )
        payload = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": self.temperature},
        }
        data = self._post(url, headers={"Content-Type": "application/json"}, payload=payload)
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as exc:
            raise LLMError(f"Unexpected Gemini response: {json.dumps(data)[:500]}") from exc

    # Anthropic ----------------------------------------------------------
    def _anthropic(self, system: str, prompt: str) -> str:
        payload = {
            "model": self.cfg.model,
            "max_tokens": 4096,
            "temperature": self.temperature,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        }
        headers = {
            "x-api-key": self.cfg.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        data = self._post("https://api.anthropic.com/v1/messages", headers=headers, payload=payload)
        try:
            return "".join(b.get("text", "") for b in data["content"])
        except (KeyError, TypeError) as exc:
            raise LLMError(f"Unexpected Anthropic response: {json.dumps(data)[:500]}") from exc

    # OpenAI / Groq (chat-completions compatible) ------------------------
    def _openai_compatible(self, system: str, prompt: str) -> str:
        base = (
            "https://api.openai.com/v1"
            if self.cfg.provider == "openai"
            else "https://api.groq.com/openai/v1"
        )
        payload = {
            "model": self.cfg.model,
            "temperature": self.temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        }
        headers = {
            "Authorization": f"Bearer {self.cfg.api_key}",
            "Content-Type": "application/json",
        }
        data = self._post(f"{base}/chat/completions", headers=headers, payload=payload)
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise LLMError(f"Unexpected response: {json.dumps(data)[:500]}") from exc

    # Ollama (local) ------------------------------------------------------
    def _ollama(self, system: str, prompt: str) -> str:
        payload = {
            "model": self.cfg.model,
            "stream": False,
            "options": {"temperature": self.temperature},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        }
        data = self._post(
            f"{self.cfg.ollama_host}/api/chat",
            headers={"Content-Type": "application/json"},
            payload=payload,
        )
        try:
            return data["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise LLMError(f"Unexpected Ollama response: {json.dumps(data)[:500]}") from exc


# --- JSON parsing helpers -----------------------------------------------
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)


def _parse_json(raw: str):
    """Best-effort parse: strip code fences, then fall back to first {...}/[...] span."""
    s = raw.strip()
    s = _FENCE_RE.sub("", s).strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    # Fall back: grab the outermost JSON object or array.
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = s.find(opener), s.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(s[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError("Could not parse JSON from model output")
