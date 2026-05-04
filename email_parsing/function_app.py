"""
Azure Functions entry point — Python v2 programming model.

Functions defined here:
  - poll_emails      : Timer trigger, every 15 minutes
  - scan_now         : HTTP POST /api/scan  (auth: FUNCTION)
  - get_opportunities: HTTP GET  /api/opportunities (auth: ANONYMOUS)
  - score_opportunities: HTTP POST /api/score (auth: FUNCTION)
"""

import logging
import json
from datetime import datetime, timezone

import azure.functions as func

# ---------------------------------------------------------------------------
# Graceful import of pipeline modules so the Function App can still be loaded
# in local dev / CI even when environment variables are absent.
# ---------------------------------------------------------------------------
try:
    from core import email_client, llm_parser, storage
    _IMPORTS_OK = True
except Exception as _import_err:  # pragma: no cover
    logging.warning(
        "Pipeline module import failed — running in degraded mode. "
        "Error: %s",
        _import_err,
    )
    _IMPORTS_OK = False

try:
    from scoring.models import OpportunityInput
    from scoring.pipeline import run_scoring_pipeline
    _SCORING_OK = True
except Exception as _scoring_import_err:  # pragma: no cover
    logging.warning("Scoring module import failed — /api/score will be unavailable. Error: %s", _scoring_import_err)
    _SCORING_OK = False

logger = logging.getLogger(__name__)

app = func.FunctionApp()


# ---------------------------------------------------------------------------
# Shared processing logic
# ---------------------------------------------------------------------------

def process_emails() -> dict:
    """
    Fetch unread emails and run the full parsing pipeline on each one.

    Returns a summary dict:
        {"processed": int, "opportunities": int, "failures": int}
    """
    if not _IMPORTS_OK:
        raise RuntimeError(
            "Pipeline modules failed to import — check environment variables."
        )

    processed = 0
    opportunities = 0
    failures = 0

    emails = email_client.fetch_unread_emails()
    logger.info("Fetched %d unread email(s) to evaluate.", len(emails))

    for email_data in emails:
        email_id: str = email_data.get("id") or email_data.get("emailId", "")

        try:
            result = llm_parser.parse_email(email_data)
            storage.store_parsed_email(result)

            opportunity_count = len(getattr(result, "opportunities", []))
            opportunities += opportunity_count

            email_client.mark_as_read(email_id)

            processed += 1
            logger.debug(
                "Email %s processed — %d opportunity/ies found.",
                email_id,
                opportunity_count,
            )

        except Exception as exc:  # noqa: BLE001
            failures += 1
            logger.error(
                "Failed to process email %s: %s", email_id, exc, exc_info=True,
            )

    summary = {
        "processed": processed,
        "opportunities": opportunities,
        "failures": failures,
    }
    logger.info(
        "Processed %d emails, found %d opportunities, %d failures.",
        processed,
        opportunities,
        failures,
    )
    return summary


# ---------------------------------------------------------------------------
# Function 1: Timer trigger — poll every 15 minutes
# ---------------------------------------------------------------------------

@app.timer_trigger(
    arg_name="timer",
    schedule="0 */15 * * * *",
    run_on_startup=False,
    use_monitor=True,
)
def poll_emails(timer: func.TimerRequest) -> None:
    """Scheduled poll: fetch and parse unread funding emails."""
    utc_now = datetime.now(timezone.utc).isoformat()
    logger.info("poll_emails triggered at %s.", utc_now)

    if timer.past_due:
        logger.warning("Timer trigger is past due — processing now anyway.")

    try:
        summary = process_emails()
        logger.info(
            "poll_emails complete — processed: %d, opportunities: %d, failures: %d.",
            summary["processed"],
            summary["opportunities"],
            summary["failures"],
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("poll_emails raised an unhandled exception: %s", exc, exc_info=True)
        raise


# ---------------------------------------------------------------------------
# Function 2: HTTP POST /api/scan — manual trigger
# ---------------------------------------------------------------------------

@app.route(route="scan", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def scan_now(req: func.HttpRequest) -> func.HttpResponse:
    """Manually trigger the email processing pipeline."""
    logger.info("scan_now: manual trigger received.")

    try:
        summary = process_emails()
        return func.HttpResponse(
            body=json.dumps(summary),
            status_code=200,
            mimetype="application/json",
        )
    except RuntimeError as exc:
        # Degraded mode or missing config
        logger.error("scan_now: configuration error — %s", exc)
        return func.HttpResponse(
            body=json.dumps({"error": str(exc)}),
            status_code=503,
            mimetype="application/json",
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("scan_now: unexpected error — %s", exc, exc_info=True)
        return func.HttpResponse(
            body=json.dumps({"error": "Internal server error", "detail": str(exc)}),
            status_code=500,
            mimetype="application/json",
        )


# ---------------------------------------------------------------------------
# Function 3: HTTP GET /api/opportunities — frontend read endpoint
# ---------------------------------------------------------------------------

@app.route(route="opportunities", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_opportunities(req: func.HttpRequest) -> func.HttpResponse:
    """
    Return a JSON array of funding opportunities, with optional filtering.

    Query parameters (all optional):
        type        — FundingType  e.g. "grant"
        status      — OpportunityStatus  e.g. "identified"
        funderName  — partial / exact funder name
    """
    logger.info("get_opportunities: request received.")

    if not _IMPORTS_OK:
        return func.HttpResponse(
            body=json.dumps({"error": "Service unavailable — pipeline not initialised."}),
            status_code=503,
            mimetype="application/json",
        )

    filters: dict = {}
    for param in ("type", "status", "funderName"):
        value = req.params.get(param)
        if value:
            filters[param] = value

    try:
        results = storage.get_opportunities(filters)

        # Serialise: support both Pydantic models and plain dicts
        serialisable = []
        for item in results:
            if hasattr(item, "model_dump"):
                serialisable.append(item.model_dump(mode="json"))
            elif hasattr(item, "dict"):
                serialisable.append(item.dict())
            else:
                serialisable.append(item)

        return func.HttpResponse(
            body=json.dumps(serialisable, default=str),
            status_code=200,
            mimetype="application/json",
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("get_opportunities: error — %s", exc, exc_info=True)
        return func.HttpResponse(
            body=json.dumps({"error": "Failed to retrieve opportunities", "detail": str(exc)}),
            status_code=500,
            mimetype="application/json",
        )


# ---------------------------------------------------------------------------
# Function 4: HTTP POST /api/score — ad-hoc scoring endpoint
# ---------------------------------------------------------------------------

@app.route(route="score", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def score_opportunities(req: func.HttpRequest) -> func.HttpResponse:
    """Score a list of opportunity objects on demand."""
    logger.info("score_opportunities: request received.")

    if not _SCORING_OK:
        return func.HttpResponse(
            body=json.dumps({"error": "Scoring module unavailable."}),
            status_code=503,
            mimetype="application/json",
        )

    try:
        body = req.get_json()
    except Exception:
        return func.HttpResponse(
            body=json.dumps({"error": "Invalid JSON body"}),
            status_code=400,
            mimetype="application/json",
        )

    if not isinstance(body, list):
        body = [body]

    try:
        inputs = [OpportunityInput(**item) for item in body]
    except Exception as exc:
        return func.HttpResponse(
            body=json.dumps({"error": "Invalid opportunity data", "detail": str(exc)}),
            status_code=422,
            mimetype="application/json",
        )

    try:
        scored = run_scoring_pipeline(inputs)
        return func.HttpResponse(
            body=json.dumps([s.model_dump() for s in scored], default=str),
            status_code=200,
            mimetype="application/json",
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("score_opportunities: unexpected error — %s", exc, exc_info=True)
        return func.HttpResponse(
            body=json.dumps({"error": "Scoring failed", "detail": str(exc)}),
            status_code=500,
            mimetype="application/json",
        )
