import asyncio

from fastapi import FastAPI

from app.models import OpportunityInput, ScoredOpportunity
from app.pipeline import score_opportunity

app = FastAPI(title="Charity Aid Scoring Pipeline", version="0.1.0")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/score", response_model=list[ScoredOpportunity])
async def score(opportunities: list[OpportunityInput]):
    results = await asyncio.gather(
        *(score_opportunity(opp) for opp in opportunities)
    )
    return list(results)
