# Charity Aid Scoring Pipeline

Stateless scoring engine for **M4W (Music for Wellbeing)**, a small Kent-based UK charity. Scores incoming grant opportunities so M4W can prioritise which grants to apply for.

Built with **Python 3.11+** and **FastAPI**. Currently running **Phase 1** with deterministic mock scoring.

---

## How to Run

```bash
cd scoring
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

API at `http://localhost:8000`. Interactive docs at `http://localhost:8000/docs`.

## How to Test

```bash
pytest tests/ -v
```

45 tests covering algorithmic scoring, gating, mock LLM, and end-to-end API.

To test manually, POST `test_opportunities_curated.json` to `http://localhost:8000/score` via Postman or curl:

```bash
curl -X POST http://localhost:8000/score \
  -H "Content-Type: application/json" \
  -d @test_opportunities_curated.json
```

---

## How It Works

`POST /score` accepts a JSON array of grant opportunities and returns the same array with scoring results appended to each object.

Each opportunity flows through these stages (orchestrated in `pipeline.py`):

### 1. Gating

Four checks determine whether an opportunity should be scored, flagged for review, or discarded.

| Check | Logic | Fail behaviour |
|---|---|---|
| **Extraction Confidence** | Pass if `extractionConfidence` >= 0.5 | `needs_review` (still scored) |
| **Eligibility** | Mock: keyword match on `description` + `eligibility` against M4W themes | `needs_review` (still scored) |
| **Geography** | Pass if `location` contains Kent areas, UK, England, nationwide. Fail on Scotland, Wales, etc. | Hard `failed` (scoring skipped) or `needs_review` if ambiguous |
| **Reapplication** | Pass if `relationship` is `new`, `re-eligible`, or `existing-funder` | `needs_review` for `previously-applied` |

Geography hard fail is the only case where scoring is skipped entirely and the LLM is not called. All other failures produce `needs_review` — the opportunity is still scored so the dashboard has data for human review.

### 2. LLM Scoring (mock)

A single function (`llm.py:score_opportunity_with_llm`) returns eligibility + four scored dimensions. Currently uses deterministic keyword heuristics:

- **Eligibility** — pass if any M4W keyword found in description/eligibility text
- **Strategic Fit (0-10)** — keyword hit count x2, clamped 1-10
- **Effort (0-10)** — detects effort signals ("EOI"=9, "theory of change"=3) or infers from funder type. Higher = less effort = better
- **Probability (0-10)** — based on grant size band, mirroring M4W's historical win rates
- **Strategic Value (0-10)** — base 3, +3 multi-year, +2 partnership keywords, +2 core/unrestricted

### 3. Algorithmic Scoring

Pure functions, no mock involved:

- **Funding Value (0-10)** — from grant amount: <2k=3, 2-5k=5, 5-15k=7, 15-30k=9, 30k+=10
- **Timing** — urgency from days to deadline (not included in weighted score, returned separately)
- **Geography Modifier** — multiplier on Strategic Fit: Kent=1.10x, regional=1.05x, UK-wide=1.00x

### 4. Weighting

```
final_score = (strategic_fit_final * 0.30 + funding_value * 0.35 + probability * 0.15 + strategic_value * 0.15 + effort * 0.05) * 10
```

### 5. Tag Generation

| Tag | Condition |
|---|---|
| Quick Win | effort >= 8 AND timing >= 8 |
| Multi-Year | duration is multi-year |
| Strong Match | strategic fit (final) >= 8 AND probability >= 7 |
| High Value | funding value >= 9 |

---

## Project Structure

```
scoring/
├── app/
│   ├── main.py           # FastAPI app — POST /score, GET /health
│   ├── models.py          # Pydantic input/output schemas
│   ├── algorithmic.py     # Funding value, timing, geography modifier
│   ├── gating.py          # Extraction confidence, eligibility, geography, reapplication
│   ├── llm.py             # Single scoring function (mock now, Azure later)
│   └── pipeline.py        # Orchestrates gating → LLM → weighting → tags → output
├── tests/
├── prompt.md              # LLM system prompt for Phase 2
├── test_opportunities_curated.json   # 20 realistic test opportunities
├── pyproject.toml
└── scoring-pipeline-plan.md
```

---

## Current Limitations

1. **Strategic fit is purely keyword-based.** Funders whose descriptions don't use M4W vocabulary (e.g., The Fore talks about "scaling impact" not "older people") score low despite being excellent matches.

2. **Probability ignores funder relationship and alignment quality.** Only uses grant size band. An existing funder with strong alignment should score higher than a new funder at the same amount — the mock can't distinguish this.

3. **Effort detection is coarse.** Only fires on specific phrases ("EOI", "theory of change"). Many grants don't mention their process in the description.

4. **Eligibility gating is too permissive.** Any single keyword match passes. A "community garden" grant would pass even though it has nothing to do with M4W's specific work.

5. **"High Value" tag can be misleading.** Fires on funding_value >= 9 regardless of alignment — a £50k tech grant gets tagged "High Value" despite being irrelevant.

---

## Phase 2 — Azure OpenAI Integration

When Azure access is available, one function changes: **`llm.py:score_opportunity_with_llm()`**. The mock keyword body gets replaced with an Azure OpenAI GPT-4o-mini call. The system prompt is defined in `prompt.md`. The function signature and return shape stay identical.

This single change resolves all five limitations above — the LLM can understand context, assess mission alignment semantically, infer effort from funder type and grant size, and adjust probability based on funder relationship quality.

**What stays the same:**
- All algorithmic functions (funding value, timing, geography modifier)
- Geography and reapplication gating
- Extraction confidence gating
- Weight formula and tag generation
- API contract (POST /score input/output)
- Pipeline orchestration
