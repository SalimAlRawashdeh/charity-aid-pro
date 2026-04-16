import pytest

from app.llm import score_opportunity_with_llm


@pytest.mark.asyncio
async def test_returns_all_dimensions():
    opp = {
        "description": "Community wellbeing project for older people",
        "eligibility": "Music and arts organisations",
        "type": "trust",
        "amount": 5000,
        "amountMax": None,
        "duration": "single-year",
        "durationMonths": 12,
    }
    result = await score_opportunity_with_llm(opp)
    assert set(result.keys()) == {
        "eligibility", "strategic_fit", "effort", "probability", "strategic_value",
    }
    # Check eligibility shape
    elig = result["eligibility"]
    assert "pass" in elig
    assert "confidence" in elig
    assert "reasoning" in elig
    # Check score dimensions
    for key in ["strategic_fit", "effort", "probability", "strategic_value"]:
        dim = result[key]
        assert "score" in dim
        assert "reasoning" in dim
        assert 0 <= dim["score"] <= 10


@pytest.mark.asyncio
async def test_eligible_with_keywords():
    opp = {
        "description": "Community wellbeing project for older people",
        "eligibility": "Music and arts organisations",
        "type": "trust",
        "amount": 5000,
        "amountMax": None,
        "duration": "single-year",
        "durationMonths": 12,
    }
    result = await score_opportunity_with_llm(opp)
    assert result["eligibility"]["pass"] is True
    assert result["eligibility"]["confidence"] > 0


@pytest.mark.asyncio
async def test_not_eligible_without_keywords():
    opp = {
        "description": "Technology startup funding for software companies",
        "eligibility": "Digital enterprises only",
        "type": "corporate",
        "amount": 50000,
        "amountMax": None,
        "duration": "single-year",
        "durationMonths": 12,
    }
    result = await score_opportunity_with_llm(opp)
    assert result["eligibility"]["pass"] is False
    assert result["eligibility"]["confidence"] == 0.0


@pytest.mark.asyncio
async def test_high_fit_keywords():
    opp = {
        "description": "wellbeing older people isolation community disability arts music mental health",
        "eligibility": "social prescribing loneliness elderly",
        "type": "trust",
        "amount": 5000,
        "amountMax": None,
        "duration": "single-year",
        "durationMonths": 12,
    }
    result = await score_opportunity_with_llm(opp)
    assert result["strategic_fit"]["score"] == 10


@pytest.mark.asyncio
async def test_low_effort_eoi():
    opp = {
        "description": "Expression of interest only",
        "eligibility": "",
        "type": "trust",
        "amount": 1000,
        "amountMax": None,
        "duration": "single-year",
        "durationMonths": 12,
    }
    result = await score_opportunity_with_llm(opp)
    assert result["effort"]["score"] == 9


@pytest.mark.asyncio
async def test_multi_year_strategic_value():
    opp = {
        "description": "Core funding for partnership project",
        "eligibility": "",
        "type": "trust",
        "amount": 10000,
        "amountMax": None,
        "duration": "multi-year",
        "durationMonths": 36,
    }
    result = await score_opportunity_with_llm(opp)
    assert result["strategic_value"]["score"] >= 8
