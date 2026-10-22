"""Regresiones de la revisión adversarial (hallazgos #0-#47). Cada test reproduce el escenario de falla reportado."""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pandas as pd
import pytest

from qa.agents.base import Budget, RunContext, run_agent
from qa.agents.llm import LLMError, ScriptedLLM, price_tokens, usage_cost
from qa.db import repo
from qa.db.schema import AgentRun, FeedEvent, MapChangeProposal, MarketSnapshot, Recommendation, ScenarioSet as ScRow
from qa.demo import scripted_responses
from qa.fundamentals.metrics import compute_fundamentals
from qa.portfolio.construction import PositionMeta, enforce_constraints
from qa.portfolio.metrics import performance
from qa.provenance import DataPoint, Source, Verification
from qa.providers.sec_edgar import parse_companyfacts
from qa.scoring import engine
from qa.scoring.engine import FactorScore, combine, kill_switches, score_financial_quality, score_valuation
from qa.universe.model import Company, Exposure
from qa.valuation.core import MarketInputs, ScenarioAssumptions, ScenarioResult, ScenarioSet, run_scenario


# ----------------------------------------------------------------------------- helpers
def fact(val, start, end, filed, form="10-Q", frame=None, accn="A"):
    r = {"val": val, "end": end, "filed": filed, "form": form, "accn": accn}
    if start:
        r["start"] = start
    if frame:
        r["frame"] = frame
    return r


def sec(concepts: dict) -> dict:
    return {"facts": {"us-gaap": {k: {"units": {"USD": v}} for k, v in concepts.items()}}}


def dp(metric, v, end, ptype, known, ver=Verification.CONFIRMED_FILING, src="sec_edgar", unit="USD", ccy="USD"):
    return DataPoint(entity="X", metric=metric, value=v, unit=unit, currency=ccy, period_end=date.fromisoformat(end),
                     period_type=ptype, verification=ver, source=Source(name=src),
                     known_at=date.fromisoformat(known))


def ytd_company(years=(2024, 2025), ocf_q=(-10, -12, -13, -15), capex_q=(5, 5, 5, 5)):
    """10-Q con flujo de caja ACUMULADO (Q1, 6M, 9M) + 10-K FY, como en la SEC real."""
    rows = {"NetCashProvidedByUsedInOperatingActivities": [], "PaymentsToAcquirePropertyPlantAndEquipment": [],
            "Revenues": [], "CashAndCashEquivalentsAtCarryingValue": []}
    for y in years:
        ends = [f"{y}-03-31", f"{y}-06-30", f"{y}-09-30", f"{y}-12-31"]
        files = [f"{y}-05-01", f"{y}-08-01", f"{y}-11-01", f"{y + 1}-02-15"]
        forms = ["10-Q", "10-Q", "10-Q", "10-K"]
        for metric, qv in (("NetCashProvidedByUsedInOperatingActivities", ocf_q),
                           ("PaymentsToAcquirePropertyPlantAndEquipment", capex_q)):
            acc = 0
            for i in range(4):
                acc += qv[i]
                rows[metric].append(fact(acc, f"{y}-01-01", ends[i], files[i], forms[i]))
        for i in range(4):
            if i < 3:
                rows["Revenues"].append(fact(100, f"{y}-{3 * i + 1:02d}-01", ends[i], files[i], forms[i]))
        rows["Revenues"].append(fact(400, f"{y}-01-01", ends[3], files[3], "10-K"))
        rows["CashAndCashEquivalentsAtCarryingValue"].append(
            {"val": 60, "end": ends[3], "filed": files[3], "form": "10-K", "accn": "A"})
    return parse_companyfacts(sec(rows), "X", cik=1)


MK = MarketInputs(price=10, shares=50, price_currency="USD", price_time="2026-10-21T00:00:00+00:00", price_source="t")


# ----------------------------------------------------------------------------- #0/#12 YTD cash flow
def test_ytd_cashflow_reconstructs_quarters_and_runway_fires_k2():
    f = compute_fundamentals("X", ytd_company(), as_of=date(2026, 3, 1))
    assert f.v("ocf_ttm") == pytest.approx(-50) and f.v("capex_ttm") == pytest.approx(20)
    assert f.v("fcf_ttm") == pytest.approx(-70)
    assert f.v("cash_runway_quarters") == pytest.approx(60 / (70 / 4))
    kills, _ = kill_switches(f, None, None, {}, None, False)
    assert any(k.startswith("K2") for k in kills)


# ----------------------------------------------------------------------------- #1 cash/debt unknown
def _fund_with_balance(cash=True, debt=True):
    pts = [dp("revenue", 250, f"2026-{m:02d}-{d}", "Q", "2026-10-01") for m, d in ((3, 31), (6, 30), (9, 30))]
    pts.append(dp("revenue", 250, "2025-12-31", "Q", "2026-02-01"))
    if cash:
        pts.append(dp("cash", 50, "2026-09-30", "instant", "2026-10-01"))
    if debt:
        pts.append(dp("debt_total", 900, "2026-09-30", "instant", "2026-10-01"))
    return compute_fundamentals("X", pts, as_of=date(2026, 10, 15))


def test_unknown_cash_refuses_valuation_and_unknown_debt_is_explicit_assumption():
    a = ScenarioAssumptions(name="base", probability=1, revenue_growth=[.1] * 3, ebit_margin=[.2] * 3, exit_multiple=10)
    with pytest.raises(ValueError, match="caja desconocida"):
        run_scenario(a, _fund_with_balance(cash=False), MK)
    r = run_scenario(a, _fund_with_balance(debt=False), MK)
    assert any("SUPONE 0" in n for n in r.notes)


# ----------------------------------------------------------------------------- #2 equity floor, #3 interest paid
def test_equity_floor_and_interest_reduce_cash():
    f = _fund_with_balance()
    bear = ScenarioAssumptions(name="bear", probability=1, revenue_growth=[-.1] * 3, ebit_margin=[.03] * 3, exit_multiple=6)
    r = run_scenario(bear, f, MK)
    assert all(v >= -1.0 for v in r.returns.values())
    no_int = ScenarioAssumptions(name="base", probability=1, revenue_growth=[.05] * 3, ebit_margin=[.12] * 3,
                                 exit_multiple=10, interest_rate=0.0)
    with_int = no_int.model_copy(update={"interest_rate": 0.05})
    assert run_scenario(with_int, f, MK).years[2]["cash"] < run_scenario(no_int, f, MK).years[2]["cash"]


# ----------------------------------------------------------------------------- #4/#40 monthly NAV, #11 window
def test_performance_infers_monthly_frequency_and_aligned_window():
    idx = pd.date_range("2024-01-31", periods=36, freq="ME").date
    rng = [0.05 if i % 2 else -0.05 for i in range(35)]
    nav = pd.Series([100.0] + list(100 * pd.Series(rng).add(1).cumprod()), index=idx)
    p = performance(nav)
    assert p["periods_per_year"] == pytest.approx(12, rel=0.05)
    assert p["volatility"] == pytest.approx(pd.Series(nav).pct_change().std() * math.sqrt(12), rel=0.03)
    short_bench = nav[nav.index <= date(2025, 6, 30)]
    assert "bench_note" in performance(nav, short_bench)


# ----------------------------------------------------------------------------- #5 single balance date
def test_balance_sheet_uses_single_date():
    pts = [dp("cash", 100, "2026-06-30", "instant", "2026-08-01"), dp("short_term_investments", 500, "2025-12-31", "instant", "2026-02-01"),
           dp("debt_total", 800, "2023-12-31", "instant", "2024-02-01"), dp("debt_noncurrent", 50, "2026-06-30", "instant", "2026-08-01"),
           dp("debt_current", 10, "2026-06-30", "instant", "2026-08-01")]
    f = compute_fundamentals("X", pts, as_of=date(2026, 9, 1))
    assert f.v("cash_total") == 100 and f.v("debt") == 60 and f.v("net_debt") == -40


# ----------------------------------------------------------------------------- #6/#19 units and currency
def test_units_scaled_and_currency_conflict_blocks_valuation(session):
    pts = [dp("revenue", v, e, "Q", "2026-10-01") for v, e in ((100e6, "2025-12-31"), (110e6, "2026-03-31"), (120e6, "2026-06-30"))]
    pts.append(dp("revenue", 130, "2026-09-30", "Q", "2026-10-10", unit="USD_millions", ccy=None, src="manual"))
    f = compute_fundamentals("X", pts, as_of=date(2026, 10, 15))
    assert f.v("revenue_ttm") == pytest.approx(460e6)
    mixed = [dp("revenue", 100, "2026-06-30", "Q", "2026-08-01", unit="EUR", ccy="EUR"),
             dp("revenue", 100, "2026-03-31", "Q", "2026-05-01", unit="EUR", ccy="EUR"),
             dp("revenue", 100, "2026-09-30", "Q", "2026-10-10", unit="USD", ccy="USD", src="seed")]
    fm = compute_fundamentals("X", mixed, as_of=date(2026, 10, 15))
    assert fm.currency_conflict and fm.currency == "EUR"
    from qa.funnel.pack import market_inputs
    c = Company(ticker="X", name="x", exchange="NASDAQ", country="US", currency="USD", nodes=["gpu"], exposure=Exposure())
    session.add(MarketSnapshot(company_id="X", as_of=datetime(2026, 10, 14, tzinfo=timezone.utc), price=10, currency="USD",
                               shares_outstanding=10, market_cap=100, market_cap_usd=100, adv_usd_20d=1e7, source="t"))
    session.flush()
    assert market_inputs(session, c, fm, date(2026, 10, 15)) is None


# ----------------------------------------------------------------------------- #7 options edge financing
def test_option_edge_is_zero_when_beliefs_equal_risk_neutral():
    from qa.options.analyzer import analyze_calls
    from qa.providers.base import OptionChain, OptionQuoteData
    from qa.options.analyzer import bs_call
    now = datetime(2026, 10, 21, tzinfo=timezone.utc)
    days = 365
    T, S, K, r, iv = days / 365, 100.0, 110.0, 0.04, 0.5
    px = bs_call(S, K, T, r, iv)
    chain = OptionChain("X", now, S, [OptionQuoteData("X", now, (now + timedelta(days=days)).date(), K, "C", px * 0.999,
                                                      px * 1.001, None, 100, 1000, iv, S, "t")], "t")
    med = S * math.exp((r - iv ** 2 / 2) * T)
    months = days / 30.44
    tgt12 = math.exp(math.log(S) + (math.log(med) - math.log(S)) * 12 / months)
    res = [ScenarioResult(name=n, probability=p, years=[], price_target={12: tgt12, 24: tgt12, 36: tgt12},
                          returns={12: 0, 24: 0, 36: 0}, financing_need=0, dilution_from_financing=0)
           for n, p in (("bear", .2), ("base", .6), ("bull", .2))]
    ss = ScenarioSet(base_revenue=1, price0=S, results=res, expected_return={12: 0, 24: 0, 36: 0},
                     prob_loss={12: 0, 24: 0, 36: 0}, prob_permanent_loss=0, bear_downside_36m=0, upside_downside_ratio=None)
    rep = analyze_calls(chain, ss, r=r, dispersion_frac=1.0, now=now, min_open_interest=1)
    assert rep.candidates and abs(rep.candidates[0].edge_vs_stock) < 0.02


# ----------------------------------------------------------------------------- #8 yoy by date
def test_dilution_yoy_compares_same_quarter_by_date():
    pts = []
    for y, base in ((2024, 100), (2025, 104)):
        for i, (m, d) in enumerate(((3, 31), (6, 30), (9, 30))):
            pts.append(dp("diluted_shares", base + i * 0.3, f"{y}-{m:02d}-{d}", "Q", f"{y}-{m + 1:02d}-28", unit="shares", ccy=None))
        pts.append(dp("diluted_shares", base + 1, f"{y}-12-31", "FY", f"{y + 1}-02-15", unit="shares", ccy=None))
    f = compute_fundamentals("X", pts, as_of=date(2026, 1, 15))
    assert f.metrics["dilution_yoy"].period == "2025-09-30 vs 2024-09-30"
    assert f.v("dilution_yoy") == pytest.approx(104.6 / 100.6 - 1)


# ----------------------------------------------------------------------------- #9/#21 duplicates & precedence
def test_same_quarter_two_sources_counted_once_filing_wins():
    pts = [dp("revenue", v, e, "Q", "2026-08-01") for v, e in ((1500, "2025-09-27"), (1600, "2025-12-27"),
                                                                (1700, "2026-03-28"), (2050, "2026-06-27"))]
    pts.append(dp("revenue", 1999, "2026-06-30", "Q", "2026-08-01", ver=Verification.SEARCH_SUMMARY, src="web_research_seed"))
    f = compute_fundamentals("X", pts, as_of=date(2026, 9, 1))
    assert f.v("revenue_ttm") == 6850
    assert "sec_edgar" in f.metrics["revenue_ttm"].inputs[-1]


# ----------------------------------------------------------------------------- #10/#26 synthetic contamination
def test_any_synthetic_point_triggers_k4():
    pts = [dp("revenue", 10, "2026-06-30", "Q", "2026-08-01", ver=Verification.SYNTHETIC),
           dp("cash", 5, "2026-06-30", "instant", "2026-08-01")]
    f = compute_fundamentals("X", pts, as_of=date(2026, 9, 1))
    assert f.has_synthetic and f.data_quality == "synthetic"
    kills, _ = kill_switches(f, None, None, {}, None, False)
    assert any(k.startswith("K4") for k in kills)


# ----------------------------------------------------------------------------- #13/#15/#23 SEC vintages
def test_concept_switch_keeps_first_filing_and_restatement_is_vintage(session):
    raw = sec({"RevenueFromContractWithCustomerExcludingAssessedTax": [fact(100, "2023-01-01", "2023-12-31", "2024-02-20", "10-K")],
               "Revenues": [fact(90, "2023-01-01", "2023-12-31", "2025-02-20", "10-K", frame="CY2023"),
                            fact(120, "2024-01-01", "2024-12-31", "2025-02-20", "10-K", frame="CY2024")]})
    pts = parse_companyfacts(raw, "X", cik=1)
    fy23 = [p for p in pts if p.period_end == date(2023, 12, 31)]
    assert [(p.value, p.known_at) for p in fy23] == [(100, date(2024, 2, 20)), (90, date(2025, 2, 20))]
    assert fy23[1].fiscal_label == "CY2023" and fy23[0].fiscal_label.startswith("FY al 2023-12-31")
    repo.upsert_datapoints(session, pts)
    from qa.feed.events import detect_restatements
    assert detect_restatements(session, date(2025, 1, 1)) == 1
    assert detect_restatements(session, date(2025, 1, 1)) == 0          # sin duplicados


# ----------------------------------------------------------------------------- #14 look-ahead in pack
def test_company_pack_respects_decision_date(session):
    from qa.funnel.pack import company_pack
    from qa.bottlenecks import load_bottlenecks
    from qa.map.model import load_map
    m = load_map()
    c = Company(ticker="AXTI", name="x", exchange="NASDAQ", country="US", currency="USD", nodes=["compound_substrates"],
                exposure=Exposure())
    session.add(MarketSnapshot(company_id="AXTI", as_of=datetime(2026, 9, 1, tzinfo=timezone.utc), price=30, currency="USD",
                               shares_outstanding=1, market_cap=30, market_cap_usd=30, adv_usd_20d=1, source="t"))
    session.add(FeedEvent(event_date=date(2026, 10, 1), company_id="AXTI", event_type="news", title="futuro",
                          verification="media_report"))
    session.flush()
    pack = company_pack(session, c, m, load_bottlenecks(m), date(2025, 6, 30))
    assert pack["market"] is None and pack["recent_events"] == [] and "web_research" not in pack


# ----------------------------------------------------------------------------- #16/#27/#43/#45 reuse
def test_reuse_across_days_and_reuse_rows_recorded(session):
    from qa.funnel.pack import hash_view
    llm = ScriptedLLM(scripted_responses())
    pack = {"company": {"id": "TEST_ALPHA"}, "_meta": {"decision_date": "2026-10-20"}, "fundamentals": {"as_of": "2026-10-20"},
            "l0": {"mcap_usd": 1.0e9, "adv_usd": 1e7, "prescore": 5.1}, "market": {"price": 100.0}}
    ctx1 = RunContext(session=session, llm=llm, budget=Budget(1, 10), decision_date=date(2026, 10, 20))
    run_agent(ctx1, "triage", "TEST_ALPHA", "t", pack, hash_view(pack))
    pack2 = {**pack, "_meta": {"decision_date": "2026-10-21"}, "fundamentals": {"as_of": "2026-10-21"},
             "l0": {"mcap_usd": 1.001e9, "adv_usd": 1.1e7, "prescore": 5.2}, "market": {"price": 101.0}}
    ctx2 = RunContext(session=session, llm=llm, budget=Budget(1, 10), decision_date=date(2026, 10, 21))
    r2 = run_agent(ctx2, "triage", "TEST_ALPHA", "t", pack2, hash_view(pack2))
    assert r2.status == "reused" and llm.calls == ["triage"]
    statuses = [r.status for r in session.query(AgentRun).all()]
    assert statuses.count("reused") == 1
    assert hash_view({"market": {"price": 100.0}})["price_bucket"] != hash_view({"market": {"price": 100.0}}, move_pct=5)["price_bucket"]


# ----------------------------------------------------------------------------- #17/#19 manual CSV
def test_manual_csv_rejects_rows_individually(tmp_path):
    from qa.providers.manual import ManualCsvProvider
    hdr = "entity,metric,value,unit,currency,period_end,period_type,fiscal_label,source_name,source_url,document_type,known_at\n"
    rows = ["ERO,revenue,100,USD,USD,2026-06-30,Q,,ir,https://x,pr,2026-07-30",
            "ERO,revenue,\"1,234\",USD,USD,2026-03-31,Q,,ir,https://x,pr,2026-04-30",
            "ERO,revenue,5,USD,,2025-12-31,Q,,ir,https://x,pr,2026-01-30",
            "ERO,revenue,5,USD,USD,2025-09-30,quarter,,ir,https://x,pr,2025-10-30",
            "ERO,revenue,5,USD,USD,2025-06-30,Q,,ir,https://x,pr,2025-06-01"]
    (tmp_path / "ero.csv").write_text(hdr + "\n".join(rows) + "\n", encoding="utf-8")
    prov = ManualCsvProvider(tmp_path)
    other = Company(ticker="AAOI", name="x", exchange="NASDAQ", country="US", currency="USD", nodes=["gpu"], exposure=Exposure())
    assert prov.supports(other) is False                       # no aborta para otras empresas
    ero = Company(ticker="ERO", name="x", exchange="NYSE", country="CA", currency="USD", nodes=["copper"], exposure=Exposure())
    assert len(prov.get_facts(ero)) == 1 and len(prov.rejected) == 4


# ----------------------------------------------------------------------------- #18 price move dedupe
def test_price_move_event_not_duplicated(session):
    from qa.feed.events import detect_price_moves
    now = datetime.now(timezone.utc)
    for d, px in ((now - timedelta(days=40), 100), (now, 125)):
        session.add(MarketSnapshot(company_id="X", as_of=d, price=px, currency="USD", source="t"))
    session.flush()
    assert [detect_price_moves(session, 15) for _ in range(3)] == [1, 0, 0]


# ----------------------------------------------------------------------------- #20 seed import
def test_seed_import_point_in_time_and_types(tmp_path):
    import json
    from qa.providers.manual import import_seed_research
    rows = [{"ticker": "A", "latest_reported_quarter": {"period_end_date": "2026-06-30", "revenue": 10, "revenue_unit": "USD millions",
                                                         "cash_and_equivalents": 5, "total_debt": 2, "source_url": "https://x",
                                                         "source_date": "2026-08"}},
            {"ticker": "B", "latest_reported_quarter": {"period_end_date": "2026-06-30", "revenue": 10, "revenue_unit": "USD millions",
                                                         "source_url": "https://x", "source_date": None}}]
    p = tmp_path / "s.json"
    p.write_text(json.dumps(rows), encoding="utf-8")
    pts = import_seed_research(p)
    assert {x.entity for x in pts} == {"A"}
    by = {x.metric: x for x in pts}
    assert by["cash"].period_type == "instant" and by["debt_total"].period_type == "instant"
    assert by["revenue"].known_at == date(2026, 8, 31)


# ----------------------------------------------------------------------------- #22 GBp
def test_lse_history_scaled_from_pence(monkeypatch):
    from qa.providers import yahoo
    df = pd.DataFrame({"Open": [50.0], "High": [51.0], "Low": [49.0], "Close": [50.0], "Volume": [1000]},
                      index=pd.to_datetime(["2026-10-20"]))
    fake = NS(Ticker=lambda s: NS(history=lambda **k: df.copy(), fast_info={"currency": "GBp"}))
    monkeypatch.setattr(yahoo, "_yf", lambda: fake)
    out = yahoo.YahooProvider().history("IQE.L", start=date(2026, 10, 1))
    assert out["close"].iloc[0] == pytest.approx(0.5) and out.attrs["currency"] == "GBP"


# ----------------------------------------------------------------------------- #24/#36/#25/#35 committee
def _demo_ctx(session, responses=None):
    return RunContext(session=session, llm=ScriptedLLM(responses or scripted_responses()), budget=Budget(5, 50), reuse=False)


def _demo_setup(session):
    import json as _json
    from qa.bottlenecks import load_bottlenecks
    from qa.config import FIXTURES_DIR
    from qa.demo import SYN
    from qa.map.model import load_map
    from qa.providers.base import MarketSnapshotData
    from qa.providers.manual import FixtureProvider
    from qa.universe.model import Universe
    m = load_map()
    fx = _json.loads((FIXTURES_DIR / "synthetic_companies.json").read_text(encoding="utf-8"))
    comps = {cid: Company(ticker=cid, name=cid, exchange="NASDAQ", country="US", currency="USD", nodes=nodes,
                          exposure=Exposure(**expo), status=status, tags=["synthetic"]) for cid, (nodes, expo, status) in SYN.items()}
    u = Universe(version="t", companies=comps)
    repo.sync_map(session, m)
    repo.sync_universe(session, u, m)
    for c in comps.values():
        repo.upsert_datapoints(session, FixtureProvider().get_facts(c))
        mk = fx["market"][c.id]
        repo.save_snapshot(session, MarketSnapshotData(c.id, datetime.now(timezone.utc), mk["price"], "USD", mk["shares"],
                                                       mk["price"] * mk["shares"], mk["price"] * mk["shares"], mk["adv_usd_20d"], "t"))
    session.flush()
    return m, u, load_bottlenecks(m, u)


def test_only_run_never_sells_unanalyzed_holdings(session, tmp_path):
    from qa.funnel.pipeline import Pipeline
    from qa.portfolio import ledger
    m, u, reg = _demo_setup(session)
    engine.ALLOW_SYNTHETIC_FOR_DEMO = True
    try:
        Pipeline(_demo_ctx(session), m, u, reg, portfolio_name="P", mode="test", runs_dir=tmp_path).run()
        held = ledger.holdings(session, "P")
        assert held
        rep = Pipeline(_demo_ctx(session), m, u, reg, portfolio_name="P", mode="test", runs_dir=tmp_path).run(only=["TEST_ALPHA"])
    finally:
        engine.ALLOW_SYNTHETIC_FOR_DEMO = False
    after = ledger.holdings(session, "P")
    for k in held:
        assert after.get(k, 0) > 0, f"{k} fue vendida sin análisis"
    assert any("se agregaron las posiciones" in n for n in rep.notes)


def test_failed_fundamental_blocks_before_committee(session, tmp_path):
    from qa.funnel.pipeline import Pipeline
    m, u, reg = _demo_setup(session)
    resp = scripted_responses()
    resp["fundamental"] = {"broken": True}                     # salida inválida -> status error
    engine.ALLOW_SYNTHETIC_FOR_DEMO = True
    try:
        rep = Pipeline(_demo_ctx(session, resp), m, u, reg, portfolio_name="P2", mode="test", runs_dir=tmp_path).run()
    finally:
        engine.ALLOW_SYNTHETIC_FOR_DEMO = False
    scored = [s for s in rep.scores if s["tier"] in ("L2", "L3")]
    assert scored and all(any(k.startswith("K9") for k in s["kill_switches"]) and not s["eligible"] for s in scored)
    assert rep.trades == []


def test_failed_auditor_blocks_and_final_action_recorded(session, tmp_path):
    from qa.funnel.pipeline import Pipeline
    m, u, reg = _demo_setup(session)
    resp = scripted_responses()
    resp["auditor"] = {"broken": True}
    engine.ALLOW_SYNTHETIC_FOR_DEMO = True
    try:
        rep = Pipeline(_demo_ctx(session, resp), m, u, reg, portfolio_name="P2b", mode="test", runs_dir=tmp_path).run()
    finally:
        engine.ALLOW_SYNTHETIC_FOR_DEMO = False
    l3 = [s for s in rep.scores if s["tier"] == "L3"]
    assert l3 and all(any("Research Auditor" in k for k in s["kill_switches"]) for s in l3)
    assert rep.trades == []
    recs = session.query(Recommendation).all()
    buy = [r for r in recs if r.pm_action == "BUY"]
    assert buy and all(r.action == "AVOID" and r.target_weight == 0 for r in buy)


# ----------------------------------------------------------------------------- #31/#34 rebuttal provenance
def test_invalid_rebuttal_keeps_l2_provenance_and_fresh_checks(session, tmp_path):
    from qa.funnel.pipeline import Pipeline
    m, u, reg = _demo_setup(session)
    resp = scripted_responses()
    base_val = resp["valuation"]
    resp["valuation_rebuttal"] = lambda p: {**base_val(p), "scenarios": [dict(x, name="base") for x in base_val(p)["scenarios"]]}
    engine.ALLOW_SYNTHETIC_FOR_DEMO = True
    try:
        Pipeline(_demo_ctx(session, resp), m, u, reg, portfolio_name="P3", mode="test", runs_dir=tmp_path).run(
            tiers=("L0", "L1", "L2", "L3"))
    finally:
        engine.ALLOW_SYNTHETIC_FOR_DEMO = False
    rows = session.query(ScRow).all()
    assert rows and all(r.author == "valuation" for r in rows)
    for r in rows:
        names = sorted(x["name"] for x in r.assumptions["scenarios"])
        assert names == ["base", "bear", "bull"]


# ----------------------------------------------------------------------------- #32/#38 dry run
def test_dry_run_estimates_all_layers(session, tmp_path):
    from qa.funnel.pipeline import Pipeline
    m, u, reg = _demo_setup(session)
    ctx = RunContext(session=session, llm=None, budget=Budget(5, 50), dry_run=True)
    rep = Pipeline(ctx, m, u, reg, portfolio_name="P4", mode="test", runs_dir=tmp_path).run()
    agents = {x["agent"] for x in rep.agent_log}
    assert {"triage", "fundamental", "valuation", "bear", "auditor", "portfolio_manager"} <= agents
    assert rep.est_cost_usd > 0.1 and session.query(AgentRun).count() == 0


# ----------------------------------------------------------------------------- #28/#29 failed-call costs & commit
class _FailingLLM:
    def complete(self, **kw):
        raise LLMError("max_tokens", cost_usd=0.8, input_tokens=20000, output_tokens=32000)


def test_billed_failures_count_and_persist(db_url):
    from qa.db.session import session_scope
    with pytest.raises(RuntimeError):
        with session_scope(db_url) as s:
            ctx = RunContext(session=s, llm=_FailingLLM(), budget=Budget(5, 50), reuse=False)
            r = run_agent(ctx, "auditor", "X", "t", {"a": 1})
            assert r.status == "error" and ctx.budget.spent_run == pytest.approx(0.8)
            raise RuntimeError("crash después de gastar")
    with session_scope(db_url) as s:
        assert Budget.from_settings(s).spent_month_before == pytest.approx(0.8)


# ----------------------------------------------------------------------------- #30/#39 proposals validation
def test_bad_proposals_are_rejected_without_writing(session, tmp_path, monkeypatch):
    from qa.agents import map_agents
    monkeypatch.setattr(map_agents, "DISCOVERED", tmp_path / "discovered.yaml")
    monkeypatch.setattr(map_agents, "MAP_OVERLAY", tmp_path / "overlay.yaml")
    bad = [MapChangeProposal(change_type="add_company", payload={"ticker": "AXTI", "name": "dup", "exchange": "NASDAQ",
                                                                  "map_nodes": ["compound_substrates"]}),
           MapChangeProposal(change_type="add_company", payload={"ticker": "ZZZQ", "name": "typo", "exchange": "NASDAQ",
                                                                  "map_nodes": ["eml_laser"]}),
           MapChangeProposal(change_type="add_node", payload={"raw": "not json"}),
           MapChangeProposal(change_type="add_edge", payload={"source": "hyperscaler_capex", "target": "copper"})]
    for p in bad:
        session.add(p)
    session.flush()
    for p in bad:
        with pytest.raises(ValueError):
            map_agents.review_proposal(session, p.id, approve=True, reviewer="t")
        assert p.status == "pending"
    assert not (tmp_path / "discovered.yaml").exists() and not (tmp_path / "overlay.yaml").exists()


# ----------------------------------------------------------------------------- #33 pricing
def test_cache_writes_and_fallback_iterations_priced():
    u = NS(input_tokens=1000, output_tokens=100, cache_creation_input_tokens=50000, cache_read_input_tokens=0,
           server_tool_use=NS(web_search_requests=2), iterations=None)
    c, *_ = usage_cost(u, "claude-opus-5-5")
    assert c == pytest.approx(price_tokens("claude-opus-5-5", 1000, 100, 2, 0, 50000))
    assert c > price_tokens("claude-opus-5-5", 1000, 100, 2)
    it = NS(input_tokens=1000, output_tokens=100, cache_read_input_tokens=0, cache_creation_input_tokens=0,
            server_tool_use=None, iterations=[NS(model="claude-opus-5-5", input_tokens=500, output_tokens=0),
                                              NS(model="claude-opus-4-8", input_tokens=1000, output_tokens=100)])
    c2, ti, to, _ = usage_cost(it, "claude-opus-5-5")
    assert c2 == pytest.approx(price_tokens("claude-opus-5-5", 500, 0) + price_tokens("claude-opus-4-8", 1000, 100))
    assert ti == 1500


# ----------------------------------------------------------------------------- #37 qa portfolio
def test_cli_portfolio_with_snapshots(db_url, monkeypatch, capsys):
    from qa import cli, config
    from qa.db.schema import PortfolioSnapshot
    from qa.db.session import session_scope
    monkeypatch.setattr(config, "database_url", lambda: db_url)
    import qa.db.session as sess
    monkeypatch.setattr(sess, "database_url", lambda: db_url)
    with session_scope(db_url) as s:
        s.add(PortfolioSnapshot(portfolio=config.settings()["portfolio"]["name"], date=date(2026, 10, 1), nav=100000,
                                cash=100000, positions={}))
    cli.cmd_portfolio(NS())
    assert "SIMULADA" in capsys.readouterr().out


# ----------------------------------------------------------------------------- #41 options: no rescale by spot
def test_option_targets_not_rescaled_by_spot():
    from qa.options.analyzer import interpolate_target
    assert interpolate_target(20, {12: 30, 24: 36, 36: 40}, 6 + 6) == pytest.approx(30)


# ----------------------------------------------------------------------------- #46 stronger tests
def test_bottleneck_cap_binds():
    meta = {k: PositionMeta(k, layer=f"L{k}", primary_bottleneck="B", eligible=True) for k in "ABCD"}
    cfg = {"max_weight": 0.2, "max_positions": 15, "min_positions": 1, "max_weight_per_layer": 1.0,
           "max_weight_per_bottleneck": 0.25, "min_cash": 0.0}
    w, _ = enforce_constraints({k: 0.2 for k in "ABCD"}, meta, cfg)
    assert sum(w.values()) == pytest.approx(0.25, abs=1e-3)


def test_valuation_rule_numeric_anchors():
    def ss(er36, bear, bull, pperm=0.0):
        res = [ScenarioResult(name=n, probability=1 / 3, years=[], price_target={}, returns={36: v}, financing_need=0,
                              dilution_from_financing=0) for n, v in (("bear", bear), ("base", 0), ("bull", bull))]
        return ScenarioSet(base_revenue=1, price0=1, results=res, expected_return={12: 0, 24: 0, 36: er36}, prob_loss={},
                           prob_permanent_loss=pperm, bear_downside_36m=bear, upside_downside_ratio=bull / -bear)
    ten = (1.10 ** 3) - 1
    assert score_valuation(ss(ten, -0.25, 0.25)).score == pytest.approx(0.6 * 5 + 0.4 * 2.5, abs=0.01)
    assert score_valuation(ss(1.35 ** 3 - 1, -0.25, 1.0)).score == pytest.approx(10, abs=0.01)


def test_financial_quality_coverage_penalty_same_company():
    full = [dp("revenue", 100, e, "Q", "2026-08-01") for e in ("2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30")]
    full += [dp("gross_profit", 50, e, "Q", "2026-08-01") for e in ("2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30")]
    rich = full + [dp("ocf", 20, e, "Q", "2026-08-01") for e in ("2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30")] + \
        [dp("capex", 5, e, "Q", "2026-08-01") for e in ("2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30")] + \
        [dp("cash", 100, "2026-06-30", "instant", "2026-08-01")]
    s_poor = score_financial_quality(compute_fundamentals("X", full, as_of=date(2026, 9, 1)))
    s_rich = score_financial_quality(compute_fundamentals("X", rich, as_of=date(2026, 9, 1)))
    assert s_poor.score < s_rich.score and s_poor.missing


def test_compute_fundamentals_excludes_unknown_known_at():
    p = DataPoint(entity="X", metric="cash", value=5e8, unit="USD", currency="USD", period_end=date(2023, 12, 31),
                  period_type="instant", source=Source(name="t"), known_at=None)
    assert compute_fundamentals("X", [p], as_of=date(2024, 1, 1)).v("cash") is None


# ----------------------------------------------------------------------------- #47 demo reports
def test_demo_reports_not_in_real_runs(db_url):
    from qa.config import DATA_DIR
    from qa.demo import run_demo
    before = set((DATA_DIR / "runs").glob("*.json")) if (DATA_DIR / "runs").exists() else set()
    r = run_demo(db_url)
    after = set((DATA_DIR / "runs").glob("*.json")) if (DATA_DIR / "runs").exists() else set()
    assert after == before and r["mode"] == "demo_synthetic"


def test_missing_factor_applies_cap():
    f = {k: FactorScore(score=9, rationale="t") for k in engine.FACTORS if k != "economic_capture"}
    sc = combine("X", f)
    assert sc.total == 5.0 and any("no evaluado" in c for c in sc.caps_applied)
