"""
End-to-end pipeline test using real Outlook emails.

Fetches recent emails from your personal Outlook account (reusing the token
cached by core/read_emails.py) and sends each one through the local FastAPI
server for full LLM extraction + scoring.

USAGE:
  1. Start the FastAPI server in another terminal:
       cd email_parsing && uvicorn local_server:app --reload

  2. Run this script:
       cd email_parsing && python3 pipeline_test.py

  Optional flags:
       python3 pipeline_test.py --count 10     # how many emails to fetch (default 5)
       python3 pipeline_test.py --server http://localhost:8000  # custom server URL
"""

import argparse
import json
import os
import sys
import re

import msal
import requests

# ── Config ────────────────────────────────────────────────────────────────────

CLIENT_ID  = "9242462a-6cbe-43fe-9baf-54967057b1f1"   # same app as read_emails.py
AUTHORITY  = "https://login.microsoftonline.com/consumers"
SCOPES     = ["Mail.Read"]
CACHE_FILE = "token_cache.bin"
GRAPH_URL  = "https://graph.microsoft.com/v1.0/me/messages"


# ── Auth (reuses token_cache.bin from read_emails.py) ─────────────────────────

def get_token() -> str:
    cache = msal.SerializableTokenCache()
    if os.path.exists(CACHE_FILE):
        cache.deserialize(open(CACHE_FILE).read())

    app = msal.PublicClientApplication(CLIENT_ID, authority=AUTHORITY, token_cache=cache)

    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(SCOPES, account=accounts[0])
        if result and "access_token" in result:
            if cache.has_state_changed:
                open(CACHE_FILE, "w").write(cache.serialize())
            return result["access_token"]

    # Token expired or missing — prompt re-auth
    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        sys.exit(f"Device flow failed: {flow}")
    print(flow["message"])
    sys.stdout.flush()
    result = app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        sys.exit(f"Auth failed: {result.get('error_description')}")
    if cache.has_state_changed:
        open(CACHE_FILE, "w").write(cache.serialize())
    return result["access_token"]


# ── Fetch emails with full body ───────────────────────────────────────────────

def _html_to_text(html: str) -> str:
    """Minimal HTML → plain text for emails the LLM can read."""
    text = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<p[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def fetch_emails(token: str, count: int) -> list[dict]:
    headers = {"Authorization": f"Bearer {token}"}
    params = {
        "$top": count,
        "$orderby": "receivedDateTime desc",
        "$select": "id,subject,from,receivedDateTime,body",
    }
    r = requests.get(GRAPH_URL, headers=headers, params=params)
    r.raise_for_status()

    results = []
    for msg in r.json().get("value", []):
        body_obj  = msg.get("body", {})
        content   = body_obj.get("content", "")
        ctype     = body_obj.get("contentType", "text").lower()
        body_text = _html_to_text(content) if ctype == "html" else content.strip()

        results.append({
            "id":               msg["id"],
            "subject":          msg.get("subject", "(no subject)"),
            "from":             msg.get("from", {}).get("emailAddress", {}).get("address", ""),
            "receivedDateTime": msg.get("receivedDateTime", ""),
            "body":             body_text,
        })
    return results


# ── Send to FastAPI server ────────────────────────────────────────────────────

def parse_email(server: str, email: dict) -> dict:
    r = requests.post(f"{server}/parse", json=email, timeout=120)
    r.raise_for_status()
    return r.json()


# ── Output ────────────────────────────────────────────────────────────────────

def print_result(email: dict, result: dict):
    opps = result.get("opportunities", [])
    classification = result.get("classification", "?")
    confidence = result.get("classificationConfidence", 0)

    print(f"\n{'='*60}")
    print(f"Subject : {email['subject']}")
    print(f"From    : {email['from']}")
    print(f"Class   : {classification} ({confidence:.0%})")
    print(f"Opps    : {len(opps)} found")

    for i, opp in enumerate(opps, 1):
        score   = opp.get("final_score")
        gating  = opp.get("gating") or {}
        geo     = gating.get("geography") or {}
        elig    = gating.get("eligibility") or {}

        print(f"\n  [{i}] {opp.get('funderName','?')} — {opp.get('programName','?')}")
        print(f"       Amount  : £{opp.get('amount', 0):,.0f}" +
              (f" – £{opp['amountMax']:,.0f}" if opp.get("amountMax") else ""))
        print(f"       Location: {opp.get('location','?')}")
        print(f"       Deadline: {opp.get('deadline','?')}")
        print(f"       Score   : {f'{score:.1f}' if score is not None else 'n/a'} / 100")

        if gating:
            status = gating.get("status", "?")
            geo_pass = "pass" if geo.get("pass") else "FAIL"
            eli_pass = "pass" if elig.get("pass") else "FAIL"
            print(f"       Gating  : {status}")
            print(f"         geography  : {geo_pass} — {geo.get('specificity','?')} ({geo.get('location','?')})")
            print(f"         eligibility: {eli_pass} — {elig.get('reasoning','')}")

        tags = opp.get("suggested_tags") or []
        if tags:
            print(f"       Tags    : {', '.join(tags)}")

    if not opps and classification not in ("IRRELEVANT",):
        print("  (no opportunities extracted)")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--count",  type=int, default=5,                       help="Number of emails to fetch")
    parser.add_argument("--server", type=str, default="http://localhost:8000",  help="FastAPI server URL")
    parser.add_argument("--full-json", action="store_true",                    help="Also print full JSON output")
    args = parser.parse_args()

    # Check server is up
    try:
        health = requests.get(f"{args.server}/health", timeout=5).json()
        print(f"Server  : {args.server}  (model: {health.get('model','?')})")
    except Exception:
        sys.exit(f"FastAPI server not reachable at {args.server} — run: uvicorn local_server:app --reload")

    print(f"Fetching: {args.count} emails from Outlook...\n")
    token  = get_token()
    emails = fetch_emails(token, args.count)
    print(f"Fetched {len(emails)} email(s). Sending through pipeline...\n")

    for email in emails:
        try:
            result = parse_email(args.server, email)
            print_result(email, result)
            if args.full_json:
                print("\n--- Full JSON ---")
                print(json.dumps(result, indent=2, default=str))
        except requests.HTTPError as e:
            print(f"\n[ERROR] {email['subject']}: HTTP {e.response.status_code} — {e.response.text[:200]}")
        except Exception as e:
            print(f"\n[ERROR] {email['subject']}: {e}")

    print(f"\n{'='*60}")
    print("Done.")


if __name__ == "__main__":
    main()
