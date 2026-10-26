"""Regresiones de la ronda 3 de verificación adversarial. Cada test reproduce el escenario de falla reportado."""
from __future__ import annotations

import copy
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest
from test_review_regressions import _demo_ctx, _demo_setup, dp, fact, sec

from qa.agents.base import Budget, RunContext, run_agent
from qa.agents.llm import AnthropicLLM, LLMError, ScriptedLLM
from qa.db import repo
from qa.db.schema import AgentRun, FeedEvent, FunnelState, MarketSnapshot, PaperTrade, PipelineRun, Recommendation
from qa.demo import scripted_responses
from qa.fundamentals.metrics import Fundamentals, Metric, compute_fundamentals
from qa.funnel.pipeline import Pipeline, _final_action
from qa.portfolio import ledger
from qa.portfolio.construction import PositionMeta, enforce_constraints
from qa.provenance import Verification
from qa.providers.sec_edgar import parse_companyfacts
from qa.scoring import engine
from qa.scoring.engine import kill_switches, score_financial_quality, score_valuation
from qa.universe.model import Company, Exposure
from qa.valuation.core import balance_inputs

SEED = dict(ver=Verification.SEARCH_SUMMARY, src="web_research_seed")


# ============================================================================ balance
def test_r3_dei_cover_date_shares_do_not_anchor_balance():
    """dei:EntityCommonStockSharesOutstanding trae la fecha de PORTADA (semanas después del cierre)."""
    qs = [("2025-04-01", "2025-06-30", "2025-08-05", "2025-07-29"), ("2025-07-01", "2025-09-30", "2025-11-04", "2025-10-28"),
          ("2025-10-01", "2025-12-31", "2026-02-20", "2026-02-13"), ("2026-01-01", "2026-03-31", "2026-05-05", "2026-04-28")]
    raw = sec({"Revenues": [fact(250e6, s, e, f) for s, e, f, _ in qs],
               "OperatingIncomeLoss": [fact(25e6, s, e, f) for s, e, f, _ in qs],
               "DepreciationDepletionAndAmortization": [fact(5e6, s, e, f) for s, e, f, _ in qs],
               "CashAndCashEquivalentsAtCarryingValue": [fact(100e6, None, e, f) for _, e, f, _ in qs],
               "LongTermDebt": [fact(2000e6, None, e, f) for _, e, f, _ in qs]})
    raw["facts"]["dei"] = {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
        {"val": 100e6, "end": cov, "filed": f, "form": "10-Q", "accn": "0001-" + f} for _, _, f, cov in qs]}}}
    f = compute_fundamentals("X", parse_companyfacts(raw, "X", cik=1), as_of=date(2026, 6, 1))
    assert f.balance_date == date(2026, 3, 31) and f.v("debt") == 2000e6 and not f.debt_elsewhere
    assert f.metrics["shares_outstanding"].period == "2026-04-28"           # la portada se lee aparte
    kills, _ = kill_switches(f, None, None, {}, None, False)
    assert kills == ["K7: deuda neta/EBITDA 15.8x > 6x"]


def test_r3_newer_complete_lower_precedence_balance_replaces_stale_filing():
    pts = [dp("cash", 500, "2023-12-31", "instant", "2024-02-15"), dp("debt_total", 100, "2023-12-31", "instant", "2024-02-15"),
           dp("cash", 50, "2026-06-30", "instant", "2026-08-06", **SEED),
           dp("debt_total", 3000, "2026-06-30", "instant", "2026-08-06", **SEED)]
    f = compute_fundamentals("X", pts, as_of=date(2026, 9, 1))
    assert f.balance_date == date(2026, 6, 30) and f.v("debt") == 3000 and f.v("cash_total") == 50
    assert balance_inputs(f)[:2] == (50, 3000)


def test_r3_hard_stale_balance_blocks_leverage_and_valuation():
    pts = [dp("cash", 500, "2023-12-31", "instant", "2024-02-15"), dp("debt_total", 100, "2023-12-31", "instant", "2024-02-15")]
    f = compute_fundamentals("X", pts, as_of=date(2026, 9, 1))
    assert f.balance_stale
    kills, _ = kill_switches(f, None, None, {}, None, False)
    assert any("balance demasiado antiguo" in k for k in kills)
    with pytest.raises(ValueError, match="demasiado antiguo"):
        balance_inputs(f)
    soft = compute_fundamentals("X", pts, as_of=date(2024, 9, 1))     # 245 días: solo advertencia, sin días exactos
    assert not soft.balance_stale and "balance desactualizado: 2023-12-31 (> 200 días)" in soft.warnings
    assert compute_fundamentals("X", pts, as_of=date(2024, 9, 2)).warnings == soft.warnings


def test_r3_debt_elsewhere_only_for_recent_nonzero_debt():
    cash = [dp("cash", 900, "2026-06-30", "instant", "2026-08-01")]
    old = compute_fundamentals("X", cash + [dp("debt_noncurrent", 300, "2014-12-31", "instant", "2015-02-15")],
                               as_of=date(2026, 9, 1))
    zero = compute_fundamentals("X", cash + [dp("debt_total", 0, "2025-12-31", "instant", "2026-02-15")],
                                as_of=date(2026, 9, 1))
    for f in (old, zero):
        assert not f.debt_elsewhere and not kill_switches(f, None, None, {}, None, False)[0]
        assert "SUPONE 0" in balance_inputs(f)[2][0]
    recent = compute_fundamentals("X", cash + [dp("debt_total", 400, "2026-03-31", "instant", "2026-05-01")],
                                  as_of=date(2026, 9, 1))
    assert recent.debt_elsewhere
    # semilla con deuda 0 (FN) + balance SEC posterior sin etiquetas de deuda: se supone 0 con alerta, no se bloquea
    fn = compute_fundamentals("X", [dp("debt_total", 0, "2026-06-26", "instant", "2026-08-17", **SEED),
                                    dp("cash", 900, "2026-09-26", "instant", "2026-11-04")], as_of=date(2026, 11, 10))
    assert not fn.debt_elsewhere and "SUPONE 0" in balance_inputs(fn)[2][0]


def test_r3_roic_not_meaningful_is_not_a_missing_metric():
    pts = [dp("operating_income", 50, f"2026-{m:02d}-{d}", "Q", "2026-10-01") for m, d in ((3, 31), (6, 30), (9, 30))]
    pts += [dp("operating_income", 50, "2025-12-31", "Q", "2026-02-01"), dp("cash", 900, "2026-09-30", "instant", "2026-10-01"),
            dp("equity", 100, "2026-09-30", "instant", "2026-10-01"), dp("debt_total", 0, "2026-09-30", "instant", "2026-10-01")]
    f = compute_fundamentals("X", pts, as_of=date(2026, 10, 15))
    assert f.v("roic") is None and f.metrics["roic"].note.startswith("n.m.")
    assert "roic=10.0" in score_financial_quality(f).rationale


# ============================================================================ trimestres / TTM
def test_r3_fy_minus_quarters_uses_consistent_vintages_and_precedence():
    R1 = "RevenueFromContractWithCustomerExcludingAssessedTax"
    old = [fact(25, "2023-01-01", "2023-03-31", "2023-05-01", accn="a"), fact(25, "2023-04-01", "2023-06-30", "2023-08-01", accn="b"),
           fact(25, "2023-07-01", "2023-09-30", "2023-11-01", accn="c"), fact(100, "2023-01-01", "2023-12-31", "2024-02-20", "10-K", accn="d")]
    raw = sec({R1: old, "Revenues": [fact(90, "2023-01-01", "2023-12-31", "2025-02-20", "10-K", accn="e")]})
    f = compute_fundamentals("X", parse_companyfacts(raw, "X", cik=1), as_of=date(2025, 6, 30))
    assert f.quarterly["revenue"][date(2023, 12, 31)] == 25                 # nunca un trimestre de 15 inventado
    # Q1-Q3 de buscador + Q4 de la empresa + FY SEC: el Q4 directo de mejor fuente no se reemplaza por uno peor
    pts = [dp("revenue", 1000, e, "Q", k, **SEED) for e, k in (("2025-09-26", "2025-11-04"), ("2025-12-26", "2026-02-03"),
                                                              ("2026-03-27", "2026-05-05"))]
    pts += [dp("revenue", 1500, "2026-06-26", "Q", "2026-08-01", ver=Verification.COMPANY_STATEMENT, src="company_ir"),
            dp("revenue", 4000, "2026-06-26", "FY", "2026-08-20")]
    g = compute_fundamentals("X", pts, as_of=date(2026, 9, 1))
    assert g.quarterly["revenue"][date(2026, 6, 26)] == 1500
    # emisor anual (solo FY) + trimestre de semilla: sin derivación posible, el trimestre directo se conserva
    h = compute_fundamentals("X", [dp("revenue", 4700, "2026-06-30", "FY", "2026-09-20"),
                                   dp("revenue", 1315.8, "2026-06-30", "Q", "2026-08-17", **SEED)], as_of=date(2026, 10, 1))
    assert h.v("revenue_last_q") == 1315.8 and h.v("revenue_ttm") == 4700


def test_r3_later_fy_replaces_older_quarterly_ttm():
    R1 = "RevenueFromContractWithCustomerExcludingAssessedTax"
    raw = sec({R1: [fact(25, "2023-01-01", "2023-03-31", "2023-05-01", accn="a"), fact(25, "2023-04-01", "2023-06-30", "2023-08-01", accn="b"),
                    fact(25, "2023-07-01", "2023-09-30", "2023-11-01", accn="c"), fact(75, "2023-01-01", "2023-09-30", "2023-11-01", accn="c"),
                    fact(100, "2023-01-01", "2023-12-31", "2024-02-20", "10-K", accn="d")],
               "Revenues": [fact(120, "2024-01-01", "2024-12-31", "2025-02-20", "10-K", accn="e")]})
    f = compute_fundamentals("X", parse_companyfacts(raw, "X", cik=1), as_of=date(2025, 6, 30))
    assert f.v("revenue_ttm") == 120 and f.metrics["revenue_ttm"].period == "FY a 2024-12-31"
    assert f.v("revenue_growth_yoy") == pytest.approx(0.2)             # FY 2024 vs TTM 2023 (misma fecha -1 año)


# ============================================================================ scoring (mutantes sobrevivientes)
def _ss(er36, ud, bear36, pperm):
    return NS(expected_return={36: er36}, upside_downside_ratio=ud, bear_downside_36m=bear36,
              prob_permanent_loss=pperm, warnings=[])


def test_r3_valuation_rule_anchors_and_penalty():
    ann = lambda r: (1 + r) ** 3 - 1                                       # noqa: E731
    assert score_valuation(_ss(ann(0.10), 4, -0.2, 0.0)).score == pytest.approx(0.6 * 5 + 0.4 * 10)
    assert score_valuation(_ss(ann(0.20), 2, -0.2, 0.0)).score == pytest.approx(0.6 * 7 + 0.4 * 5)   # pendiente 20
    assert score_valuation(_ss(ann(0.20), 2, -0.2, 0.4)).score == pytest.approx(0.6 * 7 + 0.4 * 5 - 2)
    assert score_valuation(_ss(ann(0.10), None, 0.05, 0.0)).score == pytest.approx(0.6 * 5 + 0.4 * 10)
    assert score_valuation(_ss(ann(0.10), None, -0.05, 0.0)).score == pytest.approx(0.6 * 5)


def _fq(**vals):
    f = Fundamentals(entity="X", as_of=date(2026, 1, 1), currency="USD")
    f.metrics = {k: Metric(v) for k, v in vals.items()}
    return score_financial_quality(f).score


def test_r3_missing_core_metric_counts_as_weak_not_neutral():
    """Ronda 4: un faltante núcleo cuenta 2.5 (débil): se penaliza frente a ignorarlo, pero un hueco estructural de
    cobertura no hunde el factor por sí solo (antes contaba 0 y disparaba el tope FQ<3 en emisores IFRS)."""
    burner = dict(gross_margin=0.6, fcf_margin=-0.3, roic=-0.2, net_debt=-500, net_debt_to_ebitda=None,
                  cash_runway_quarters=20)
    without = _fq(**burner)
    assert without == pytest.approx((10 + 0 + 0 + 10 + 10 + engine.FQ_MISSING) / 6, abs=0.01)
    assert without < (10 + 0 + 0 + 10 + 10) / 5                           # peor que ignorar el faltante
    twin = dict(gross_margin=0.6, fcf_margin=0.2, roic=0.3, net_debt=-500, net_debt_to_ebitda=None)
    assert _fq(**twin) == pytest.approx((40 + engine.FQ_MISSING) / 5, abs=0.01)
    assert _fq(**twin, dilution_yoy=0.0) > _fq(**twin)                     # el dato bueno mejora
    no_runway = dict(burner, cash_runway_quarters=None)
    assert _fq(**burner) != _fq(**no_runway)                              # el runway cuenta para quien quema caja


def test_r3_layer_and_bottleneck_caps_and_notes():
    cfg = {"max_weight": 0.10, "max_positions": 15, "min_positions": 1, "max_weight_per_layer": 0.40,
           "max_weight_per_bottleneck": 0.25, "min_cash": 0.02}
    meta = {k: PositionMeta(k, layer="L", primary_bottleneck=f"b{k}", eligible=True) for k in "ABCDE"}
    notes_by: dict = {}
    w, _ = enforce_constraints({k: 0.10 for k in "ABCDE"}, meta, cfg, notes_by=notes_by)
    assert sum(w.values()) == pytest.approx(0.40, abs=1e-3)
    assert all(any(n.startswith("layer=L") for n in notes_by[k]) for k in "ABCDE")
    meta = {k: PositionMeta(k, layer=f"l{k}", primary_bottleneck="B", eligible=True) for k in "ABCD"}
    w, _ = enforce_constraints({k: 0.10 for k in "ABCD"}, meta, cfg)
    assert sum(w.values()) == pytest.approx(0.25, abs=1e-3)


# ============================================================================ bottlenecks point-in-time
def test_r3_bottleneck_registry_point_in_time():
    from qa.bottlenecks import default_bottlenecks, evidence_date, registry_as_of
    from qa.map.model import default_map
    assert evidence_date({"date": "2026-10-16/19"}) == date(2026, 10, 19)
    assert evidence_date({"date": "2026-08/09 (approx.)"}) == date(2026, 9, 30)
    assert evidence_date({"date": "2025-2026 (exact date not captured)"}) == date(2026, 12, 31)
    assert evidence_date({"date": "2026-13"}) is None
    reg, m = default_bottlenecks(), default_map()
    live = registry_as_of(reg, m, date(2026, 12, 1))
    assert all(live[k] is reg[k] for k in reg)
    past = registry_as_of(reg, m, date(2026, 6, 30))
    for b in past.values():
        for e in b.raw.get("evidence", []) + b.raw.get("counter_evidence", []):
            assert (evidence_date(e) or date.max) <= date(2026, 6, 30)
        if "pit_note" in b.raw:
            assert "supply_limit" not in b.raw and "estimated_duration" not in b.raw


# ============================================================================ reutilización / hash / reloj
def test_r3_reuse_only_from_same_or_earlier_decision_date(session, tmp_path):
    m, u, reg = _demo_setup(session)
    engine.ALLOW_SYNTHETIC_FOR_DEMO = True
    today = datetime.now(timezone.utc).date()
    try:
        for dd in (today + timedelta(days=5), today + timedelta(days=1)):
            ctx = RunContext(session=session, llm=ScriptedLLM(scripted_responses()), budget=Budget(5, 50),
                             decision_date=dd, reuse=True)
            Pipeline(ctx, m, u, reg, portfolio_name="R", mode="test", runs_dir=tmp_path).run(tiers=("L0", "L1", "L2"))
        later = ScriptedLLM(scripted_responses())
        ctx = RunContext(session=session, llm=later, budget=Budget(5, 50), decision_date=today + timedelta(days=2), reuse=True)
        Pipeline(ctx, m, u, reg, portfolio_name="R", mode="test", runs_dir=tmp_path).run(tiers=("L0", "L1", "L2"))
    finally:
        engine.ALLOW_SYNTHETIC_FOR_DEMO = False
    rows = session.query(AgentRun).all()
    by_id = {r.id: r for r in rows}
    for r in rows:
        if r.status == "reused":
            assert by_id[r.reused_from].decision_time <= r.decision_time
    assert later.calls == []                    # día +2 reutiliza lo del día +1 (mismos datos, sin cambios)


def test_r3_default_decision_date_is_utc_and_trigger_validated():
    from qa.config import _validate
    ctx = RunContext(session=None, llm=None, budget=Budget(1, 1))
    assert ctx.decision_date == datetime.now(timezone.utc).date()
    for bad in (0, -5, "x"):
        with pytest.raises(ValueError, match="price_move_trigger_pct"):
            _validate({"funnel": {"reuse": {"price_move_trigger_pct": bad}}})


def test_r3_map_curator_is_never_reused(session):
    from qa.agents.map_agents import run_map_curator
    from qa.bottlenecks import default_bottlenecks
    from qa.map.model import default_map
    from qa.universe.model import default_universe
    out = {"proposals": [{"change_type": "update_bottleneck_status", "payload_json": "{}", "rationale": "r",
                          "evidence": []}], "stale_items": []}
    llm = ScriptedLLM({"map_curator": out})
    for _ in range(2):
        ctx = RunContext(session=session, llm=llm, budget=Budget(5, 50), reuse=True)
        run_map_curator(ctx, default_map(), default_bottlenecks(), default_universe())
    assert llm.calls == ["map_curator", "map_curator"]


def test_r3_price_move_events_are_per_episode(session):
    from qa.feed.events import detect_price_moves
    now = datetime.now(timezone.utc)

    def snap(p, when):
        session.add(MarketSnapshot(company_id="X", as_of=when, price=p, currency="USD", source="t"))
        session.flush()
    snap(100.0, now - timedelta(days=40))
    counts = []
    for p in (120.0, 121.0, 122.0, 141.0):
        snap(p, datetime.now(timezone.utc))
        counts.append(detect_price_moves(session, threshold_pct=15))
    assert counts == [1, 0, 0, 1]                # 141: nuevo tramo >= 15% desde 120 (precio al primer evento)


def test_r3_price_moves_cannot_crowd_out_restatements(session):
    from qa.bottlenecks import default_bottlenecks
    from qa.funnel.pack import company_pack
    from qa.map.model import default_map
    c = Company(ticker="AAOI", name="x", exchange="NASDAQ", country="US", currency="USD", nodes=["optical_transceivers"],
                exposure=Exposure())
    today = datetime.now(timezone.utc).date()
    session.add(FeedEvent(event_date=today - timedelta(days=20), company_id="AAOI", event_type="restatement", title="R",
                          verification="confirmed_by_filing"))
    for i in range(12):
        session.add(FeedEvent(event_date=today - timedelta(days=i), company_id="AAOI", event_type="price_move",
                              title=f"P{i}", verification="market_data"))
    session.flush()
    p = company_pack(session, c, default_map(), default_bottlenecks(), today)
    types = [e["type"] for e in p["recent_events"]]
    assert "restatement" in types and types.count("price_move") == 2


# ============================================================================ comité
def _run(session, tmp_path, resp=None, tiers=None, pf="P", budget=None):
    if "demo" not in session.info:
        session.info["demo"] = _demo_setup(session)
    m, u, reg = session.info["demo"]
    ctx = _demo_ctx(session, resp)
    if budget is not None:
        ctx.budget = Budget(budget, 50)
    p = Pipeline(ctx, m, u, reg, portfolio_name=pf, mode="test", runs_dir=tmp_path)
    return p.run(tiers=tiers) if tiers else p.run()


def _pm(weights):
    def pm(p):  # noqa: ANN001, ANN202
        return {"decisions": [{"company_id": k, "action": a, "target_weight": w, "conviction": "medium", "thesis": "T",
                               "catalysts": [], "risks": [], "invalidation_triggers": []} for k, (a, w) in weights.items()],
                "cash_weight": 0.9, "portfolio_rationale": "x", "correlation_and_concentration_notes": "x",
                "rejected_with_reason": []}
    return pm


@pytest.fixture
def synthetic_ok():
    engine.ALLOW_SYNTHETIC_FOR_DEMO = True
    yield
    engine.ALLOW_SYNTHETIC_FOR_DEMO = False


def _holding_setup(session, tmp_path):
    """Primer ciclo: el PM compra TEST_SLOW al 8%."""
    r = scripted_responses()
    r["portfolio_manager"] = _pm({"TEST_SLOW": ("BUY", 0.08)})
    _run(session, tmp_path, r)
    held = ledger.holdings(session, "P")
    assert "TEST_SLOW" in held
    return held


@pytest.mark.parametrize("case", ["no_L3", "auditor_error", "bear_error", "budget"])
def test_r3_incomplete_analysis_never_sells_a_holding(session, tmp_path, synthetic_ok, case):
    held = _holding_setup(session, tmp_path)
    r = scripted_responses()
    r["portfolio_manager"] = _pm({"TEST_SLOW": ("ADD", 0.10)})
    kw = {}
    if case == "no_L3":
        kw["tiers"] = ("L0", "L1", "L2", "committee")
    elif case == "auditor_error":
        r["auditor"] = {"broken": True}
    elif case == "bear_error":
        r["bear"] = {"broken": True}
    else:
        kw["budget"] = 1.0              # alcanza para L1/L2 y el PM, no para la cota de L3 (Opus + 8 búsquedas)
    rep = _run(session, tmp_path, r, **kw)
    assert ledger.holdings(session, "P")["TEST_SLOW"] == pytest.approx(held["TEST_SLOW"])     # ni venta ni ADD
    assert any("análisis incompleto" in n and "sin aumentos" in n for n in rep.constraint_notes)


def test_r3_missing_scenarios_or_bear_block_entry(session, tmp_path, synthetic_ok):
    r = scripted_responses()
    r["bear"] = lambda p: {"bad": 1}
    r["portfolio_manager"] = _pm({"TEST_ALPHA": ("BUY", 0.08)})
    rep = _run(session, tmp_path, r)
    l3 = [s for s in rep.scores if s["tier"] == "L3"]
    assert l3 and all(any("sin bear case" in k for k in s["kill_switches"]) for s in l3) and rep.trades == []
    r = scripted_responses()
    val = r["valuation"]
    bad = lambda p: {**val(p), "scenarios": val(p)["scenarios"][:2]}   # noqa: E731
    r["valuation"] = r["valuation_rebuttal"] = bad
    rep = _run(session, tmp_path, r, tiers=("L0", "L1", "L2", "L3"))
    assert all(any("sin escenarios de valuación" in k for k in s["kill_switches"]) for s in rep.scores
               if s["tier"] in ("L2", "L3"))


def test_r3_pm_silent_holding_sale_has_recommendation(session, tmp_path, synthetic_ok):
    _holding_setup(session, tmp_path)
    r = scripted_responses()
    f = r["fundamental"]

    def gc(p):  # noqa: ANN001, ANN202
        out = copy.deepcopy(f)
        if (p.get("company") or {}).get("id") == "TEST_SLOW":
            out["red_flags"]["going_concern"] = True
        return out
    r["fundamental"] = gc
    r["portfolio_manager"] = _pm({"TEST_ALPHA": ("BUY", 0.05)})
    _run(session, tmp_path, r)
    sell = [t for t in session.query(PaperTrade).all() if t.symbol == "TEST_SLOW" and t.side == "SELL"]
    assert sell and all(t.recommendation_id is not None for t in sell)
    rec = session.get(Recommendation, sell[-1].recommendation_id)
    assert rec.action == "SELL" and rec.pm_action is None and any("K1" in n for n in rec.constraint_notes)


def test_r3_hold_band_equals_trade_threshold(session):
    band = ledger.min_trade_fraction()
    assert _final_action(0.083, 0.08) == "HOLD" and 0.083 - 0.08 < band
    now = datetime.now(timezone.utc)
    q = {"A": ledger.Quote(10.0, now, "t", "large")}
    ledger.rebalance(session, "H", {"A": 0.083}, q)
    assert ledger.rebalance(session, "H", {"A": 0.08}, q) == []                 # HOLD nunca opera
    assert _final_action(0.083, 0.083 - band) == "TRIM"


def test_r3_dry_run_has_no_side_effects_and_estimates_rebuttal(session, tmp_path, synthetic_ok):
    _run(session, tmp_path)
    before = (session.query(AgentRun).count(), {(x.company_id, x.tier_reached) for x in session.query(FunnelState)})
    m, u, reg = session.info["demo"]
    ctx = RunContext(session=session, llm=None, budget=Budget(5, 50), reuse=True, dry_run=True)
    rep = Pipeline(ctx, m, u, reg, portfolio_name="P", mode="test", runs_dir=tmp_path).run()
    after = (session.query(AgentRun).count(), {(x.company_id, x.tier_reached) for x in session.query(FunnelState)})
    assert before == after
    ctx2 = RunContext(session=session, llm=None, budget=Budget(5, 50), reuse=False, dry_run=True)
    rep = Pipeline(ctx2, m, u, reg, portfolio_name="P", mode="test", runs_dir=tmp_path).run()
    assert any(x["agent"] == "valuation_rebuttal" for x in rep.agent_log)


def test_r3_crashed_run_is_not_marked_completed(session, tmp_path, synthetic_ok):
    r = scripted_responses()

    def boom(p):  # noqa: ANN001, ANN202
        raise RuntimeError("corte a mitad de L3")
    r["auditor"] = boom
    with pytest.raises(RuntimeError):
        _run(session, tmp_path, r)
    runs = session.query(PipelineRun).all()
    assert runs and all(x.status == "running" for x in runs)
    assert session.query(FunnelState).count() == 0              # el estado del embudo solo se escribe al terminar
    rep = _run(session, tmp_path)
    assert session.get(PipelineRun, rep.run_id).status == "completed"


# ============================================================================ LLM: transporte y presupuesto
class _Stream:
    def __init__(self, item):
        self.item = item
        self.current_message_snapshot = NS(usage=NS(input_tokens=30000, output_tokens=10, cache_read_input_tokens=0,
                                                   server_tool_use=None))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        if isinstance(self.item, BaseException):
            raise self.item
        return self.item


class _Client:
    def __init__(self, items):
        self.items = list(items)
        self.beta = NS(messages=NS(stream=lambda **kw: _Stream(self.items.pop(0))))


def _msg(stop="pause_turn", inp=15000, out=800):
    return NS(stop_reason=stop, content=[NS(type="text", text='{"ok": 1}')], model="claude-opus-5-5",
              usage=NS(input_tokens=inp, output_tokens=out, cache_read_input_tokens=0, server_tool_use=None),
              stop_details=None)


def test_r3_transport_error_mid_stream_is_recorded_not_raised(session):
    import httpx2
    llm = AnthropicLLM(client=_Client([_msg(), httpx2.RemoteProtocolError("peer closed connection")]))
    ctx = RunContext(session=session, llm=llm, budget=Budget(30, 60), reuse=False)
    res = run_agent(ctx, "auditor", "X", "t", {"a": 1})
    assert res.status == "error" and "transporte" in res.error
    row = session.query(AgentRun).one()
    assert row.cost_usd > 0 and ctx.budget.spent_run == pytest.approx(row.cost_usd)
    # el input del pedido cortado (snapshot parcial) también se cuenta
    assert row.input_tokens == 15000 + 30000


def test_r3_pause_turn_continuations_respect_budget():
    llm = AnthropicLLM(client=_Client([_msg() for _ in range(5)]))
    with pytest.raises(LLMError, match="presupuesto") as ei:
        llm.complete(agent="a", model="claude-opus-5-5", system="s", user="u", schema={}, effort="high",
                     max_tokens=32000, web_search_uses=8, budget_usd=0.5)
    assert 0 < ei.value.cost_usd <= 0.5


def test_r3_keyboard_interrupt_records_billed_cost(session):
    llm = AnthropicLLM(client=_Client([_msg(), KeyboardInterrupt()]))
    ctx = RunContext(session=session, llm=llm, budget=Budget(30, 60), reuse=False)
    with pytest.raises(KeyboardInterrupt):
        run_agent(ctx, "auditor", "X", "t", {"a": 1})
    row = session.query(AgentRun).one()
    assert row.status == "error" and row.cost_usd > 0


# ============================================================================ mapa: propuestas
@pytest.fixture
def overlay_tmp(tmp_path, monkeypatch):
    from qa.agents import map_agents
    monkeypatch.setattr(map_agents, "MAP_OVERLAY", tmp_path / "map_overlay.yaml")
    monkeypatch.setattr(map_agents, "DISCOVERED", tmp_path / "discovered.yaml")
    yield tmp_path
    from qa.map.model import default_map
    from qa.universe.model import default_universe
    default_map.cache_clear()
    default_universe.cache_clear()


def _prop(kind, payload):
    from qa.db.schema import MapChangeProposal
    return MapChangeProposal(id=1, change_type=kind, payload=payload, evidence=[], rationale="r")


def test_r3_map_proposals_validated_on_the_resulting_map(overlay_tmp):
    from qa.agents.map_agents import apply_proposal
    bad = [("add_node", {"id": "x1", "name": "N", "type": "component", "layer": "memory", "segment": "memory_dram",
                         "depends_on": ["hbm"]}),                                            # clave reservada
           ("add_node", {"id": 123, "name": "N", "type": "component", "segment": "memory_dram", "depends_on": ["hbm"]}),
           ("add_node", {"id": "iso1", "name": "N", "type": "component", "segment": "memory_dram"}),   # aislado
           ("add_node", {"id": "bt1", "name": "N", "type": "widget", "segment": "memory_dram", "depends_on": ["hbm"]}),
           ("add_edge", {"source": "hbm", "target": "hyperscaler_capex", "relation": 5})]
    for kind, payload in bad:
        with pytest.raises(ValueError):
            apply_proposal(_prop(kind, payload))
    assert not (overlay_tmp / "map_overlay.yaml").exists()                  # nada escrito
    apply_proposal(_prop("add_edge", {"source": "copper", "target": "hbm"}))   # un cambio válido sigue pudiendo aplicarse
    assert (overlay_tmp / "map_overlay.yaml").exists()


def test_r3_discovered_company_duplicates_and_currency(overlay_tmp):
    import yaml
    from qa.agents.map_agents import apply_proposal
    base = {"name": "Foo", "country": "JP", "map_nodes": ["eml_lasers"], "estimated_exposure": "H"}
    with pytest.raises(ValueError, match="ya está"):
        apply_proposal(_prop("add_company", base | {"ticker": "nvda", "exchange": "NASDAQ"}))
    with pytest.raises(ValueError, match="duplicado"):
        apply_proposal(_prop("add_company", base | {"ticker": "6857", "exchange": "TSE"}))     # 6857.T ya existe
    with pytest.raises(ValueError, match="moneda"):
        apply_proposal(_prop("add_company", base | {"ticker": "ZZZQ", "exchange": "MARS"}))
    with pytest.raises(ValueError, match="contradice"):
        apply_proposal(_prop("add_company", base | {"ticker": "9999", "exchange": "TSE", "currency": "USD"}))
    apply_proposal(_prop("add_company", base | {"ticker": "9999", "exchange": "TSE"}))
    entry = yaml.safe_load((overlay_tmp / "discovered.yaml").read_text())["companies"][0]
    assert entry["currency"] == "JPY"


def test_r3_discovery_keeps_original_nodes_for_review(session):
    from qa.agents.map_agents import run_discovery
    from qa.bottlenecks import default_bottlenecks
    from qa.db.schema import MapChangeProposal
    from qa.map.model import default_map
    from qa.universe.model import default_universe
    cand = {"name": "Bar", "ticker": "BARX", "exchange": "NASDAQ", "country": "US", "map_nodes": ["hbm_typo"],
            "role": "r", "estimated_exposure": "M", "why_overlooked": "w", "evidence": [], "confidence": "low"}
    llm = ScriptedLLM({"discovery": {"candidates": [cand], "searched_for": "x"}})
    ctx = RunContext(session=session, llm=llm, budget=Budget(5, 50))
    run_discovery(ctx, default_map(), default_bottlenecks(), default_universe(), ["eml_lasers"])
    p = session.query(MapChangeProposal).one()
    assert p.payload["map_nodes"] == [] and p.payload["original_map_nodes"] == ["hbm_typo"] and "ATENCIÓN" in p.rationale


# ============================================================================ CSV manual / semillas
HDR = "entity,metric,value,unit,currency,period_end,period_type,fiscal_label,source_name,source_url,document_type,known_at\n"


def test_r3_manual_csv_bom_encoding_and_malformed_files(tmp_path):
    from qa.providers.manual import ManualCsvProvider
    ero = Company(ticker="ERO", name="x", exchange="NYSE", country="CA", currency="USD", nodes=["copper"], exposure=Exposure())
    (tmp_path / "a.csv").write_bytes(("﻿" + HDR + "ERO,revenue,284.3,USD,USD,2026-06-30,Q,,ir,https://x,pr,2026-08-01\n")
                                     .encode("utf-8"))
    (tmp_path / "b.csv").write_bytes((HDR + "ERO,revenue,1,USD,USD,2026-03-31,Q,Société,ir,https://x,pr,2026-05-01\n")
                                     .encode("cp1252"))
    (tmp_path / "c.csv").write_text(HDR + 'ERO,revenue,1,USD,USD,2025-12-31,Q,"Q4,ir,https://x,pr,2026-02-01\n'
                                    + "x" * 140000 + "\n", encoding="utf-8")
    (tmp_path / "d.csv").write_text(HDR + "ERO,revenue,1,USD,USD,2025-09-30,Q,,ir,https://x,pr,no-es-fecha\n", encoding="utf-8")
    prov = ManualCsvProvider(tmp_path)
    assert [p.value for p in prov.get_facts(ero)] == [284.3]
    assert len(prov.file_errors) == 2 and not prov.reconcile_safe
    assert any(r.startswith("d.csv:2:") for r in prov.rejected)


def test_r3_manual_rows_reconciled_when_rejected_or_removed(tmp_path, session, monkeypatch):
    from qa.providers import registry
    from qa.providers.base import ProviderUnavailable
    from qa.providers.manual import ManualCsvProvider
    c = Company(ticker="TSXCO", name="x", exchange="TSX", country="CA", currency="CAD", nodes=["copper"], exposure=Exposure())
    (tmp_path / "m.csv").write_text(HDR + "TSXCO,revenue,160,CAD,CAD,2026-06-30,Q,,ir,https://x,pr,2026-08-01\n",
                                    encoding="utf-8")

    class NoPrices:
        name = "stub"

        def snapshot(self, c):  # noqa: ANN001, ANN202
            raise ProviderUnavailable("offline")
    monkeypatch.setattr(registry, "price_provider", lambda: NoPrices())
    monkeypatch.setattr(registry, "fundamentals_providers", lambda: [ManualCsvProvider(tmp_path)])
    registry.ingest_company(session, c, registry.IngestReport())
    assert len(repo.load_datapoints(session, "TSXCO")) == 1
    (tmp_path / "m.csv").write_text(HDR + "TSXCO,revenue,160,CAD,,2026-06-30,Q,,ir,https://x,pr,2026-06-01\n",
                                    encoding="utf-8")                  # ahora rechazada (sin moneda, look-ahead)
    registry.ingest_company(session, c, registry.IngestReport())
    assert repo.load_datapoints(session, "TSXCO") == []
    # corrección que cambia la fuente citada: la fila vieja (otra source_name) también se reemplaza
    (tmp_path / "m.csv").write_text(HDR + "TSXCO,revenue,150,CAD,CAD,2026-06-30,Q,,wire,https://x,pr,2026-08-01\n",
                                    encoding="utf-8")
    registry.ingest_company(session, c, registry.IngestReport())
    (tmp_path / "m.csv").write_text(HDR + "TSXCO,revenue,151,CAD,CAD,2026-06-30,Q,,MD&A,https://y,mda,2026-08-05\n",
                                    encoding="utf-8")
    registry.ingest_company(session, c, registry.IngestReport())
    assert [(p.value, p.source.name) for p in repo.load_datapoints(session, "TSXCO")] == [(151, "MD&A")]


def test_r3_manual_stock_flow_types_and_unknown_metrics(tmp_path):
    from qa.providers.manual import ManualCsvProvider
    rows = ["X,debt_total,900,CAD,CAD,2026-06-30,Q,,s,https://x,f,2026-08-01",
            "X,revenue,100,CAD,CAD,2026-06-30,instant,,s,https://x,f,2026-08-01",
            "X,revenu,100,CAD,CAD,2026-06-30,Q,,s,https://x,f,2026-08-01",
            "X,cash,100,CAD,CAD,2026-06-30,instant,,s,https://x,f,2026-08-01"]
    (tmp_path / "x.csv").write_text(HDR + "\n".join(rows) + "\n", encoding="utf-8")
    prov = ManualCsvProvider(tmp_path)
    c = Company(ticker="X", name="x", exchange="TSX", country="CA", currency="CAD", nodes=["copper"], exposure=Exposure())
    assert [p.metric for p in prov.get_facts(c)] == ["cash"] and len(prov.rejected) == 3


def test_r3_seed_dates_strict(tmp_path, session):
    import json
    from qa.providers.manual import _parse_date, import_seed_research
    assert _parse_date("2026-00") is None and _parse_date("2026-13") is None and _parse_date("2026-02-30") is None
    assert _parse_date("2026-06") == date(2026, 6, 30)
    seed = [{"ticker": t, "latest_reported_quarter": {"source_url": "https://x", "period_end_date": "2026-06-30",
                                                      "revenue_unit": "USD millions", "revenue": 10, "source_date": sd}}
            for t, sd in (("A", "2026-06"), ("B", "2026-13"), ("C", "2026-08-05"))]
    p = tmp_path / "seed.json"
    p.write_text(json.dumps(seed), encoding="utf-8")
    pts = import_seed_research(p)
    assert {x.entity for x in pts} == {"C"}
    repo.upsert_datapoints(session, pts, provider="web_research_seed")
    seed[2]["latest_reported_quarter"]["source_date"] = None                # deja de calificar
    p.write_text(json.dumps(seed), encoding="utf-8")
    repo.upsert_datapoints(session, import_seed_research(p), provider="web_research_seed")
    assert repo.load_datapoints(session, "C") == []


def test_r3_options_provider_follows_settings(monkeypatch):
    from qa.providers import registry
    from qa.providers.base import ProviderUnavailable
    s = copy.deepcopy(registry.settings())
    s["providers"]["options"] = []
    monkeypatch.setattr(registry, "settings", lambda: s)
    with pytest.raises(ProviderUnavailable):
        registry.options_provider()
    s["providers"]["options"] = ["yahoo"]
    assert registry.options_provider().name == "yahoo"


# ============================================================================ ronda 4
def test_r4_partial_newer_balance_keeps_filing_and_still_checks_leverage():
    """Un comunicado posterior con solo caja y deuda no reemplaza un 10-Q que trae inversiones CP y patrimonio;
    igual se le aplica el control K7."""
    filing = [dp("cash", 330, "2026-03-27", "instant", "2026-05-05"),
              dp("short_term_investments", 620, "2026-03-27", "instant", "2026-05-05"),
              dp("equity", 2300, "2026-03-27", "instant", "2026-05-05")]
    seed = [dp("cash", 346.7, "2026-06-26", "instant", "2026-08-17", **SEED),
            dp("debt_total", 0, "2026-06-26", "instant", "2026-08-17", **SEED)]
    f = compute_fundamentals("X", filing + seed, as_of=date(2026, 8, 18))
    assert f.balance_date == date(2026, 3, 27) and f.v("cash_total") == 950 and f.v("equity") == 2300
    assert f.alt_balance_date == date(2026, 6, 26)
    qs = (("2025-09-30", "2025-11-01"), ("2025-12-31", "2026-02-01"), ("2026-03-31", "2026-05-01"), ("2026-06-30", "2026-08-01"))
    ebit = [dp(m, 10, e, "Q", k) for e, k in qs for m in ("operating_income", "d_and_a")]
    lev = filing + ebit + [dp("cash", 50, "2026-06-26", "instant", "2026-08-17", **SEED),
                           dp("debt_total", 3000, "2026-06-26", "instant", "2026-08-17", **SEED)]
    g = compute_fundamentals("X", lev, as_of=date(2026, 8, 18))
    assert g.balance_date == date(2026, 3, 27)
    kills, _ = kill_switches(g, None, None, {}, None, False)
    assert any("balance posterior" in k and k.startswith("K7") for k in kills)


def test_r4_debt_tagged_only_in_annual_report_blocks_at_q3():
    pts = [dp("debt_total", 3000, "2025-12-31", "instant", "2026-02-20"), dp("cash", 400, "2026-09-30", "instant", "2026-11-05")]
    f = compute_fundamentals("X", pts, as_of=date(2026, 11, 10))
    assert f.debt_elsewhere
    with pytest.raises(ValueError, match="otra fecha"):
        balance_inputs(f)


def test_r4_debt_free_filer_without_debt_tag_scores_net_cash():
    ebit = [dp("operating_income", 100, e, "Q", k) for e, k in (("2025-09-30", "2025-11-01"), ("2025-12-31", "2026-02-01"),
                                                                ("2026-03-31", "2026-05-01"), ("2026-06-30", "2026-08-01"))]
    pts = ebit + [dp("cash", 800, "2026-06-30", "instant", "2026-08-01"), dp("equity", 1500, "2026-06-30", "instant", "2026-08-01")]
    f = compute_fundamentals("X", pts, as_of=date(2026, 9, 1))
    assert f.debt_assumed_zero and f.v("debt") is None and f.v("net_debt") == -800 and f.v("roic") is not None
    fq = score_financial_quality(f)
    assert "leverage=10.0" in fq.rationale and "roic" not in fq.rationale.split("sin dato")[-1]
    _, alerts = kill_switches(f, None, None, {}, None, False)
    assert any(a.startswith("deuda supuesta 0") for a in alerts)
    seed_only = compute_fundamentals("X", [dp("cash", 800, "2026-06-30", "instant", "2026-08-01", **SEED)],
                                     as_of=date(2026, 9, 1))
    assert not seed_only.debt_assumed_zero                         # el supuesto solo vale para un balance de filing


def test_r4_annual_filer_keeps_fy_ttm_with_isolated_later_quarter():
    pts = [dp("revenue", 3500, "2025-12-31", "FY", "2026-03-15"),
           dp("revenue", 814, "2026-06-30", "Q", "2026-07-31", **SEED)]
    f = compute_fundamentals("X", pts, as_of=date(2026, 10, 22))
    assert f.v("revenue_ttm") == 3500 and "trimestres posteriores aislados" in f.metrics["revenue_ttm"].note


def test_r4_on_time_20f_filer_not_stale_before_filing():
    pts = [dp("cash", 900, "2024-12-31", "instant", "2025-04-29"), dp("debt_total", 1100, "2024-12-31", "instant", "2025-04-29")]
    for d in (date(2026, 4, 28), date(2026, 5, 14)):                 # 4 meses + prórroga 12b-25
        assert not compute_fundamentals("X", pts, as_of=d).balance_stale
    assert compute_fundamentals("X", pts, as_of=date(2026, 6, 10)).balance_stale


def test_r4_invalid_stream_is_recorded_not_raised(session):
    llm = AnthropicLLM(client=_Client([AssertionError(), ValueError("Unterminated string")]))
    for _ in range(2):
        ctx = RunContext(session=session, llm=llm, budget=Budget(30, 60), reuse=False)
        res = run_agent(ctx, "auditor", "X", "t", {"a": 1})
        assert res.status == "error" and "stream inválido" in res.error


def test_r4_dry_run_simulates_the_budget_gate_and_committee_is_reserved(session, tmp_path, synthetic_ok):
    if "demo" not in session.info:
        session.info["demo"] = _demo_setup(session)
    m, u, reg = session.info["demo"]
    ctx = RunContext(session=session, llm=None, budget=Budget(1.5, 50), reuse=False, dry_run=True)
    rep = Pipeline(ctx, m, u, reg, portfolio_name="P", mode="test", runs_dir=tmp_path).run()
    assert rep.budget_needed_usd and rep.budget_needed_usd > 1.5
    assert any("se saltarían" in n for n in rep.notes)
    log = {(x["agent"], x["subject"]): x["status"] for x in rep.agent_log}
    assert log[("portfolio_manager", "portfolio")] == "dry_run"          # la reserva del comité no se consume antes
    # corrida real con presupuesto justo: L3 se saltea pero el comité corre
    real = _run(session, tmp_path, budget=1.5)
    statuses = {x["agent"]: x["status"] for x in real.agent_log}
    assert statuses.get("auditor") == "skipped_budget" and "comité no ejecutado" not in " ".join(real.notes)


def test_r4_exact_evidence_dates_are_not_capped_by_review_date():
    from qa.bottlenecks import BottleneckRecord, known_date, registry_as_of, stale_evidence
    from qa.map.model import default_map
    ev = {"claim": "x", "date": "2026-11-05", "evidence_type": "company_statement"}
    assert known_date(ev, date(2026, 10, 21)) == date(2026, 11, 5)
    assert known_date({"date": "2026 (approx.)"}, date(2026, 10, 21)) == date(2026, 10, 21)
    b = BottleneckRecord(id="b", name="b", layer="l", status="current_shortage", confidence="high", map_nodes=["hbm"],
                         raw={"name": "b", "status": "current_shortage", "evidence": [ev], "counter_evidence": []},
                         reviewed_at=date(2026, 10, 21))
    assert registry_as_of({"b": b}, default_map(), date(2026, 11, 4)) == {}
    assert stale_evidence({"b": b})


# ============================================================================ ronda 4b (comité y datos/mapa)
def test_r4b_small_full_exit_and_small_new_entry_always_trade(session):
    now = datetime.now(timezone.utc)
    q = {"A": ledger.Quote(10.0, now, "t", "large"), "B": ledger.Quote(10.0, now, "t", "large")}
    ledger.rebalance(session, "S", {"A": 0.004}, q)                     # entrada nueva < banda: se opera
    assert ledger.holdings(session, "S").get("A", 0) > 0
    ledger.rebalance(session, "S", {}, q)                                # salida total < banda: se opera
    assert ledger.holdings(session, "S") == {}


def _band_pipeline(session, tmp_path):
    m, u, reg = _demo_setup(session)
    return Pipeline(_demo_ctx(session), m, u, reg, portfolio_name="B", mode="test", runs_dir=tmp_path)


def test_r4b_no_trade_band_yields_to_min_cash_and_caps(session, tmp_path):
    p = _band_pipeline(session, tmp_path)
    cur = {f"H{i:02d}": 0.081667 for i in range(12)}
    meta = {k: PositionMeta(k, layer=f"l{k}", primary_bottleneck=f"b{k}", eligible=True) for k in list(cur) + ["NEW"]}
    targets = {k: 0.0776 for k in cur} | {"NEW": 0.0485}
    notes: dict = {}
    out = p._no_trade_band(targets, cur, meta, {}, notes)
    assert sum(out.values()) <= 0.98 + 1e-9                             # la caja mínima manda sobre la banda
    assert out["NEW"] == 0.0485
    held = {"X": 0.1045}
    meta = {"X": PositionMeta("X", layer="l", primary_bottleneck="b", eligible=True)}
    out = p._no_trade_band({"X": 0.10}, held, meta, {}, {})
    assert out["X"] == 0.10                                               # el tope por posición manda sobre la banda
    out = p._no_trade_band({"X": 0.0815}, {"X": 0.083}, meta, {}, {})
    assert out["X"] == 0.083                                              # ajuste chico sin conflicto: no se opera


def test_r4b_pm_sees_k9_holding_as_hold_only(session, tmp_path, synthetic_ok):
    _holding_setup(session, tmp_path)
    r = scripted_responses()
    r["auditor"] = {"broken": True}
    seen = {}

    def pm(pack):  # noqa: ANN001, ANN202
        for c in pack["candidates"]:
            seen[c["company_id"]] = (c["score"] or {}).get("eligible"), (c["score"] or {}).get("kill_switches")
        return _pm({"TEST_SLOW": ("HOLD", 0.08)})(pack)
    r["portfolio_manager"] = pm
    _run(session, tmp_path, r)
    assert seen["TEST_SLOW"] == ("hold_only", [])
    assert ledger.holdings(session, "P").get("TEST_SLOW", 0) > 0


def test_r4b_reserve_is_not_passed_to_pause_turn_budget(session):
    got = {}

    class Spy:
        def complete(self, **kw):  # noqa: ANN003, ANN201
            got["budget"] = kw["budget_usd"]
            raise LLMError("x")
    ctx = RunContext(session=session, llm=Spy(), budget=Budget(5, 50), reuse=False, reserve_usd=0.7)
    run_agent(ctx, "auditor", "X", "t", {"a": 1})
    assert got["budget"] == pytest.approx(5 - 0.7)
    run_agent(ctx, "portfolio_manager", "portfolio", "t", {"candidates": []})
    assert got["budget"] == pytest.approx(5)


def test_r4b_manual_csv_structural_errors_never_delete_rows(tmp_path, session, monkeypatch):
    from qa.providers import registry
    from qa.providers.base import ProviderUnavailable
    from qa.providers.manual import ManualCsvProvider
    c = Company(ticker="ERO", name="x", exchange="NYSE", country="CA", currency="USD", nodes=["copper"], exposure=Exposure())
    good = (HDR + "ERO,revenue,284.3,USD,USD,2026-06-30,Q,,ir,https://x,pr,2026-08-01\n"
            "ERO,cash,90,USD,USD,2026-06-30,instant,,ir,https://x,pr,2026-08-01\n")

    class NoPrices:
        name = "stub"

        def snapshot(self, c):  # noqa: ANN001, ANN202
            raise ProviderUnavailable("offline")
    monkeypatch.setattr(registry, "price_provider", lambda: NoPrices())
    f = tmp_path / "m.csv"
    for bad in (HDR + 'ERO,revenue,284.3,USD,USD,2026-06-30,Q,"Q2 2026,ir,https://x,pr,2026-08-01\n',   # comilla
                good.replace(",", ";"),                                                                 # Excel es-ES
                good.replace("ir,https://x", "ir,https://x,extra")):                                  # columnas corridas
        f.write_text(good, encoding="utf-8")
        monkeypatch.setattr(registry, "fundamentals_providers", lambda: [ManualCsvProvider(tmp_path)])
        registry.ingest_company(session, c, registry.IngestReport())
        assert len(repo.load_datapoints(session, "ERO")) == 2
        f.write_text(bad, encoding="utf-8")
        prov = ManualCsvProvider(tmp_path)
        monkeypatch.setattr(registry, "fundamentals_providers", lambda: [prov])
        rep = registry.IngestReport()
        registry.ingest_company(session, c, rep)
        assert prov.file_errors and len(repo.load_datapoints(session, "ERO")) == 2, bad[:60]


def test_r4b_manual_csv_rejects_reserved_source_names_and_locates_errors(tmp_path):
    from qa.providers.manual import load_manual_csv
    p = tmp_path / "x.csv"
    p.write_text(HDR + "ERO,revenue,1,USD,USD,2026-06-30,Q,,sec_edgar,https://x,10q,2026-08-01\n", encoding="utf-8")
    pts, rej, err = load_manual_csv(p)
    assert not pts and "reservado" in rej[0] and err is None
    body = HDR + "".join(f"ERO,revenue,{i},USD,USD,2026-06-30,Q,,ir,https://x,pr,2026-08-01\n" for i in range(1, 10))
    p.write_bytes(body.encode("utf-8") + "ERO,revenue,1,USD,USD,2026-06-30,Q,Société,ir,https://x,pr,2026-08-01\n"
                  .encode("cp1252"))
    _, _, err = load_manual_csv(p)
    assert err.startswith("x.csv:11:")


def test_r4b_map_proposals_extra_keys_root_nodes_and_currency(overlay_tmp):
    import yaml
    from qa.agents.map_agents import apply_proposal, _company_currency
    apply_proposal(_prop("add_node", {"id": "hbm4_base_die", "name": "HBM4 base die", "type": "component",
                                      "segment": "memory_dram", "depends_on": ["hbm"], "description": "base die"}))
    node = yaml.safe_load((overlay_tmp / "map_overlay.yaml").read_text())["nodes"][0]
    assert "description" not in node and "base die" in node["notes"]
    with pytest.raises(ValueError, match="reservadas"):
        apply_proposal(_prop("add_node", {"id": "x9", "name": "N", "type": "component", "segment": "memory_dram",
                                          "depends_on": ["hbm"], "layer": "memory"}))
    apply_proposal(_prop("add_node", {"id": "hbm_test_handlers", "name": "HBM test handlers", "type": "equipment",
                                      "segment": "memory_dram", "feeds": ["hbm"]}))          # nodo raíz
    ov = yaml.safe_load((overlay_tmp / "map_overlay.yaml").read_text())
    assert {"source": "hbm_test_handlers", "target": "hbm"} in [{k: e[k] for k in ("source", "target")} for e in ov["edges"]]
    assert _company_currency({"exchange": "LSE", "currency": "USD"}) == "USD"
    assert _company_currency({"exchange": "LSE", "currency": "GBX"}) == "GBP"
    with pytest.raises(ValueError):
        _company_currency({"exchange": "NASDAQ", "currency": "JPY"})


def test_r4b_evidence_ranges_never_before_publication():
    from qa.bottlenecks import _parse_evidence_date, known_date
    assert _parse_evidence_date({"date": "2026-10-28/11-02"})[0] == date(2026, 11, 2)
    assert _parse_evidence_date({"date": "2026-10-28/02"})[0] == date(2026, 11, 2)
    assert _parse_evidence_date({"date": "2025-12/01"})[0] == date(2026, 1, 31)
    assert _parse_evidence_date({"date": "2026-10-16 / 2026-10-19"})[0] == date(2026, 10, 19)
    assert known_date({"date": "2026-11 (approx.)"}, date(2026, 10, 21)) == date(2026, 11, 30)
    assert known_date({"date": "2026 (exact date not captured)"}, date(2026, 10, 21)) == date(2026, 10, 21)


# ============================================================================ primera ingesta real (SEC)
def test_real_concept_switch_is_not_a_restatement(session):
    """Patrimonio sin minoritarios (concepto preferido) y luego con minoritarios en un comparativo: otra medida."""
    raw = {"facts": {"us-gaap": {
        "StockholdersEquity": {"units": {"USD": [fact(653, None, "2025-09-30", "2025-10-28")]}},
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest": {"units": {"USD": [
            fact(677, None, "2025-09-30", "2026-10-22", accn="B")]}},
        "NetIncomeLoss": {"units": {"USD": [fact(100, "2026-01-01", "2026-03-31", "2026-05-01"),
                                            fact(100.5, "2026-01-01", "2026-03-31", "2026-08-01", accn="C"),
                                            fact(80, "2026-01-01", "2026-03-31", "2026-08-02", "10-Q/A", accn="D")]}}}}}
    pts = parse_companyfacts(raw, "X", cik=1)
    eq = [p for p in pts if p.metric == "equity"]
    assert [p.value for p in eq] == [653]                       # el comparativo con minoritarios se ignora
    ni = [p for p in pts if p.metric == "net_income"]
    assert ";material" not in ni[1].note and ";material" in ni[2].note      # 0.5% no es material; /A sí
    repo.upsert_datapoints(session, pts)
    from qa.feed.events import detect_restatements
    assert detect_restatements(session, date(2026, 1, 1)) == 1


def test_real_explicit_fx_when_reporting_and_quote_currency_differ(session):
    from qa.db.schema import Price
    from qa.funnel.pack import market_inputs
    c = Company(ticker="CCJ", name="Cameco", exchange="NYSE", country="CA", currency="USD", nodes=["uranium_mining"],
                exposure=Exposure())
    f = compute_fundamentals("CCJ", [dp("revenue", 3500, "2025-12-31", "FY", "2026-03-15", unit="CAD", ccy="CAD"),
                                     dp("diluted_shares", 435, "2025-12-31", "FY", "2026-03-15", unit="shares", ccy=None)],
                             as_of=date(2026, 10, 22))
    session.add(MarketSnapshot(company_id="CCJ", as_of=datetime(2026, 10, 22, 20, tzinfo=timezone.utc), price=90.0,
                               currency="USD", shares_outstanding=435e6, source="yahoo"))
    session.flush()
    assert market_inputs(session, c, f, date(2026, 10, 22)) is None            # sin FX explícito no se valúa
    session.add(Price(symbol="FX:CADUSD", date=date(2026, 10, 22), close=0.72, currency="USD", source="yahoo"))
    session.flush()
    mk = market_inputs(session, c, f, date(2026, 10, 22))
    assert mk.fx_report_to_price == 0.72 and "FX CAD->USD" in mk.price_source


def test_real_companyfacts_gap_is_reported():
    from qa.providers.sec_edgar import SecEdgarProvider
    prov = SecEdgarProvider(user_agent="test")
    sub = {"filings": {"recent": {"form": ["8-K", "10-Q", "10-Q"], "filingDate": ["2026-09-04", "2026-07-28", "2026-04-28"],
                                  "reportDate": ["2026-08-31", "2026-06-30", "2026-03-31"]}}}
    prov._get = lambda url: sub
    pts = [dp("revenue", 1, "2026-03-31", "Q", "2026-04-28")]
    assert "10-Q presentado el 2026-07-28" in prov._filing_gap(1, pts)
    assert prov._filing_gap(1, pts + [dp("revenue", 1, "2026-06-30", "Q", "2026-07-28")]) is None


def test_real_market_cap_from_price_and_shares_when_yahoo_omits_it(monkeypatch):
    import pandas as pd
    from qa.providers import yahoo

    class FI(dict):
        def __getitem__(self, k):           # fast_info sin market cap (Yahoo devuelve 401 en quoteSummary)
            if k == "market_cap":
                raise KeyError(k)
            return dict.__getitem__(self, k)
    df = pd.DataFrame({"Open": [10.0], "High": [10.0], "Low": [10.0], "Close": [10.0], "Volume": [1000]},
                      index=pd.to_datetime(["2026-10-23"]))
    fake = NS(Ticker=lambda s: NS(fast_info=FI(last_price=10.0, currency="USD", shares=1_000_000),
                                  history=lambda **k: df.copy()))
    monkeypatch.setattr(yahoo, "_yf", lambda: fake)
    c = Company(ticker="X", name="x", exchange="NASDAQ", country="US", currency="USD", nodes=["hbm"], exposure=Exposure())
    snap = yahoo.YahooProvider().snapshot(c)
    assert snap.market_cap == 10_000_000 and snap.market_cap_usd == 10_000_000
