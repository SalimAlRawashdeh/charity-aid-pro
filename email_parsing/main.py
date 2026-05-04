"""
Local FastAPI shim for testing without the Azure Functions runtime.

Run with:
    uvicorn main:app --reload --port 7071

Mirrors the HTTP routes in function_app.py. The timer trigger (poll_emails)
is exposed as POST /api/poll for manual invocation.
"""

import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from function_app import process_emails, _IMPORTS_OK, _SCORING_OK
from core import email_client, llm_parser, storage

try:
    from scoring.models import OpportunityInput
    from scoring.pipeline import run_scoring_pipeline
except Exception:
    OpportunityInput = None
    run_scoring_pipeline = None

logger = logging.getLogger(__name__)
app = FastAPI(title="Email Parsing (local)")


@app.post("/api/poll")
def poll_emails():
    return process_emails()


@app.post("/api/scan")
def scan_now():
    if not _IMPORTS_OK:
        raise HTTPException(503, "Pipeline not initialised")
    try:
        return process_emails()
    except Exception as exc:
        logger.error("scan_now failed: %s", exc, exc_info=True)
        raise HTTPException(500, str(exc))


@app.post("/api/scan-dry")
def scan_dry():
    """
    Parse + score unread emails and return the results directly without
    touching storage. Useful for local testing before Cosmos is set up.
    The response is the JSON payload that would have been written to the DB.
    """
    if not _IMPORTS_OK:
        raise HTTPException(503, "Pipeline not initialised")

    results = []
    failures = []
    emails = email_client.fetch_unread_emails()

    for email_data in emails:
        email_id = email_data.get("id") or email_data.get("emailId", "")
        try:
            parsed = llm_parser.parse_email(email_data)
            payload = (
                parsed.model_dump(mode="json")
                if hasattr(parsed, "model_dump")
                else parsed
            )
            results.append({"emailId": email_id, "parsed": payload})
        except Exception as exc:
            logger.error("scan_dry: failed on %s: %s", email_id, exc, exc_info=True)
            failures.append({"emailId": email_id, "error": str(exc)})

    return {
        "count": len(results),
        "failures": failures,
        "results": results,
    }


@app.get("/api/opportunities")
def get_opportunities(
    type: str | None = None,
    status: str | None = None,
    funderName: str | None = None,
):
    if not _IMPORTS_OK:
        raise HTTPException(503, "Pipeline not initialised")

    filters: dict[str, Any] = {}
    if type:
        filters["type"] = type
    if status:
        filters["status"] = status
    if funderName:
        filters["funderName"] = funderName

    results = storage.get_opportunities(filters)
    serialisable = []
    for item in results:
        if hasattr(item, "model_dump"):
            serialisable.append(item.model_dump(mode="json"))
        elif hasattr(item, "dict"):
            serialisable.append(item.dict())
        else:
            serialisable.append(item)
    return JSONResponse(serialisable)


@app.post("/api/score")
async def score_opportunities(request: Request):
    if not _SCORING_OK:
        raise HTTPException(503, "Scoring module unavailable")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON body")

    if not isinstance(body, list):
        body = [body]

    try:
        inputs = [OpportunityInput(**item) for item in body]
    except Exception as exc:
        raise HTTPException(422, f"Invalid opportunity data: {exc}")

    scored = run_scoring_pipeline(inputs)
    return [s.model_dump() for s in scored]
