import pytest

from qa.fundamentals.metrics import compute_fundamentals
from qa.providers.manual import FixtureProvider
from qa.scoring import engine
from qa.scoring.engine import FactorScore, combine, score_financial_quality, score_valuation
from qa.universe.model import Company, Exposure
from qa.valuation.core import MarketInputs, ScenarioAssumptions, compute_multiples, reverse_dcf, run_scenarios


def fund(cid):
    c = Company(ticker=cid, name="t", exchange="NASDAQ", country="US", currency="USD", nodes=["gpu"], exposure=Exposure())
    return compute_fundamentals(cid, FixtureProvider().get_facts(c))


MK = MarketInputs(price=40, shares=103e6, price_currency="USD", price_time="2026-10-21T00:00:00+00:00", price_source="t")


def scen(bear=0.25, base=0.5, bull=0.25):
    return [ScenarioAssumptions(name="bear", probability=bear, revenue_growth=[0, .05, .05], ebit_margin=[.15] * 3, exit_multiple=12),
            ScenarioAssumptions(name="base", probability=base, revenue_growth=[.3, .25, .2], ebit_margin=[.22, .24, .25], exit_multiple=18),
            ScenarioAssumptions(name="bull", probability=bull, revenue_growth=[.45, .4, .3], ebit_margin=[.25, .28, .3], exit_multiple=25)]


def test_multiples_not_meaningful_for_losses():
    f = fund("TEST_BURN")
    m = compute_multiples(f, MarketInputs(price=5, shares=140e6, price_currency="USD", price_time="x", price_source="t"))
    assert m.values["EV/EBITDA"] is None and "negativo" in m.not_meaningful["EV/EBITDA"]
    assert m.values["P/E"] is None


def test_scenarios_validation_and_monotonic():
    f = fund("TEST_ALPHA")
    with pytest.raises(ValueError):
        run_scenarios(scen(0.3, 0.5, 0.3), f, MK)
    ss = run_scenarios(scen(), f, MK)
    r = {x.name: x.returns[36] for x in ss.results}
    assert r["bear"] < r["base"] < r["bull"]
    assert ss.expected_return[36] == pytest.approx(sum(x.probability * x.returns[36] for x in ss.results))
    assert "subjective" in ss.probability_basis


def test_scenario_requires_one_revenue_definition():
    with pytest.raises(ValueError):
        ScenarioAssumptions(name="base", probability=1, revenue_growth=[.1] * 3, revenue_path=[1, 2, 3],
                            ebit_margin=[.1] * 3, exit_multiple=10)


def test_cash_burner_needs_financing():
    f = fund("TEST_BURN")
    mk = MarketInputs(price=5, shares=140e6, price_currency="USD", price_time="x", price_source="t")
    a = [ScenarioAssumptions(name=n, probability=p, revenue_growth=[.3] * 3, ebit_margin=[-.5, -.3, -.1], exit_multiple=3,
                             exit_multiple_type="EV/Sales", capex_pct_revenue=.25) for n, p in
         (("bear", .3), ("base", .4), ("bull", .3))]
    ss = run_scenarios(a, f, mk)
    assert all(r.financing_need > 0 and r.dilution_from_financing > 0 for r in ss.results)


def test_reverse_dcf_monotonic_in_price():
    f = fund("TEST_ALPHA")
    lo = reverse_dcf(f, MarketInputs(price=20, shares=103e6, price_currency="USD", price_time="x", price_source="t"))
    hi = reverse_dcf(f, MarketInputs(price=80, shares=103e6, price_currency="USD", price_time="x", price_source="t"))
    assert lo.implied_revenue_cagr_10y < hi.implied_revenue_cagr_10y


def test_valuation_rule_range_and_none():  # anclas exactas: test_r3_valuation_rule_anchors_and_penalty
    ss = run_scenarios(scen(), fund("TEST_ALPHA"), MK)
    fs = score_valuation(ss)
    assert 0 <= fs.score <= 10 and fs.method == "rule"
    assert score_valuation(None).score == 0


def _factors(v):
    return {k: FactorScore(score=v, rationale="t") for k in engine.FACTORS}


def test_caps_block_narrative_compensation():
    f = _factors(9.0)
    f["financial_quality"] = FactorScore(score=1.0, rationale="mala")
    sc = combine("X", f)
    assert sc.total_uncapped > 7 and sc.total == 5.0 and sc.caps_applied


def test_kill_switches():
    f = fund("TEST_BURN")
    sc = combine("X", _factors(8), fundamentals=f, adv_usd=1e5)
    assert any(k.startswith("K4") for k in sc.kill_switches)          # sintético
    assert any(k.startswith("K3") for k in sc.kill_switches)          # liquidez
    assert not sc.eligible
    bad = scen()
    for a in bad:
        a.exit_multiple = 2
    ss = run_scenarios(bad, fund("TEST_ALPHA"), MK)
    sc2 = combine("X", _factors(8), fundamentals=fund("TEST_ALPHA"), scenarios=ss)
    assert any(k.startswith("K5") for k in sc2.kill_switches)


def test_financial_quality_orders_quality_over_burner():  # faltantes: test_r3_missing_core_metric_*
    assert score_financial_quality(fund("TEST_ALPHA")).score > score_financial_quality(fund("TEST_BURN")).score
