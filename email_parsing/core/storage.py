"""
Supabase (Postgres) storage layer.

Single table: opportunities. See supabase/migrations/.
Re-runs of the pipeline are safe: opportunities are upserted on `id`.
"""

from __future__ import annotations

import logging
from typing import Any

from supabase import Client, create_client

from . import config
from .schema import FundingOpportunity, ParsedEmail

logger = logging.getLogger(__name__)


# ── Client ───────────────────────────────────────────────────────────────────

_client: Client | None = None


def _get_client() -> Client:
    global _client
    if _client is None:
        if not config.SUPABASE_URL or not config.SUPABASE_KEY:
            raise RuntimeError(
                "SUPABASE_URL / SUPABASE_KEY are not set. "
                "Populate them in local.settings.json or the function app config."
            )
        _client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
        logger.debug("Supabase client initialised (url=%s)", config.SUPABASE_URL)
    return _client


# ── Field mapping (camelCase ↔ snake_case) ───────────────────────────────────

_OPP_FIELD_MAP: dict[str, str] = {
    "id": "id",
    "funderName": "funder_name",
    "programName": "program_name",
    "amount": "amount",
    "amountMax": "amount_max",
    "type": "type",
    "deadline": "deadline",
    "location": "location",
    "duration": "duration",
    "durationMonths": "duration_months",
    "status": "status",
    "score": "score",
    "tags": "tags",
    "description": "description",
    "eligibility": "eligibility",
    "notes": "notes",
    "website": "website",
    "contactName": "contact_name",
    "contactEmail": "contact_email",
    "source": "source",
    "extractionConfidence": "extraction_confidence",
    "gating": "gating",
    "scores": "scores",
    "timing": "timing",
    "final_score": "final_score",
    "suggested_tags": "suggested_tags",
    "scored_at": "scored_at",
}

_OPP_FIELD_MAP_REVERSE = {v: k for k, v in _OPP_FIELD_MAP.items()}


def _opp_to_row(opp: FundingOpportunity) -> dict[str, Any]:
    src = opp.model_dump(mode="json")
    return {db_col: src[py_field] for py_field, db_col in _OPP_FIELD_MAP.items() if py_field in src}


def _row_to_opp(row: dict[str, Any]) -> dict[str, Any]:
    return {_OPP_FIELD_MAP_REVERSE.get(k, k): v for k, v in row.items()}


# ── Public API ────────────────────────────────────────────────────────────────


def store_parsed_email(parsed_email: ParsedEmail) -> None:
    """
    Upsert all opportunities from this email.

    The LLM doesn't guarantee unique opportunity IDs (it sometimes returns
    the same string twice within one email, or reuses an ID across emails),
    so we override `id` with a deterministic `{emailId}#{index}`. This keeps
    re-runs idempotent — same email + same number of opps → same IDs.
    """
    rows: list[dict[str, Any]] = []
    for idx, opp in enumerate(parsed_email.opportunities):
        row = _opp_to_row(opp)
        row["id"] = f"{parsed_email.emailId}#{idx}"
        rows.append(row)

    if not rows:
        logger.info("No opportunities in email '%s' — nothing to store.", parsed_email.emailId)
        return

    _get_client().table("opportunities").upsert(rows, on_conflict="id").execute()
    logger.info(
        "Stored email '%s' — %d opportunity/opportunities upserted.",
        parsed_email.emailId,
        len(rows),
    )


def get_opportunities(filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """
    Return opportunity dicts (camelCase keys) with optional filters.

    Supported filter keys:
        type        — exact match
        status      — exact match
        funderName  — case-insensitive substring
    """
    filters = filters or {}
    q = _get_client().table("opportunities").select("*")

    if "type" in filters:
        q = q.eq("type", filters["type"])
    if "status" in filters:
        q = q.eq("status", filters["status"])
    if "funderName" in filters:
        q = q.ilike("funder_name", f"%{filters['funderName']}%")

    rows = (q.execute().data or [])
    out = [_row_to_opp(r) for r in rows]
    logger.info("get_opportunities returned %d row(s) (filters=%s)", len(out), filters)
    return out
