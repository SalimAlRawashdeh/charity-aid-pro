from app.algorithmic import get_geography_modifier, score_funding_value, score_timing


class TestFundingValue:
    def test_small_grant(self):
        score, used = score_funding_value(500, None)
        assert score == 3
        assert used == 500

    def test_medium_grant(self):
        score, used = score_funding_value(3000, None)
        assert score == 5

    def test_solid_grant(self):
        score, used = score_funding_value(10000, None)
        assert score == 7

    def test_significant_grant(self):
        score, used = score_funding_value(20000, None)
        assert score == 9

    def test_large_grant(self):
        score, used = score_funding_value(50000, None)
        assert score == 10

    def test_amount_max_preferred(self):
        score, used = score_funding_value(1000, 20000)
        assert score == 9
        assert used == 20000

    def test_boundaries(self):
        assert score_funding_value(2000, None)[0] == 5
        assert score_funding_value(5000, None)[0] == 7
        assert score_funding_value(15000, None)[0] == 9
        assert score_funding_value(30000, None)[0] == 10


class TestTiming:
    def test_unknown_deadline(self):
        score, days = score_timing("unknown")
        assert score is None
        assert days is None

    def test_invalid_date(self):
        score, days = score_timing("not-a-date")
        assert score is None

    def test_far_future(self):
        score, days = score_timing("2028-01-01")
        assert score == 2
        assert days is not None and days > 180


class TestGeographyModifier:
    def test_kent_only(self):
        assert get_geography_modifier("kent_only") == 1.10

    def test_uk_regional(self):
        assert get_geography_modifier("uk_regional") == 1.05

    def test_uk_wide(self):
        assert get_geography_modifier("uk_wide") == 1.00

    def test_unknown(self):
        assert get_geography_modifier(None) == 1.00
