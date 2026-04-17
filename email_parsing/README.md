# Charity Aid Pro: Email Parsing Service

This directory contains a Python 3.11 Azure Functions service that:

1. polls a Microsoft 365 mailbox,
2. classifies each unread email with Azure OpenAI,
3. extracts structured funding opportunities,
4. stores results in Azure Cosmos DB,
5. exposes a read API for the frontend.

## Contents

- [Architecture](#architecture)
- [How The Pipeline Works](#how-the-pipeline-works)
- [Scoring Pipeline](#scoring-pipeline)
- [Directory Structure](#directory-structure)
- [Data Contracts](#data-contracts)
- [Environment Variables](#environment-variables)
- [Local Development](#local-development)
- [Local Testing Without Azure (Gemini)](#local-testing-without-azure-gemini)
- [Running Tests](#running-tests)
- [HTTP Endpoints](#http-endpoints)
- [Error Handling And Reliability](#error-handling-and-reliability)
- [Prompt Engineering Workflow](#prompt-engineering-workflow)
- [Deployment](#deployment)
- [Operations And Troubleshooting](#operations-and-troubleshooting)

## Architecture

Main components:

- `function_app.py`
  - Azure Functions entrypoints (`timer`, `POST /api/scan`, `GET /api/opportunities`, `POST /api/score`)
  - per-email orchestration and dead-letter handling
- `core/email_client.py`
  - Microsoft Graph auth and mailbox operations
  - fetches all recent emails regardless of read status (Cosmos dedup prevents reprocessing)
  - retry/backoff for Graph HTTP requests
- `core/llm_parser.py`
  - classify → extract → score pipeline
  - confidence-based escalation from mini to full model
  - retry/backoff and timeout handling for LLM calls
- `core/storage.py`
  - Cosmos DB persistence
  - dedup checks, upsert, filtered query, dead-letter writes
- `core/schema.py`
  - Pydantic v2 models for classification, opportunities, parsed emails
  - `FundingOpportunity` extended with scoring fields (`gating`, `scores`, `timing`, `final_score`, `suggested_tags`, `scored_at`)
- `scoring/`
  - in-process scoring pipeline (no HTTP hop)
  - `models.py` — `OpportunityInput`, `ScoredOpportunity`, gating/score sub-schemas
  - `gating.py` — extraction confidence, geography, reapplication checks
  - `algorithmic.py` — funding value, timing, geography modifier scoring
  - `llm.py` — geography gating via LLM + Phase 1 mock heuristics for other dimensions
  - `pipeline.py` — async `score_opportunity()` + sync `run_scoring_pipeline()` entry point
- `prompts/`
  - prompt templates used by classification and extraction

## How The Pipeline Works

1. `process_emails()` fetches recent messages from Graph (all, not just unread — read status is not used as a gate).
2. For each message:
   - skip if already processed (Cosmos point-read by `emailId`),
   - classify (`mini`),
   - if low confidence (`< CONFIDENCE_THRESHOLD`), classify again with `full`,
   - if class is relevant, extract opportunities (`mini`),
   - if extraction confidence is low (or empty), extract again with `full`,
   - run scoring pipeline on extracted opportunities (in-process, no HTTP hop),
   - upsert parsed email + scored opportunities to Cosmos,
   - mark message as read.
3. If any email fails:
   - write dead-letter record,
   - move message to `ParseFailed` folder,
   - continue processing remaining emails.

## Scoring Pipeline

After extraction, each `FundingOpportunity` passes through a multi-stage scoring pipeline:

```
check_extraction_confidence()   — algorithmic: pass if confidence ≥ 0.5
check_reapplication()           — algorithmic: pass unless relationship = "previously-applied"
check_geography_with_llm()      — LLM: assess geographic eligibility for Kent-based charity
                                  falls back to keyword matching if LLM call fails
  → hard fail if explicitly out-of-area (Scotland, Wales, West Midlands etc.)
  → needs_review if location unknown or unspecified
  → passed if Kent / South East / UK-wide

score_opportunity_with_llm()    — Phase 1 mock heuristics (keyword-based):
  eligibility, strategic_fit, effort, probability, strategic_value

algorithmic scoring:
  funding_value (£ bands), timing (days to deadline), geography modifier

final_score = weighted composite (0–100):
  strategic_fit  30%
  funding_value  35%
  probability    15%
  strategic_value 15%
  effort          5%

suggested_tags: Quick Win, Multi-Year, Strong Match, High Value
```

Gating status:
- `passed` — all gates pass, known geography
- `needs_review` — a gate failed or geography is unspecified (human should confirm)
- `failed` — geography explicitly out of area (skips all further scoring)

Scoring failures are non-fatal: opportunities are stored unscored rather than dropped.

## Directory Structure

```text
email_parsing/
  function_app.py           — Azure Functions entry point (all HTTP + timer triggers)
  host.json
  local.settings.json.example
  local.settings.json       — local secrets (gitignored)
  requirements.txt
  requirements-dev.txt      — adds fastapi, uvicorn, pytest
  local_server.py           — FastAPI dev server for local testing (no Azure required)
  test_local.py             — terminal test script (hardcoded sample emails)
  pipeline_test.py          — end-to-end test against real Outlook emails via Graph API
  core/
    __init__.py
    config.py
    email_client.py
    llm_parser.py
    read_emails.py          — MSAL device-code flow helper for personal Outlook auth
    schema.py
    storage.py
  scoring/
    __init__.py
    models.py
    gating.py
    algorithmic.py
    llm.py
    pipeline.py
  prompts/
    classify.txt
    extract.txt
  sample/
    *.txt
    eml_to_txt.py
  tests/
    conftest.py
    test_email_client.py
    test_llm_parser.py
    test_storage.py
  infra/
    main.bicep
    deploy.sh
    parameters.json
```

## Data Contracts

Primary model: `FundingOpportunity` in `core/schema.py`.

Core fields (extracted by LLM):
- `id`, `funderName`, `programName`, `amount`, `amountMax`, `type`, `deadline`
- `location`, `duration`, `durationMonths`, `relationship`, `status`
- `description`, `eligibility`, `tags`, `website`, `notes`
- `extractionConfidence` (`0.0` to `1.0`)

Scoring fields (populated after scoring pipeline):
- `final_score` — composite score `0–100` (overwrites raw LLM `score`)
- `gating` — `GatingResult` with status (`passed` / `needs_review` / `failed`) and per-gate results
- `scores` — `ScoresResult` with strategic_fit, funding_value, probability, effort, strategic_value
- `timing` — `TimingResult` with score and days_to_deadline
- `suggested_tags` — auto-generated tags e.g. `Quick Win`, `High Value`, `Multi-Year`, `Strong Match`
- `scored_at` — UTC timestamp of when scoring ran

Other models:
- `ClassificationResult`
  - `classification`: `FUNDING_OPPORTUNITY | NEWSLETTER | IRRELEVANT`
  - `confidence`: float (`0.0` to `1.0`)
  - `reason`: string
- `ParsedEmail`
  - top-level stored document with email metadata, classification, model used, and opportunities list

## Environment Variables

All configuration is read from environment variables in `core/config.py`.

Required:

- Graph
  - `GRAPH_TENANT_ID`
  - `GRAPH_CLIENT_ID`
  - `GRAPH_CLIENT_SECRET`
  - `GRAPH_USER_EMAIL`
- Azure OpenAI
  - `AZURE_OPENAI_ENDPOINT`
  - `AZURE_OPENAI_KEY`
  - `AZURE_OPENAI_DEPLOYMENT` (default: `gpt-4o-mini`)
  - `AZURE_OPENAI_DEPLOYMENT_FULL` (default: `gpt-4o`)
- Cosmos DB
  - `COSMOS_ENDPOINT`
  - `COSMOS_KEY`
  - `COSMOS_DATABASE` (default: `email-parser`)
  - `COSMOS_CONTAINER` (default: `opportunities`)

For local development, copy:

```bash
cp local.settings.json.example local.settings.json
```

Then fill in values.

## Local Development

### Prerequisites

- Python 3.11
- Azure Functions Core Tools v4
- uv (recommended)

### Setup with uv

```bash
cd email_parsing
python3 -m uv python install 3.11
python3 -m uv venv --python 3.11
python3 -m uv pip install --python .venv/bin/python -r requirements-dev.txt
```

### Run locally (Azure Functions)

```bash
cd email_parsing
source .venv/bin/activate
func start
```

The app starts with route prefix `api` (from `host.json`).

## Local Testing Without Azure (Gemini)

The `feature/scoring_email` branch includes a full local test setup that requires no Azure subscription, no Cosmos DB, and no Microsoft 365 tenant. It uses Google Gemini as a drop-in LLM replacement.

### One-time setup

**1. Get a Gemini API key**

Go to [aistudio.google.com](https://aistudio.google.com) → Get API key → Create API key.

**2. Configure credentials**

```bash
cp local.settings.json.example local.settings.json
```

Edit `local.settings.json` and set:

```json
"AZURE_OPENAI_ENDPOINT": "https://generativelanguage.googleapis.com/v1beta/openai/",
"AZURE_OPENAI_KEY": "<your-gemini-api-key>",
"AZURE_OPENAI_DEPLOYMENT": "gemini-2.5-pro",
"AZURE_OPENAI_DEPLOYMENT_FULL": "gemini-2.5-pro"
```

All other fields (`GRAPH_*`, `COSMOS_*`) can remain as `"not-needed-for-local-test"`.

**3. Install dependencies**

```bash
pip3 install msal requests fastapi uvicorn
```

---

### Option A — Terminal script with hardcoded sample emails

No server needed. Runs 3 built-in test emails through the full pipeline and prints results.

```bash
cd email_parsing
python3 test_local.py
```

---

### Option B — FastAPI local server

**Terminal 1 — start the server:**

```bash
cd email_parsing
uvicorn local_server:app --reload
```

Server runs at `http://localhost:8000`. Interactive docs at `http://localhost:8000/docs`.

Available endpoints:

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Liveness check + active model name |
| `POST` | `/score` | Score pre-extracted opportunity objects |
| `POST` | `/parse` | Full pipeline on a raw email dict |

Example — parse a raw email:

```bash
curl -X POST http://localhost:8000/parse \
  -H "Content-Type: application/json" \
  -d '{
    "id": "test-001",
    "subject": "Kent Community Foundation Wellbeing Fund now open",
    "from": "grants@kcf.org.uk",
    "receivedDateTime": "2026-04-17T10:00:00Z",
    "body": "Grants of £5,000–£15,000 for Kent charities supporting older people and wellbeing. Deadline 30 June 2026."
  }'
```

---

### Option C — End-to-end test against real Outlook emails

Uses MSAL device-code flow to authenticate with a personal Outlook account and pipe real emails through the pipeline.

**Register an app (one-time):**

1. Go to [entra.microsoft.com](https://entra.microsoft.com) → App registrations → New registration
2. Supported account types: `Accounts in any organizational directory and personal Microsoft accounts`
3. Copy the Application (client) ID → paste into `core/read_emails.py` as `CLIENT_ID`
4. Authentication → Allow public client flows → Yes → Save
5. API permissions → Microsoft Graph → Delegated → `Mail.Read` → Add

**Authenticate (one-time — token cached in `token_cache.bin`):**

```bash
cd email_parsing
python3 core/read_emails.py
# Opens browser device flow — sign in with your Outlook account
```

**Start the server (Terminal 1):**

```bash
uvicorn local_server:app --reload
```

**Run the pipeline test (Terminal 2):**

```bash
cd email_parsing
python3 pipeline_test.py                  # fetch 5 most recent emails
python3 pipeline_test.py --count 20       # fetch more to get past non-funding emails
python3 pipeline_test.py --full-json      # also print full schema JSON per email
```

---

### How the Gemini integration works

The `AzureOpenAI` client in `llm_parser.py` is monkey-patched at startup (in `local_server.py` and `test_local.py`) with a thin wrapper that calls Gemini's native API using `x-goog-api-key` header — the same auth method as the Gemini curl quickstart. No code in `core/` is modified.

```
AzureOpenAI(...) → _GeminiAsAzure → httpx → Gemini native API
```

Geography gating uses the same Gemini model via `check_geography_with_llm()` in `scoring/llm.py`. If that call fails, it falls back to keyword-based geography matching automatically.

---

### Rate limits

The free Gemini tier has per-minute request limits. If you hit `429 Too Many Requests`:
- Switch to `gemini-2.0-flash` in `local.settings.json` (higher free-tier limits)
- Or reduce `--count` when running `pipeline_test.py`

## Running Tests

Use uv and run only the parser test suite:

```bash
cd email_parsing
python3 -m uv run --python .venv/bin/python pytest -q tests
```

Current suite covers:

- Graph retry behavior and mailbox shaping,
- LLM JSON repair, retry/fail-fast behavior, confidence escalation,
- Cosmos dedup/upsert/query/dead-letter behavior.

## HTTP Endpoints

### `POST /api/scan`

Manually trigger processing run.

- Auth level: `FUNCTION`
- Returns summary:

```json
{
  "processed": 10,
  "opportunities": 24,
  "failures": 1
}
```

### `GET /api/opportunities`

Returns flattened opportunity rows from Cosmos including all scoring fields.

- Auth level: `ANONYMOUS`
- Optional query params:
  - `type`
  - `status`
  - `funderName`

### `POST /api/score`

Score a list of pre-extracted opportunity objects on demand.

- Auth level: `FUNCTION`
- Body: array of `OpportunityInput` objects
- Returns: array of `ScoredOpportunity` objects with gating, scores, and final_score

### `GET /api/dead-letters`

List failed emails awaiting manual review.

- Auth level: `FUNCTION`
- Optional: `?include_resolved=true`

### `POST /api/dead-letters/{email_id}/retry`

Reprocess a specific failed email.

- Auth level: `FUNCTION`

## Error Handling And Reliability

### What is implemented

- Graph API:
  - exponential backoff on `429` and transient `5xx`, plus transport exceptions.
- LLM calls:
  - bounded retry loop with timeout,
  - retry for rate-limit and transient failures,
  - clear typed errors (`LLMInvocationError`, `LLMOutputError`),
  - one JSON repair attempt when model returns invalid JSON,
  - fail-open toggle for classification fallback (`_CLASSIFICATION_FAIL_OPEN`, default `False`).
- Pipeline robustness:
  - per-email failure isolation,
  - dead-letter persistence,
  - continue processing remaining emails.

### Reliability defaults

- confidence threshold: `0.7`
- LLM max attempts: `3`
- LLM timeout: `45s`

### Cost controls

- default model is `gpt-4o-mini`
- escalate to `gpt-4o` only on low confidence or extraction failure paths

## Prompt Engineering Workflow

Prompt templates are loaded from disk once and cached by process lifetime.

- Classification template: `prompts/classify.txt`
- Extraction template: `prompts/extract.txt`

Use placeholders:

- `{{subject}}`
- `{{body}}`
- `{{email_id}}` (extract only)

Recommended process:

1. update prompt text,
2. run parser tests,
3. run a small sample scan,
4. inspect extracted records and dead-letters,
5. iterate.

## Deployment

Infrastructure and deploy script are in `infra/`.

```bash
cd email_parsing/infra
./deploy.sh --resource-group charity-email-parser-rg --location uksouth
```

After deployment:

1. set Key Vault secret values,
2. set `GRAPH_CLIENT_ID` and `GRAPH_USER_EMAIL` app settings,
3. confirm function health with a manual `POST /api/scan`.

## Operations And Troubleshooting

### Common issues

1. `Pipeline modules failed to import`
   - check env vars and package installation.
2. frequent dead-letters with JSON parse errors
   - tighten extraction prompt output constraints,
   - review model deployment names and API version.
3. no opportunities returned from API
   - verify Cosmos container name and partition-key expectations,
   - confirm documents are being upserted with `id == emailId`.
4. tests fail with import errors
   - run from `email_parsing` and use uv command shown above.

### Observability tips

- monitor:
  - count of processed/failures,
  - dead-letter volume,
  - escalation rate to full model,
  - LLM retry count.
- keep logs structured by stage (`classify`, `extract`, `json_repair`) and `email_id` where available.

## Notes

- This service is designed for safe batch continuation: one bad email does not block the run.
- Keep all secrets in environment/Key Vault references; do not hardcode credentials.
- Scoring failures are non-fatal: if the scoring pipeline errors, opportunities are stored unscored rather than dropped.
- `local.settings.json` is gitignored — never commit it.
- Phase 1 scoring uses mock heuristics for `strategic_fit`, `effort`, `probability`, and `strategic_value`. Geography gating already uses a real LLM call. Full LLM scoring is the Phase 2 upgrade path (see `scoring/prompt.md` for the planned prompt).
