"""Demo offline de punta a punta con DATOS SINTÉTICOS y LLM GUIONADO.

Sirve para verificar que el pipeline completo (L0 -> L3 -> comité -> cartera simulada) funciona sin red ni
API key. NADA de esto es análisis real: empresas `TEST_*`, datos `synthetic_test_data`, respuestas de agentes
pregrabadas. Usa una base separada (data/demo.sqlite) para no contaminar la base real.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from qa.agents.base import Budget, RunContext
from qa.agents.llm import ScriptedLLM
from qa.bottlenecks import load_bottlenecks
from qa.config import FIXTURES_DIR, ROOT, utc_today
from qa.db import repo
from qa.db.session import session_scope
from qa.funnel.pipeline import Pipeline
from qa.map.model import load_map
from qa.providers.base import MarketSnapshotData
from qa.providers.manual import FixtureProvider
from qa.universe.model import Company, Exposure, Universe

DEMO_DB = f"sqlite:///{(ROOT / 'data' / 'demo.sqlite').as_posix()}"
DEMO_PORTFOLIO = "DEMO-SYNTHETIC"
SYN = {"TEST_ALPHA": (["eml_lasers", "compound_substrates"], {"ai": "H", "energy": "N", "commodities": "L"}, "active"),
       "TEST_BURN": (["smr_advanced"], {"ai": "L", "energy": "H", "commodities": "N"}, "candidate"),
       "TEST_SLOW": (["distribution_transformers"], {"ai": "L", "energy": "H", "commodities": "L"}, "active")}


def _ev(claim: str) -> dict:
    return {"claim": claim, "source_url": None, "source_date": None, "verification": "hypothesis"}


def _rubric(band: str, score: float, why: str) -> dict:
    return {"rubric_band": band, "score": score, "rationale": why, "evidence": [_ev("demo")], "missing_information": []}


def _growth(p: dict) -> float:
    g = (((p.get("data_pack") or p).get("fundamentals") or {}).get("metrics") or {}).get("revenue_growth_yoy", {})
    return g.get("value") or 0.0 if isinstance(g, dict) else 0.0


def scripted_responses() -> dict:
    def triage(p):  # noqa: ANN001, ANN202
        cid = p["company"]["id"]
        hi = cid == "TEST_ALPHA"
        return {"real_exposure": "direct" if hi else "partial", "exposure_explanation": "DEMO",
                "primary_bottleneck_id": None, "estimated_exposure_pct_revenue": 60.0 if hi else 20.0,
                "exposure_pct_basis": "estimate", "quick_red_flags": [] if hi else ["DEMO flag"],
                "priority": 8.0 if hi else 5.0, "uncertainty": 6.0, "recommended_next": "L2", "reason": "DEMO"}

    def valuation(p):  # noqa: ANN001, ANN202
        g = max(min(_growth(p), 0.6), -0.1)
        rebut = "bear_case" in p
        base = {"tax_rate": 0.21, "da_pct_revenue": 0.04, "capex_pct_revenue": 0.06, "nwc_pct_incremental_revenue": 0.15,
                "annual_dilution": 0.01, "exit_multiple_type": "EV/EBIT", "author": "demo", "revenue_path": None}
        bear_p = 0.30 if rebut else 0.25
        return {"scenarios": [
            base | {"name": "bear", "probability": bear_p, "revenue_growth": [g * 0.1, 0.0, 0.03],
                    "ebit_margin": [0.10, 0.08, 0.08], "exit_multiple": 10, "rationale": "DEMO bear"},
            base | {"name": "base", "probability": 0.75 - bear_p if rebut else 0.5, "revenue_growth": [g * 0.7, g * 0.5, g * 0.4],
                    "ebit_margin": [0.18, 0.19, 0.20], "exit_multiple": 16, "rationale": "DEMO base"},
            base | {"name": "bull", "probability": 0.25, "revenue_growth": [g, g * 0.8, g * 0.6],
                    "ebit_margin": [0.22, 0.25, 0.27], "exit_multiple": 22, "rationale": "DEMO bull"}],
            "comparables": [], "method_notes": "DEMO", "key_sensitivities": ["margen", "múltiplo"]}

    def pm(p):  # noqa: ANN001, ANN202
        cands = sorted(p["candidates"], key=lambda c: -(c["score"] or {}).get("total", 0))
        return {"decisions": [{"company_id": c["company_id"], "action": "BUY" if i == 0 else "AVOID",
                               "target_weight": 0.08 if i == 0 else 0.0, "conviction": "medium",
                               "thesis": "DEMO — no es una recomendación", "catalysts": [], "risks": ["DEMO"],
                               "invalidation_triggers": ["DEMO"]} for i, c in enumerate(cands)],
                "cash_weight": 0.92, "portfolio_rationale": "DEMO", "correlation_and_concentration_notes": "DEMO",
                "rejected_with_reason": []}

    red = {"going_concern": False, "customer_concentration_gt_40": False, "export_control_exposure": False,
           "restatement": False, "recent_equity_raise": False, "financing_committed": False, "other": []}
    return {
        "triage": triage,
        "industrial": {"company_role_in_chain": "DEMO", "bottleneck_links": [], "bottleneck_adjustment": 0.0,
                       "adjustment_rationale": "DEMO", "substitution_threats": [], "new_dependencies_hypotheses": [],
                       "evidence": [_ev("demo")]},
        "fundamental": {"business_quality_summary": "DEMO", "exposure_pct_revenue": 55.0, "exposure_basis": "estimate",
                        "growth_drivers": [], "pricing_power_evidence": [], "capacity_and_backlog": "DEMO",
                        "margin_trajectory": "DEMO", "balance_sheet_assessment": "DEMO", "red_flags": red,
                        "economic_capture": _rubric("7-8", 7.0, "DEMO"), "data_issues": []},
        "catalyst": {"catalysts": [], "catalysts_score": _rubric("5-6", 5.5, "DEMO")},
        "valuation": valuation, "valuation_rebuttal": valuation,
        "expectations": {"priced_in_summary": "DEMO", "implied_vs_base": "roughly_aligned", "consensus_notes": "DEMO",
                         "informational_edge": _rubric("5-6", 5.0, "DEMO")},
        "bear": {"bear_points": [{"argument": "DEMO: posible doble pedido en la cadena", "evidence": [_ev("demo")],
                                  "severity": "high", "what_would_resolve_it": "datos de inventario de clientes"}],
                 "is_bear_scenario_too_optimistic": True, "proposed_bear_changes": "subir probabilidad bear",
                 "probability_challenge": "DEMO", "overall_view": "thesis_weakened"},
        "auditor": {"verdict": "pass_with_issues", "issues": [{"area": "data_quality", "description": "datos sintéticos",
                                                               "severity": "major", "fix": "usar datos reales"}],
                    "unverified_claims": [], "disagreements_to_log": []},
        "portfolio_manager": pm,
    }


def _demo_feed(s) -> None:  # noqa: ANN001
    """Eventos y propuestas SINTÉTICOS para poder ver el Research Feed en la demo."""
    from qa.db.schema import FeedEvent, MapChangeProposal
    today = utc_today()
    s.add_all([
        FeedEvent(event_date=today, company_id="TEST_ALPHA", event_type="earnings", title="DEMO: resultados trimestrales",
                  detail="Evento sintético de demostración", verification="synthetic_test_data", impact="unknown",
                  triggers_review=True),
        FeedEvent(event_date=today, company_id="TEST_SLOW", event_type="price_move",
                  title="DEMO: movimiento de precio +18%", verification="synthetic_test_data", impact="unknown",
                  triggers_review=True),
        FeedEvent(event_date=today, bottleneck_id="inp_substrates_eml_lasers", event_type="capacity",
                  title="DEMO: anuncio de expansión de capacidad", verification="synthetic_test_data", impact="unknown"),
        MapChangeProposal(change_type="add_company", status="pending", rationale="DEMO: candidata sintética",
                          payload={"name": "DEMO Corp (sintética)", "ticker": None, "exchange": None,
                                   "map_nodes": ["eml_lasers"], "role": "DEMO", "estimated_exposure": "unknown"},
                          evidence=[{"claim": "DEMO", "source_url": None, "source_date": None,
                                     "verification": "hypothesis"}]),
    ])


def run_demo(db_url: str = DEMO_DB) -> dict:
    m = load_map()
    fx = json.loads((FIXTURES_DIR / "synthetic_companies.json").read_text(encoding="utf-8"))
    comps = {cid: Company(ticker=cid, name=f"{cid} (SINTÉTICA)", exchange="NASDAQ", country="US", currency="USD",
                          nodes=nodes, exposure=Exposure(**expo), status=status, tags=["synthetic"],
                          notes="SYNTHETIC — NOT A REAL COMPANY")
             for cid, (nodes, expo, status) in SYN.items()}
    u = Universe(version="demo", companies=comps)
    reg = load_bottlenecks(m, u)
    with session_scope(db_url) as s:
        repo.sync_map(s, m)
        repo.sync_universe(s, u, m)
        fp = FixtureProvider()
        for c in comps.values():
            repo.upsert_datapoints(s, fp.get_facts(c), provider="fixture", entities={c.id})
            mk = fx["market"][c.id]
            repo.save_snapshot(s, MarketSnapshotData(company_id=c.id, as_of=datetime.now(timezone.utc), price=mk["price"],
                                                     currency="USD", shares_outstanding=mk["shares"],
                                                     market_cap=mk["price"] * mk["shares"],
                                                     market_cap_usd=mk["price"] * mk["shares"],
                                                     adv_usd_20d=mk["adv_usd_20d"], source="synthetic_fixture"))
        _demo_feed(s)
        s.flush()
        ctx = RunContext(session=s, llm=ScriptedLLM(scripted_responses()), budget=Budget(per_run_usd=5, monthly_usd=50),
                         decision_date=utc_today(), reuse=False)
        from qa.scoring import engine
        engine.ALLOW_SYNTHETIC_FOR_DEMO = True
        try:
            rep = Pipeline(ctx, m, u, reg, portfolio_name=DEMO_PORTFOLIO, mode="demo_synthetic").run()
        finally:
            engine.ALLOW_SYNTHETIC_FOR_DEMO = False
        return rep.__dict__
