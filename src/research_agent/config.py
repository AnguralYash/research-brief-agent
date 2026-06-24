"""Configuration: env loading, provider defaults, and run paths.

Kept dependency-free (no python-dotenv) — we parse a `.env` file by hand so the
only third-party deps stay httpx + trafilatura.
"""
from __future__ import annotations

import os
from pathlib import Path

# Project root = three levels up from this file (src/research_agent/config.py).
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = PROJECT_ROOT / ".cache"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"

# Per-provider default models. Chosen to be cheap/fast/free-tier-friendly; the
# whole point of the abstraction is that any of these is a one-line swap.
PROVIDER_DEFAULT_MODEL = {
    "gemini": "gemini-2.5-flash",
    "anthropic": "claude-haiku-4-5",
    "openai": "gpt-4o-mini",
    "groq": "llama-3.3-70b-versatile",
    "ollama": "llama3.1",
}

PROVIDER_API_KEY_ENV = {
    "gemini": "GEMINI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "groq": "GROQ_API_KEY",
    "ollama": None,  # local; no key required
}


def load_dotenv(path: Path | None = None) -> None:
    """Minimal .env loader: KEY=VALUE lines, '#' comments, optional quotes.

    Does not overwrite variables already present in the real environment.
    """
    path = path or (PROJECT_ROOT / ".env")
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


class Config:
    """Resolved runtime configuration for a single agent run."""

    def __init__(self) -> None:
        load_dotenv()
        self.provider = os.environ.get("LLM_PROVIDER", "gemini").strip().lower()
        if self.provider not in PROVIDER_DEFAULT_MODEL:
            raise ValueError(
                f"Unknown LLM_PROVIDER={self.provider!r}. "
                f"Choose one of: {', '.join(PROVIDER_DEFAULT_MODEL)}"
            )
        self.model = (
            os.environ.get("LLM_MODEL", "").strip()
            or PROVIDER_DEFAULT_MODEL[self.provider]
        )
        key_env = PROVIDER_API_KEY_ENV[self.provider]
        self.api_key = os.environ.get(key_env, "").strip() if key_env else ""
        self.ollama_host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

    def require_key(self) -> None:
        """Raise a friendly error if a key is needed but missing."""
        key_env = PROVIDER_API_KEY_ENV[self.provider]
        if key_env and not self.api_key:
            raise SystemExit(
                f"\nMissing API key: set {key_env} (provider={self.provider}).\n"
                f"For a free Gemini key (no credit card): https://aistudio.google.com/apikey\n"
                f"Then add it to a .env file or export it, e.g.  export {key_env}=...\n"
            )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"Config(provider={self.provider!r}, model={self.model!r})"
