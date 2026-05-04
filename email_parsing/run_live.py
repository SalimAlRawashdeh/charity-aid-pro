"""
End-to-end runner: fetch live emails from a personal Outlook account using the
cached MSAL token, parse + score them with the LLM pipeline, and store the
results in Supabase.

Run with the project venv:
    /Users/yasha/git/charity-aid-pro/.venv/bin/python email_parsing/run_live.py

Optional flags:
    --count N        How many recent emails to fetch (default 10)
    --mark-read      Mark each successfully processed email as read in Outlook

Reads AZURE_OPENAI_* and SUPABASE_* from email_parsing/local.settings.json.
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import sys
from pathlib import Path

# ── Load local.settings.json into env BEFORE any pipeline imports ────────────

_HERE = Path(__file__).resolve().parent
_settings = _HERE / "local.settings.json"
if not _settings.exists():
    sys.exit("local.settings.json not found — populate it and try again.")

for k, v in json.loads(_settings.read_text()).get("Values", {}).items():
    os.environ.setdefault(k, v)

# Clear any stray Google credentials that conflict with the Bearer-token shim
for _v in ("GOOGLE_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_PROJECT"):
    os.environ.pop(_v, None)

# ── Patch openai.AzureOpenAI to call Gemini natively ─────────────────────────
# Mirrors the shim in test_local.py — the AQ. key format only works with
# Gemini's native endpoint via x-goog-api-key. Must run before llm_parser import.

import time  # noqa: E402
import httpx  # noqa: E402
import openai  # noqa: E402

_GEMINI_NATIVE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class _Choice:
    class _Message:
        def __init__(self, content): self.content = content
    def __init__(self, content): self.message = self._Message(content)


class _Response:
    def __init__(self, content): self.choices = [_Choice(content)]


class _Completions:
    def __init__(self, api_key): self._key = api_key

    def create(self, *, model, messages, temperature=0, timeout=45, **_):
        contents, system = [], None
        for m in messages:
            role = m["role"]
            if role == "system":
                system = {"parts": [{"text": m["content"]}]}
            elif role == "user":
                contents.append({"role": "user", "parts": [{"text": m["content"]}]})
            elif role == "assistant":
                contents.append({"role": "model", "parts": [{"text": m["content"]}]})

        payload = {"contents": contents, "generationConfig": {"temperature": temperature}}
        if system:
            payload["system_instruction"] = system

        url = _GEMINI_NATIVE.format(model=model)
        with httpx.Client(timeout=timeout) as c:
            resp = c.post(url, json=payload, headers={"x-goog-api-key": self._key})
        resp.raise_for_status()
        text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        return _Response(text)


class _GeminiAsAzure:
    def __init__(self, *, azure_endpoint=None, api_key, api_version=None, **_):
        self.chat = type("_Chat", (), {"completions": _Completions(api_key)})()


openai.AzureOpenAI = _GeminiAsAzure  # must precede llm_parser import

# ── Pipeline imports (after env + shim are ready) ────────────────────────────

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("run_live")

# scoring/ lives at the repo root, not inside email_parsing
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE))

from core import llm_parser, storage  # noqa: E402
from core.email_client import _html_to_markdown  # noqa: E402

# ── Personal Outlook account fetcher (uses cached MSAL token) ────────────────

import msal  # noqa: E402
import requests  # noqa: E402

_CLIENT_ID = "9242462a-6cbe-43fe-9baf-54967057b1f1"
_AUTHORITY = "https://login.microsoftonline.com/consumers"
_SCOPES = ["Mail.Read"]
_CACHE_FILE = _HERE / "core" / "token_cache.bin"


def _get_outlook_token() -> str:
    cache = msal.SerializableTokenCache()
    if _CACHE_FILE.exists():
        cache.deserialize(_CACHE_FILE.read_text())
    app = msal.PublicClientApplication(_CLIENT_ID, authority=_AUTHORITY, token_cache=cache)
    accounts = app.get_accounts()
    if not accounts:
        sys.exit(
            f"No cached MSAL account at {_CACHE_FILE}. "
            "Run email_parsing/core/read_emails.py once interactively to authenticate."
        )
    result = app.acquire_token_silent(_SCOPES, account=accounts[0])
    if not result or "access_token" not in result:
        sys.exit("Cached MSAL token has expired — re-run read_emails.py to refresh.")
    if cache.has_state_changed:
        _CACHE_FILE.write_text(cache.serialize())
    return result["access_token"]


def fetch_recent_emails(count: int) -> list[dict]:
    token = _get_outlook_token()
    url = "https://graph.microsoft.com/v1.0/me/mailFolders/Inbox/messages"
    params = {
        "$top": count,
        "$orderby": "receivedDateTime desc",
        "$select": "id,subject,from,receivedDateTime,body",
    }
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, params=params, timeout=30)
    r.raise_for_status()

    out: list[dict] = []
    for m in r.json().get("value", []):
        body = m.get("body", {}) or {}
        content = body.get("content", "") or ""
        ctype = (body.get("contentType") or "text").lower()
        body_text = _html_to_markdown(content) if ctype == "html" else content.strip()

        out.append({
            "id": m["id"],
            "subject": m.get("subject", "(no subject)"),
            "from": (m.get("from") or {}).get("emailAddress", {}).get("address", ""),
            "receivedDateTime": m.get("receivedDateTime", ""),
            "body": body_text,
        })
    return out


def _mark_read(token: str, message_id: str) -> None:
    requests.patch(
        f"https://graph.microsoft.com/v1.0/me/messages/{message_id}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"isRead": True},
        timeout=30,
    ).raise_for_status()


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=10)
    ap.add_argument("--mark-read", action="store_true")
    args = ap.parse_args()

    # Sanity-check critical env
    for v in ("AZURE_OPENAI_KEY", "SUPABASE_URL", "SUPABASE_KEY"):
        if not os.environ.get(v) or os.environ[v].startswith("<paste"):
            sys.exit(f"{v} is not set in local.settings.json")

    log.info("Fetching %d recent email(s) from Outlook...", args.count)
    emails = fetch_recent_emails(args.count)
    log.info("Got %d email(s). Beginning pipeline.", len(emails))

    processed = failed = total_opps = 0
    token_for_read = _get_outlook_token() if args.mark_read else None

    for email in emails:
        eid = email["id"]
        subject = email["subject"]
        log.info("---- %s | %s", email["receivedDateTime"][:10], subject[:80])

        try:
            result = llm_parser.parse_email(email)
            storage.store_parsed_email(result)

            n = len(result.opportunities)
            total_opps += n
            processed += 1
            log.info("  classification=%s, opportunities=%d", result.classification, n)
            for i, opp in enumerate(result.opportunities, 1):
                fs = f"{opp.final_score:.1f}" if opp.final_score is not None else "n/a"
                log.info("    [%d] %s — %s (score %s)", i, opp.funderName, opp.programName, fs)

            if args.mark_read and token_for_read:
                _mark_read(token_for_read, eid)

        except Exception as exc:  # noqa: BLE001
            failed += 1
            log.error("  FAILED: %s", exc, exc_info=True)

    print()
    print(f"Done. processed={processed}  failed={failed}  opportunities={total_opps}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
