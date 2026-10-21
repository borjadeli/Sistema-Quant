from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

from qa.options.analyzer import analyze_calls, bs_call, interpolate_target, mixture_prob_above, rn_prob_above
from qa.portfolio import ledger
from qa.portfolio.construction import PositionMeta, enforce_constraints
from qa.portfolio.metrics import brier, concentration, performance, scenario_outcome
from qa.providers.base import OptionChain, OptionQuoteData


def test_black_scholes_reference():
    assert bs_call(100, 100, 1, 0.05, 0.2) == pytest.approx(10.4506, abs=1e-3)
    assert rn_prob_above(100, 1e-9, 1, 0.05, 0.2) == 1.0


def test_mixture_and_interpolation():
    assert mixture_prob_above([(1.0, 100, 0.3)], 100) == pytest.approx(0.5)
    assert interpolate_target(10, {12: 20, 24: 40, 36: 80}, 6) == pytest.approx(10 * 2 ** 0.5)


def _chain(age_h=1):
    now = datetime.now(timezone.utc)
    exp = date.today() + timedelta(days=365)
    q = [OptionQuoteData("X", now, exp, k, "C", b, b * 1.05, None, 100, 500, 0.6, 100.0, "t")
         for k, b in ((100, 22.0), (150, 9.0), (200, 4.0))]
    q.append(OptionQuoteData("X", now, exp, 120, "C", 1.0, 3.0, None, 1, 1, 0.6, 100.0, "t"))   # spread enorme
    return OptionChain("X", now - timedelta(hours=age_h), 100.0, q, "t")


def test_options_refuse_stale_chain():
    assert analyze_calls(_chain(age_h=48), None).status == "stale_chain"


def test_options_filters_and_probabilities():
    rep = analyze_calls(_chain(), None)
    assert rep.status == "no_scenarios" and rep.rejected["wide_spread"] == 1
    for c in rep.candidates:
        assert 0 <= c.rn["p_5x"] <= c.rn["p_2x"] <= c.rn["p_itm"] <= 1
        assert c.breakeven == pytest.approx(c.strike + c.premium)


def _meta(**kw):
    base = dict(layer="L", primary_bottleneck="B", eligible=True)
    return base | kw


def test_constraints():
    meta = {"A": PositionMeta("A", **_meta()), "B": PositionMeta("B", **_meta()),
            "C": PositionMeta("C", **_meta(eligible=False, kill_reasons=["K3"])),
            "D": PositionMeta("D", **_meta(layer="M", primary_bottleneck="Z", speculative=True))}
    w, notes = enforce_constraints({"A": 0.3, "B": 0.2, "C": 0.1, "D": 0.1}, meta)
    assert "C" not in w and w["D"] <= 0.03
    assert all(v <= 0.10 + 1e-9 for v in w.values())
    assert w["A"] + w["B"] <= 0.25 + 1e-9                     # tope por bottleneck
    assert sum(w.values()) <= 0.98 + 1e-9


def test_ledger_rebalance_and_cash(session):
    now = datetime.now(timezone.utc)
    q = {"A": ledger.Quote(10.0, now, "t", "large"), "B": ledger.Quote(20.0, now, "t", "small")}
    ledger.rebalance(session, "P", {"A": 0.1, "B": 0.05}, q)
    h = ledger.holdings(session, "P")
    assert h["A"] == pytest.approx(1000, rel=1e-3) and h["B"] == pytest.approx(250, rel=1e-3)
    nav = ledger.nav(session, "P", q)
    assert 99_800 < nav < 100_000                               # costos y slippage reducen NAV
    ledger.rebalance(session, "P", {}, q)
    assert ledger.holdings(session, "P") == {}


def test_live_trading_blocked(monkeypatch):
    from qa import config
    s = dict(config.settings())
    s["portfolio"] = dict(s["portfolio"], live_trading_enabled=True)
    monkeypatch.setattr(ledger, "settings", lambda: s)
    with pytest.raises(ledger.LiveTradingBlocked):
        ledger.assert_simulated()


def test_metrics():
    idx = pd.date_range("2025-01-01", periods=300, freq="B").date
    nav = pd.Series([100 + i * 0.1 for i in range(300)], index=idx)
    nav.iloc[150] = 90
    p = performance(nav, nav * 1.0)
    assert p["max_drawdown"] < -0.05 and p["n_obs"] == 300
    assert concentration({"a": 0.5, "b": 0.5})["effective_n"] == pytest.approx(2)
    assert scenario_outcome({"bear": 5, "base": 10, "bull": 20}, 11) == "base"
    assert brier([({"bear": 0, "base": 1, "bull": 0}, "base")]) == 0
