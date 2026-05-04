"""Environment-driven configuration."""

from __future__ import annotations

import os


# ── LLM (OpenAI-compatible endpoint) ─────────────────────────────────────────
# Defaults target Google Gemini's OpenAI-compatible endpoint, but any
# OpenAI-compatible URL works (Azure, OpenRouter, etc.).
LLM_BASE_URL: str = os.environ.get(
    "LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/"
)
LLM_API_KEY: str = os.environ.get("LLM_API_KEY", "")
LLM_MODEL: str = os.environ.get("LLM_MODEL", "gemini-2.5-flash")
LLM_TIMEOUT_SECONDS: float = float(os.environ.get("LLM_TIMEOUT_SECONDS", "60"))

# ── Microsoft Graph (personal Outlook via MSAL device-code) ──────────────────
# Application (client) ID of the registered Microsoft Entra app.
MSAL_CLIENT_ID: str = os.environ.get(
    "MSAL_CLIENT_ID", "9242462a-6cbe-43fe-9baf-54967057b1f1"
)
# Path to MSAL token cache file. CI can instead provide MSAL_TOKEN_CACHE_B64.
MSAL_CACHE_FILE: str = os.environ.get(
    "MSAL_CACHE_FILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "token_cache.bin"),
)
MSAL_TOKEN_CACHE_B64: str = os.environ.get("MSAL_TOKEN_CACHE_B64", "")

# ── Supabase ─────────────────────────────────────────────────────────────────
SUPABASE_URL: str = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY: str = os.environ.get("SUPABASE_KEY", "")


def missing_required(*names: str) -> list[str]:
    """Return the names from *names* that are unset / blank in the current config."""
    here = globals()
    return [n for n in names if not here.get(n)]
