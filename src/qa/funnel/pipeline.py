"""Embudo de análisis por capas (control de costos) y orquestación multiagente.

  L0  screen cuantitativo, costo 0, todo el universo  -> filtros duros + pre-score por reglas
  L1  triage LLM barato (Haiku)                        -> exposición real, red flags, prioridad e incertidumbre
  L2  análisis estándar (Sonnet): Industrial, Fundamental, Catalyst, Valuation, Expectations -> escenarios + score
  L3  deep dive (Opus): Bear Case -> rebuttal de Valuation -> Research Auditor -> score final + discrepancias
  Comité: Portfolio Manager (Opus) -> decisiones -> restricciones por código -> cartera simulada

Promoción entre capas por VALOR DE LA INFORMACIÓN: no solo "cuanto mejor la empresa, más análisis", sino
prioridad + incertidumbre (más análisis donde la decisión puede cambiar y lo que está en juego es mayor) y
siempre las posiciones en cartera (el riesgo de pérdida está en lo que ya tenemos).
Reutilización: si el data pack no cambió materialmente (hash estable), se reutiliza el análisis previo.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from qa.agents import schemas as S
from qa.agents.base import AgentResult, RunContext, run_agent
from qa.bottlenecks import BottleneckRecord, company_bottlenecks, registry_as_of
from qa.config import DATA_DIR, settings
from qa.db import repo
from qa.db.schema import (as_utc, Disagreement, FunnelState, PipelineRun, Recommendation, ScenarioSet as ScenarioRow,
                          ScoreRow)
from qa.fundamentals.metrics import Fundamentals, compute_fundamentals
from qa.funnel.pack import company_pack, end_of_day, hash_view, market_inputs
from qa.map.model import IndustryMap
from qa.portfolio import ledger
from qa.portfolio.construction import PositionMeta, enforce_constraints
from qa.scoring.engine import (FactorScore, ScoreCard, combine, edge_proxy, score_bottleneck,
                               score_financial_quality, score_valuation)
from qa.universe.model import Company, Universe
from qa.valuation.core import MarketInputs, ScenarioSet, compute_multiples, run_scenarios

log = logging.getLogger(__name__)
TIERS = ("L0", "L1", "L2", "L3", "committee")


@dataclass
class Dossier:
    company: Company
    l0: dict = field(default_factory=dict)
    pack: dict | None = None
    fundamentals: Fundamentals | None = None
    market: MarketInputs | None = None
    agents: dict[str, AgentResult] = field(default_factory=dict)
    scenarios: ScenarioSet | None = None
    scenarios_initial: ScenarioSet | None = None
    scorecard: ScoreCard | None = None
    score_id: int | None = None
    scenario_id: int | None = None
    tier: str = "L0"
    auto_checks: list[str] = field(default_factory=list)
    scenario_checks: list[str] = field(default_factory=list)   # se reemplazan cada vez que cambian los escenarios
    disagreements: list[dict] = field(default_factory=list)
    # procedencia del ScenarioSet EN USO: (autor, supuestos normalizados); evita mezclar supuestos y resultados
    scenario_source: tuple[str, list[dict]] | None = None
    scenario_row_source: tuple[str, list[dict]] | None = None

    def out(self, agent: str):  # noqa: ANN201
        r = self.agents.get(agent)
        return r.output if r and r.ok else None


@dataclass
class CycleReport:
    run_id: str
    started_at: str
    tiers_run: list[str]
    counts: dict[str, int] = field(default_factory=dict)
    cost_usd: float = 0.0
    est_cost_usd: float = 0.0
    agent_log: list[dict] = field(default_factory=list)
    l0_table: list[dict] = field(default_factory=list)
    scores: list[dict] = field(default_factory=list)
    decisions: list[dict] = field(default_factory=list)
    constraint_notes: list[str] = field(default_factory=list)
    trades: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    mode: str = "real"                 # real | demo_synthetic | test
    db_url: str | None = None
    dry_run: bool = False


class Pipeline:
    def __init__(self, ctx: RunContext, m: IndustryMap, u: Universe, registry: dict[str, BottleneckRecord],
                 portfolio_name: str | None = None, mode: str = "real", runs_dir: Path | None = None):
        self.ctx, self.m, self.u = ctx, m, u
        self.reg = registry_as_of(registry, m, ctx.decision_date)     # sin evidencia posterior a la decisión
        self.cfg = settings()
        self.crit = m.structural_criticality()
        self.portfolio_name = portfolio_name or self.cfg["portfolio"]["name"]
        self.mode = mode
        # los reportes de demo/test NO van a data/runs (historial real)
        self.runs_dir = runs_dir or (DATA_DIR / ("runs" if mode == "real" else "demo_runs"))
        self.eod = end_of_day(ctx.decision_date)
        self._state_calls: list[tuple[Dossier, str]] = []    # FunnelState se escribe solo si la corrida termina

    # ================================================================== L0
    def screen(self, companies: list[Company]) -> list[Dossier]:
        c0 = self.cfg["funnel"]["L0"]
        holdings = set(ledger.holdings(self.ctx.session, self.portfolio_name))
        dossiers = []
        evs = {}
        for c in companies:
            d = Dossier(company=c)
            pts = repo.load_datapoints(self.ctx.session, c.id, as_of=self.ctx.decision_date)
            d.fundamentals = compute_fundamentals(c.id, pts, self.ctx.decision_date) if pts else None
            d.market = market_inputs(self.ctx.session, c, d.fundamentals, self.ctx.decision_date)
            snap = repo.latest_snapshot(self.ctx.session, c.id, self.eod)
            reasons, passed = [], True
            if c.status in ("excluded", "delisted"):
                passed = False; reasons.append(f"status {c.status}")
            mcap = snap.market_cap_usd if snap else None
            adv = snap.adv_usd_20d if snap else None
            if mcap is None or adv is None:
                reasons.append("sin datos de mercado (correr `qa ingest`)")
                if c.id not in holdings and not c.seed:
                    passed = False
            else:
                # sin montos exactos en el texto (cambian a diario y romperían la reutilización): van en mcap/adv
                if mcap < c0["min_market_cap_usd"]:
                    passed = False; reasons.append(f"market cap < mínimo ${c0['min_market_cap_usd']/1e6:.0f}M")
                if adv < c0["min_adv_usd"]:
                    passed = False; reasons.append(f"liquidez < mínimo ${c0['min_adv_usd']/1e6:.1f}M/día")
            if d.fundamentals is None:
                reasons.append("sin fundamentales")
            if d.fundamentals and d.market:
                mult = compute_multiples(d.fundamentals, d.market)
                evs_ = mult.values.get("EV/Sales")
                g = d.fundamentals.v("revenue_growth_yoy")
                if evs_:
                    evs[c.id] = evs_ / max(1 + (g or 0), 0.5)          # EV/Sales ajustado por crecimiento
            b = score_bottleneck(company_bottlenecks(c.nodes, self.reg), max(self.crit.get(n, 0.0) for n in c.nodes))
            fq = score_financial_quality(d.fundamentals) if d.fundamentals else FactorScore(score=0, rationale="sin datos")
            ai, en, cm = c.ai_energy_commodity_vector()
            expo = 10 * max(ai, 0.9 * en, 0.7 * cm)
            d.l0 = {"pass": passed, "reasons": reasons, "bottleneck": b.score, "financial_quality": fq.score,
                    "exposure_prior": round(expo, 2), "mcap_usd": mcap, "adv_usd": adv, "holding": c.id in holdings}
            dossiers.append(d)
        # valuación relativa (percentil de EV/Sales ajustado por crecimiento; menor = mejor)
        ranked = sorted(evs, key=evs.get)
        for d in dossiers:
            if d.company.id in evs:
                pct = ranked.index(d.company.id) / max(len(ranked) - 1, 1)
                d.l0["valuation_rel"] = round(10 * (1 - pct), 2)
            else:
                d.l0["valuation_rel"] = None
            v = d.l0["valuation_rel"] if d.l0["valuation_rel"] is not None else 5.0
            d.l0["prescore"] = round(0.40 * d.l0["bottleneck"] + 0.20 * d.l0["exposure_prior"]
                                     + 0.20 * d.l0["financial_quality"] + 0.20 * v, 2)
        passed = sorted([d for d in dossiers if d.l0["pass"]], key=lambda d: -d.l0["prescore"])
        promoted = {d.company.id for d in passed[: c0["max_promoted"]]} | holdings
        for d in dossiers:
            d.l0["promoted"] = d.company.id in promoted
            self._state(d, "L0")
        return dossiers

    # ================================================================== L1
    def triage(self, dossiers: list[Dossier]) -> list[Dossier]:
        c1 = self.cfg["funnel"]["L1"]
        todo = [d for d in dossiers if d.l0.get("promoted")]
        for d in todo:
            pack = company_pack(self.ctx.session, d.company, self.m, self.reg, self.ctx.decision_date, compact=True)
            pack["l0"] = d.l0
            d.agents["triage"] = run_agent(self.ctx, "triage", d.company.id,
                                           "Hacé el triage de esta empresa para decidir si merece análisis profundo.",
                                           pack, hash_view(pack))
            d.tier = "L1"
            self._state(d, "L1")
        holdings = {d.company.id for d in dossiers if d.l0.get("holding")}
        if self.ctx.dry_run:
            # sin salidas reales: peor caso = se promueve el tope de la capa (por pre-score) + cartera
            ok = sorted(todo, key=lambda d: -d.l0["prescore"])
        else:
            ok = [d for d in todo if d.out("triage") and d.out("triage").recommended_next == "L2"
                  and d.out("triage").priority >= c1["min_priority"]]
            ok.sort(key=lambda d: -(d.out("triage").priority + 0.3 * d.out("triage").uncertainty))
        top = ok[: c1["max_promoted"]]
        return top + [d for d in todo if d.company.id in holdings and d not in top]

    # ================================================================== L2
    def standard(self, dossiers: list[Dossier]) -> list[Dossier]:
        for d in dossiers:
            self._l2(d)
        c2 = self.cfg["funnel"]["L2"]
        if self.ctx.dry_run:
            top = sorted(dossiers, key=lambda d: -d.l0.get("prescore", 0))[: c2["max_promoted"]]
        else:
            scored = [d for d in dossiers if d.scorecard]
            scored.sort(key=lambda d: -d.scorecard.total)
            top = [d for d in scored if d.scorecard.total >= c2["min_score"]][: c2["max_promoted"]]
        if self.cfg["funnel"]["L3"].get("always_include_holdings"):
            top += [d for d in dossiers if d.l0.get("holding") and d not in top]
        return top

    def _l2(self, d: Dossier) -> None:
        c, s = d.company, self.ctx.session
        d.pack = company_pack(s, c, self.m, self.reg, self.ctx.decision_date)
        if d.out("triage"):
            d.pack["triage"] = d.out("triage").model_dump()
        hv = hash_view(d.pack)
        d.agents["industrial"] = run_agent(self.ctx, "industrial", c.id,
                                           "Ubicá la empresa en la cadena y evaluá su vínculo con los cuellos de botella.", d.pack, hv)
        d.agents["fundamental"] = run_agent(self.ctx, "fundamental", c.id,
                                            "Analizá fundamentales, exposición económica real y red flags.", d.pack, hv)
        d.agents["catalyst"] = run_agent(self.ctx, "catalyst", c.id,
                                         "Identificá catalizadores (positivos y negativos) para 12-36 meses.", d.pack, hv)
        vpack = dict(d.pack)
        for k in ("fundamental", "industrial"):
            if d.out(k):
                vpack[k] = d.out(k).model_dump()
        d.agents["valuation"] = run_agent(self.ctx, "valuation", c.id,
                                          "Proponé supuestos bear/base/bull a 3 años.", vpack, hash_view(vpack))
        self._use_scenarios(d, d.out("valuation"), "valuation")
        d.scenarios_initial = d.scenarios
        epack = dict(vpack)
        if d.scenarios:
            epack["scenario_results"] = _scenario_summary(d.scenarios)
        if d.out("valuation"):
            epack["valuation_assumptions"] = d.out("valuation").model_dump()
        d.agents["expectations"] = run_agent(self.ctx, "expectations", c.id,
                                             "Evaluá qué está incorporado en el precio vs nuestro base case.", epack, hash_view(epack))
        d.tier = "L2"
        if self.ctx.dry_run:
            return
        self._auto_checks(d)
        self._detect_disagreements(d)
        self._score(d, "L2")

    def _use_scenarios(self, d: Dossier, v: S.ValuationOutput | None, author: str) -> bool:
        """Calcula escenarios con los supuestos de `author`; si son válidos pasan a ser los EN USO y sus checks
        reemplazan a los anteriores. Si no, se conservan los vigentes y se agrega por qué se rechazó el nuevo set."""
        res, checks = self._scenarios(d, v, author)
        if res is None:
            d.scenario_checks = d.scenario_checks + checks
            return False
        d.scenarios, normalized = res
        d.scenario_source = (author, normalized)
        d.scenario_checks = checks
        return True

    def _scenarios(self, d: Dossier, v: S.ValuationOutput | None,
                   author: str) -> tuple[tuple[ScenarioSet, list[dict]] | None, list[str]]:
        if v is None:
            return None, []
        if d.fundamentals is None or d.market is None:
            return None, [f"{author}: escenarios no calculables (faltan fundamentales, precio o moneda consistente)"]
        sc = list(v.scenarios)
        names = sorted(x.name for x in sc)
        if names != ["base", "bear", "bull"]:
            return None, [f"{author} devolvió escenarios {names}: inválido, se descarta"]
        tot = sum(x.probability for x in sc)
        if tot <= 0:
            return None, [f"{author}: probabilidades no positivas, se descarta"]
        checks = [f"{author}: probabilidades sumaban {tot:.2f}: normalizadas"] if abs(tot - 1) > 0.02 else []
        sc = [x.model_copy(update={"probability": x.probability / tot, "author": author}) for x in sc]
        try:
            ss = run_scenarios(sc, d.fundamentals, d.market)
        except ValueError as e:
            return None, checks + [f"{author}: error en escenarios: {e}"]
        return (ss, [x.model_dump() for x in sc]), checks

    # ================================================================== L3
    def deep(self, dossiers: list[Dossier]) -> list[Dossier]:
        for d in dossiers:
            c = d.company
            dossier_pack = self._dossier_pack(d)
            hv = hash_view(dossier_pack)
            d.agents["bear"] = run_agent(self.ctx, "bear", c.id,
                                         "Construí el bear case más fuerte posible con evidencia.", dossier_pack, hv)
            if (d.out("bear") and d.out("valuation")) or self.ctx.dry_run:   # dry-run: peor caso, se estima
                rpack = dict(dossier_pack)
                rpack["bear_case"] = d.out("bear").model_dump() if d.out("bear") else None
                d.agents["valuation_rebuttal"] = run_agent(
                    self.ctx, "valuation_rebuttal", c.id,
                    "REVISIÓN: incorporá los argumentos válidos del Bear Case Analyst y re-proponé los tres escenarios. "
                    "Explicá qué aceptaste y qué rechazaste.", rpack, hash_view(rpack))
                if not self._use_scenarios(d, d.out("valuation_rebuttal"), "valuation_rebuttal") and \
                        d.out("valuation_rebuttal") is not None:
                    d.scenario_checks = d.scenario_checks + ["rebuttal inválido: se mantienen los escenarios de L2"]
            if self.ctx.dry_run:
                d.agents["auditor"] = run_agent(self.ctx, "auditor", c.id, "Auditá el dossier completo.",
                                                self._dossier_pack(d))
                d.tier = "L3"
                continue
            self._auto_checks(d)
            apack = self._dossier_pack(d)
            apack["automatic_checks"] = d.auto_checks
            d.agents["auditor"] = run_agent(self.ctx, "auditor", c.id,
                                            "Auditá el dossier completo: fuentes, cálculos, fechas y consistencia.",
                                            apack, hash_view(apack))
            d.tier = "L3"
            self._detect_disagreements(d)
            self._score(d, "L3")
        return dossiers

    # ================================================================== comité
    def committee(self, dossiers: list[Dossier], report: CycleReport, scored: list[Dossier] | None = None) -> None:
        """`dossiers`: candidatos que llegaron al comité (los ve el PM). `scored`: TODOS los dossiers con score de
        la corrida; las posiciones en cartera que no llegaron al comité igual se evalúan con su scorecard."""
        s = self.ctx.session
        current = ledger.holdings(s, self.portfolio_name)
        candidates = [d for d in dossiers if d.scorecard]
        extra = [d for d in (scored or []) if d.scorecard and d.company.id in current and d not in candidates]
        quotes = self._quotes([d.company for d in candidates + extra] +
                              [self.u.companies[k] for k in current if k in self.u.companies])
        try:
            cur_nav = ledger.nav(s, self.portfolio_name, quotes)
            cur_w = {k: q * quotes[k].price / cur_nav for k, q in current.items()}
        except ValueError as e:
            report.notes.append(f"no se puede valuar la cartera actual: {e}")
            return
        if not candidates and not current:
            report.notes.append("comité: sin candidatos con score")
            return
        pack = {"current_portfolio_weights": cur_w, "cash_weight": 1 - sum(cur_w.values()),
                "constraints": {k: self.cfg["portfolio"][k] for k in
                                ("max_positions", "min_positions", "max_weight", "max_weight_per_bottleneck",
                                 "max_weight_per_layer", "min_cash")},
                "candidates": [self._dossier_pack(d, brief=True) for d in candidates]}
        res = run_agent(self.ctx, "portfolio_manager", "portfolio",
                        "Decidí la cartera modelo para este ciclo.", pack, hash_view(pack))
        if not res.ok:
            report.notes.append(f"comité no ejecutado: {res.status} {res.error or ''}")
            return
        pm: S.PortfolioOutput = res.output
        meta, incomplete_held = {}, set()
        for d in candidates + extra:
            cid = d.company.id
            kills = list(d.scorecard.kill_switches)
            if d.tier != "L3":
                kills.append("K9: no completó L3 (bear case + auditoría) — no puede entrar a cartera")
            eligible = not kills
            if kills and cur_w.get(cid, 0) > 0 and all(k.startswith("K9") for k in kills):
                # análisis INCOMPLETO de una posición (error, presupuesto, --tiers sin L3): no es evidencia
                # negativa -> no se vende por omisión, pero tampoco se aumenta
                eligible = True
                incomplete_held.add(cid)
            meta[cid] = PositionMeta(
                company_id=cid, layer=self.m.nodes[d.company.primary_node].layer,
                primary_bottleneck=_primary_bottleneck(d, self.reg), eligible=eligible,
                speculative=d.company.status == "candidate" or "speculative" in d.company.tags, kill_reasons=kills)
        by_id = {d.company.id: d for d in candidates + extra}
        pm_dec = {x.company_id: x for x in pm.decisions if x.company_id in by_id}
        for x in pm.decisions:
            if x.company_id not in by_id:
                report.notes.append(f"PM decidió sobre {x.company_id} sin dossier: ignorado")
        proposed = {k: x.target_weight for k, x in pm_dec.items() if x.action not in ("SELL", "AVOID")}
        # posiciones actuales con dossier pero no tratadas por el PM: se mantienen (sujeto a restricciones)
        for k, w in cur_w.items():
            if k not in pm_dec and k in meta:
                proposed.setdefault(k, w)
        notes_by: dict[str, list[str]] = {}
        for k in sorted(incomplete_held):
            if k in proposed:
                txt = f"{k}: análisis incompleto ({'; '.join(meta[k].kill_reasons)}) -> sin aumentos"
                if proposed[k] > cur_w[k] + 1e-9:
                    proposed[k] = cur_w[k]
                    txt += f", se mantiene {cur_w[k]:.1%}"
                notes_by.setdefault(k, []).append(txt + " (revisar)")
        targets, notes = enforce_constraints(proposed, meta, notes_by=notes_by)
        notes = [n for k in sorted(incomplete_held) for n in notes_by.get(k, [])[:1]] + notes
        # posiciones actuales SIN dossier en esta corrida: nunca se venden por omisión; se mantienen y se avisa
        unanalyzed = {k: w for k, w in cur_w.items() if k not in meta}
        for k, w in unanalyzed.items():
            targets[k] = round(w, 4)
            notes.append(f"{k}: en cartera sin análisis en esta corrida -> se mantiene {w:.1%} (revisar)")
        invest_max = 1 - self.cfg["portfolio"]["min_cash"]
        tot = sum(targets.values())
        if tot > invest_max:
            free = [k for k in targets if k not in unanalyzed]
            room = max(invest_max - sum(unanalyzed.values()), 0.0)
            f_sum = sum(targets[k] for k in free)
            txt = f"total {tot:.1%} > {invest_max:.0%}: se escalan las posiciones analizadas"
            for k in free:
                targets[k] = round(targets[k] * room / f_sum, 4) if f_sum else 0.0
                notes_by.setdefault(k, []).append(txt)
            notes.append(txt)
        report.constraint_notes = notes
        # Una recomendación por cada empresa con dossier cuyo peso decide este comité: las del PM y también las
        # posiciones que el PM no mencionó pero cuyo peso cambia (venta por kill switch, recorte por topes).
        rec_ids = {}
        band = ledger.min_trade_fraction()
        for cid in sorted(set(pm_dec) | ({k for k in by_id if k in cur_w or k in targets})):
            d, x = by_id[cid], pm_dec.get(cid)
            cur, final_w = cur_w.get(cid, 0.0), targets.get(cid, 0.0)
            final_action = _final_action(cur, final_w, band)
            if x is None and final_action in ("HOLD", "AVOID"):
                continue
            q = quotes.get(cid)
            my_notes = list(dict.fromkeys(notes_by.get(cid, [])))
            rec = Recommendation(
                pipeline_run_id=self.ctx.pipeline_run_id, company_id=cid, action=final_action,
                pm_action=x.action if x else None, pm_target_weight=x.target_weight if x else None,
                constraint_notes=my_notes, target_weight=final_w, conviction=x.conviction if x else "low",
                price_at_decision=q.price if q else None, price_time=q.time if q else None,
                price_source=q.source if q else None,
                thesis=x.thesis if x else "Sin decisión explícita del PM: el peso lo fijan las reglas del código "
                                          "(ver constraint_notes).",
                catalysts=x.catalysts if x else [], risks=x.risks if x else [],
                scenario_id=d.scenario_id, score_id=d.score_id, data_cutoff=self.ctx.decision_date,
                invalidation_triggers=x.invalidation_triggers if x else [],
                sources=sorted({u for a in d.agents.values() for u in a.web_sources})[:50], mode="simulated")
            s.add(rec)
            s.flush()
            rec_ids[cid] = rec.id
            report.decisions.append({"company": cid, "action": final_action, "pm_action": x.action if x else None,
                                     "pm_weight": x.target_weight if x else None, "final_weight": final_w,
                                     "conviction": rec.conviction, "thesis": rec.thesis, "constraint_notes": my_notes})
        missing_q = [k for k in set(targets) | set(current) if k not in quotes]
        if missing_q:
            report.notes.append(f"sin cotización para {missing_q}: no se ejecuta rebalanceo simulado")
            return
        trades = ledger.rebalance(s, self.portfolio_name, targets, quotes, rec_ids)
        report.trades = [{"symbol": t.symbol, "side": t.side, "qty": round(t.quantity, 4), "price": t.price,
                          "price_source": t.price_source, "price_time": t.price_time.isoformat()} for t in trades]
        ledger.mark_to_market(s, self.portfolio_name, self.ctx.decision_date, quotes)

    # ================================================================== helpers
    def _quotes(self, companies: list[Company]) -> dict[str, ledger.Quote]:
        out = {}
        for c in companies:
            snap = repo.latest_snapshot(self.ctx.session, c.id, self.eod)
            if snap and snap.price:
                adv = snap.adv_usd_20d or 0
                liq = "large" if adv > 50e6 else "mid" if adv > 5e6 else "small"
                out[c.id] = ledger.Quote(price=snap.price, time=as_utc(snap.as_of), source=snap.source, liquidity=liq)
        return out

    def _dossier_pack(self, d: Dossier, brief: bool = False) -> dict:
        p: dict = {"company_id": d.company.id, "name": d.company.name,
                   "score": None if not d.scorecard else {
                       "total": d.scorecard.total, "eligible": d.scorecard.eligible,
                       "kill_switches": d.scorecard.kill_switches, "alerts": d.scorecard.alerts,
                       "factors": {k: {"score": v.score, "rationale": v.rationale} for k, v in d.scorecard.factors.items()},
                       "confidence": d.scorecard.confidence},
                   "scenarios": _scenario_summary(d.scenarios) if d.scenarios else None,
                   "layer": self.m.nodes[d.company.primary_node].layer,
                   "primary_bottleneck": _primary_bottleneck(d, self.reg),
                   "disagreements": d.disagreements}
        if brief:
            for k in ("bear", "auditor"):
                if d.out(k):
                    o = d.out(k)
                    p[k] = ({"overall_view": o.overall_view,
                             "top_points": [b.argument for b in o.bear_points if b.severity in ("high", "thesis_killer")][:4]}
                            if k == "bear" else {"verdict": o.verdict, "issues": [i.description for i in o.issues
                                                                                  if i.severity != "minor"][:4]})
            if d.out("catalyst"):
                p["catalysts"] = [f"{c.expected_date}: {c.description} ({c.direction})" for c in d.out("catalyst").catalysts][:5]
            return p
        p["data_pack"] = d.pack
        for k in ("industrial", "fundamental", "catalyst", "valuation", "expectations", "bear", "valuation_rebuttal"):
            if d.out(k):
                p[k] = d.out(k).model_dump()
        return p

    def _auto_checks(self, d: Dossier) -> None:
        """Checks automáticos recalculados desde cero en cada capa (no arrastra checks de escenarios viejos)."""
        chk = list(d.scenario_checks)
        if d.market:
            try:
                age = (self.eod - datetime.fromisoformat(d.market.price_time)).days
                bucket = next((b for b in (30, 7, 3) if age > b), None)   # por tramos: estable día a día
                if bucket:
                    chk.append(f"precio con más de {bucket} días de antigüedad")
            except ValueError:
                chk.append("timestamp de precio ilegible")
        else:
            chk.append("sin precio de mercado vigente")
        if d.fundamentals is None:
            chk.append("sin fundamentales")
        else:
            if d.fundamentals.data_quality != "filing":
                chk.append(f"calidad de datos: {d.fundamentals.data_quality}")
            if d.fundamentals.missing:
                chk.append(f"métricas faltantes: {d.fundamentals.missing}")
        if d.scenarios:
            chk += d.scenarios.warnings
            r = {x.name: x.returns[36] for x in d.scenarios.results}
            if None not in r.values() and not (r["bear"] <= r["base"] <= r["bull"]):
                chk.append(f"retornos 36m no monótonos bear<=base<=bull: {r}")
        n_ev = n_src = 0
        for a in d.agents.values():
            if not a.ok:
                continue
            for ev in _walk_evidence(a.output.model_dump()):
                n_ev += 1
                n_src += bool(ev.get("source_url"))
        if n_ev and n_src / n_ev < 0.5:
            chk.append(f"solo {n_src}/{n_ev} evidencias con fuente")
        d.auto_checks = list(dict.fromkeys(chk))

    def _detect_disagreements(self, d: Dossier) -> None:
        tri, fun, bear, exp, aud = (d.out("triage"), d.out("fundamental"), d.out("bear"), d.out("expectations"),
                                    d.out("auditor"))
        found = []
        if tri and fun and tri.estimated_exposure_pct_revenue is not None and fun.exposure_pct_revenue is not None:
            if abs(tri.estimated_exposure_pct_revenue - fun.exposure_pct_revenue) > 30:
                found.append({"topic": "exposición % de ingresos",
                              "positions": {"triage": tri.estimated_exposure_pct_revenue,
                                            "fundamental": fun.exposure_pct_revenue},
                              "resolving_evidence": "segmentos reportados en el último 10-K/20-F"})
        if exp and d.scenarios and exp.implied_vs_base == "market_more_optimistic":
            er = d.scenarios.expected_return.get(36)
            if er is not None and er > 0.5:
                found.append({"topic": "expectativas vs upside calculado",
                              "positions": {"expectations": "mercado más optimista que el base",
                                            "valuation": f"retorno esperado 36m {er:+.0%}"},
                              "resolving_evidence": "reconciliar supuestos del reverse DCF con el base case"})
        if bear:
            for b in bear.bear_points:
                if b.severity == "thesis_killer":
                    found.append({"topic": b.argument[:300], "positions": {"bear": "thesis_killer",
                                  "fundamental": fun.business_quality_summary[:200] if fun else None},
                                  "resolving_evidence": b.what_would_resolve_it})
        if aud:
            for t in aud.disagreements_to_log:
                found.append({"topic": t[:300], "positions": {"auditor": "registrada"}, "resolving_evidence": None})
        seen = {x["topic"] for x in d.disagreements}
        for x in found:
            if x["topic"] in seen:
                continue
            d.disagreements.append(x)
            self.ctx.session.add(Disagreement(pipeline_run_id=self.ctx.pipeline_run_id, company_id=d.company.id,
                                              topic=x["topic"], positions=x["positions"],
                                              resolving_evidence=x["resolving_evidence"]))

    def _score(self, d: Dossier, tier: str) -> None:
        c = d.company
        crit = max(self.crit.get(n, 0.0) for n in c.nodes)
        b = score_bottleneck(company_bottlenecks(c.nodes, self.reg), crit)
        ind = d.out("industrial")
        if ind:
            b = FactorScore(score=max(0, min(10, b.score + ind.bottleneck_adjustment)), method="hybrid",
                            rationale=f"{b.rationale}; ajuste industrial {ind.bottleneck_adjustment:+} ({ind.adjustment_rationale[:200]})",
                            evidence=b.evidence + [e.source_url for e in ind.evidence if e.source_url][:3], missing=b.missing)
        factors = {"bottleneck_importance": b, "valuation_asymmetry": score_valuation(d.scenarios)}
        factors["financial_quality"] = (score_financial_quality(d.fundamentals) if d.fundamentals
                                        else FactorScore(score=0, rationale="sin datos", missing=["fundamentales"]))
        fun, cat, exp = d.out("fundamental"), d.out("catalyst"), d.out("expectations")
        if fun:
            factors["economic_capture"] = _from_rubric(fun.economic_capture)
        if cat:
            factors["catalysts"] = _from_rubric(cat.catalysts_score)
        snap = repo.latest_snapshot(self.ctx.session, c.id, self.eod)
        proxy = edge_proxy(snap.market_cap_usd if snap else None, c.is_us_listed, None)
        if exp:
            a = _from_rubric(exp.informational_edge)
            factors["informational_edge"] = FactorScore(score=round(0.5 * proxy.score + 0.5 * a.score, 2), method="hybrid",
                                                        rationale=f"proxy {proxy.score} ({proxy.rationale}); agente {a.score}",
                                                        evidence=a.evidence, missing=a.missing)
        else:
            factors["informational_edge"] = proxy
        red = fun.red_flags.model_dump() if fun else {}
        aud = d.out("auditor")
        # análisis incompleto = bloqueo (fail-closed): sin red flags evaluadas o sin auditoría no hay cartera
        incomplete = []
        if fun is None:
            incomplete.append(f"K9: sin evaluación fundamental válida ({_status(d, 'fundamental')}): "
                              "going concern / financiamiento no evaluados")
        if d.scenarios is None:
            incomplete.append(f"K9: sin escenarios de valuación válidos ({_status(d, 'valuation')}): "
                              "K5/K6 no evaluables")
        if tier == "L3" and d.out("bear") is None:
            incomplete.append(f"K9: sin bear case ({_status(d, 'bear')})")
        if tier == "L3" and aud is None:
            incomplete.append(f"K9: sin veredicto del Research Auditor ({_status(d, 'auditor')})")
        sc = combine(c.id, factors, fundamentals=d.fundamentals, scenarios=d.scenarios,
                     adv_usd=snap.adv_usd_20d if snap else None, red_flags=red,
                     alerts_in=red.get("other", []) if fun else [], n_open_disagreements=len(d.disagreements),
                     audit_verdict=aud.verdict if aud else None,
                     is_candidate=c.status == "candidate" or "speculative" in c.tags, incomplete=incomplete)
        d.scorecard = sc
        s = self.ctx.session
        if d.scenarios is None:
            d.scenario_id = None
        elif d.scenario_source is not None and d.scenario_source != d.scenario_row_source:
            author, assumptions = d.scenario_source        # supuestos y resultados del MISMO set
            row = ScenarioRow(company_id=c.id, author=author, pipeline_run_id=self.ctx.pipeline_run_id,
                              price_at_creation=d.market.price if d.market else None,
                              price_source=f"{d.market.price_source}@{d.market.price_time}" if d.market else None,
                              assumptions={"scenarios": assumptions},
                              results=json.loads(d.scenarios.model_dump_json()),
                              expected_return={str(k): v for k, v in d.scenarios.expected_return.items()},
                              prob_loss=d.scenarios.prob_permanent_loss,
                              probability_basis=d.scenarios.probability_basis)
            s.add(row)
            s.flush()
            d.scenario_id = row.id
            d.scenario_row_source = d.scenario_source
        srow = ScoreRow(pipeline_run_id=self.ctx.pipeline_run_id, company_id=c.id, tier=tier, total=sc.total,
                        total_uncapped=sc.total_uncapped,
                        factors={k: v.model_dump() for k, v in sc.factors.items()}, caps_applied=sc.caps_applied,
                        kill_switches=sc.kill_switches, alerts=sc.alerts, missing_data=sc.missing_data,
                        confidence=sc.confidence, eligible=sc.eligible, scenario_id=d.scenario_id)
        s.add(srow)
        s.flush()
        d.score_id = srow.id
        self._state(d, tier)

    def _state(self, d: Dossier, tier: str) -> None:
        if self.ctx.dry_run:              # un dry-run no altera el estado persistido del embudo
            return
        self._state_calls.append((d, tier))

    def _write_state(self, d: Dossier, tier: str) -> None:
        s = self.ctx.session
        st = s.get(FunnelState, d.company.id) or FunnelState(company_id=d.company.id, tier_reached=tier)
        order = {t: i for i, t in enumerate(TIERS)}
        if tier == "L0" or order.get(tier, 0) >= order.get(st.tier_reached, 0):
            st.tier_reached = tier
        st.l0_pass = d.l0.get("pass")
        st.l0_reasons = d.l0.get("reasons", [])
        tri = d.out("triage")
        st.priority = tri.priority if tri else d.l0.get("prescore")
        st.last_score = d.scorecard.total if d.scorecard else st.last_score
        st.last_updated = datetime.now(timezone.utc)
        s.merge(st)

    # ================================================================== ciclo completo
    def run(self, tiers: tuple[str, ...] = TIERS, only: list[str] | None = None) -> CycleReport:
        rep = CycleReport(run_id=self.ctx.pipeline_run_id, started_at=datetime.now(timezone.utc).isoformat(),
                          tiers_run=list(tiers))
        run_row = None
        if not self.ctx.dry_run:
            run_row = PipelineRun(id=self.ctx.pipeline_run_id, status="running", mode=self.mode,
                                  decision_date=self.ctx.decision_date, tiers=list(tiers))
            self.ctx.session.add(run_row)
            self.ctx.session.flush()
        held = set(ledger.holdings(self.ctx.session, self.portfolio_name))
        comps = [c for c in self.u.companies.values() if only is None or c.id in only or c.id in held]
        if only is not None and held - set(only):
            rep.notes.append(f"--only: se agregaron las posiciones en cartera {sorted(held - set(only))} "
                             "(la cartera siempre se re-analiza; nunca se vende por omisión)")
        dossiers = self.screen(comps)
        rep.counts["L0_screened"] = len(dossiers)
        rep.counts["L0_passed"] = sum(d.l0["pass"] for d in dossiers)
        rep.counts["L0_promoted"] = sum(d.l0.get("promoted", False) for d in dossiers)
        rep.l0_table = sorted([{"company": d.company.id, **d.l0} for d in dossiers], key=lambda x: -x["prescore"])
        stage = [d for d in dossiers if d.l0.get("promoted")]
        if "L1" in tiers:
            stage = self.triage(dossiers)
            rep.counts["L1_promoted"] = len(stage)
        if "L2" in tiers:
            stage = self.standard(stage)
            rep.counts["L2_promoted"] = len(stage)
        if "L3" in tiers:
            stage = self.deep(stage)
            rep.counts["L3_done"] = len(stage)
        rep.scores = sorted([{"company": d.company.id, "tier": d.tier, "total": d.scorecard.total,
                              "eligible": d.scorecard.eligible, "kill_switches": d.scorecard.kill_switches,
                              "confidence": d.scorecard.confidence,
                              "er36": d.scenarios.expected_return.get(36) if d.scenarios else None}
                             for d in dossiers if d.scorecard], key=lambda x: -x["total"])
        if "committee" in tiers and not self.ctx.dry_run:
            self.committee(stage, rep, scored=dossiers)
        elif "committee" in tiers and stage:
            run_agent(self.ctx, "portfolio_manager", "portfolio", "Decidí la cartera modelo para este ciclo.",
                      {"candidates": [self._dossier_pack(d, brief=True) for d in stage]})
        for d, tier in self._state_calls:
            self._write_state(d, tier)
        if run_row is not None:
            run_row.status, run_row.finished_at = "completed", datetime.now(timezone.utc)
            self.ctx.session.flush()
        rep.agent_log = self.ctx.log
        rep.cost_usd = round(self.ctx.budget.spent_run, 4)
        rep.est_cost_usd = round(sum(x.get("est_cost", 0) for x in self.ctx.log), 4)
        rep.mode, rep.dry_run = self.mode, self.ctx.dry_run
        bind = self.ctx.session.get_bind()
        rep.db_url = str(bind.url) if bind is not None else None
        out = self.runs_dir
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{rep.run_id}.json").write_text(json.dumps(rep.__dict__, indent=1, default=str), encoding="utf-8")
        return rep


# ====================================================================== utilidades
def _from_rubric(r: S.RubricScore) -> FactorScore:
    lo, hi = (float(x) for x in r.rubric_band.split("-"))
    score = min(max(r.score, lo), hi)          # el puntaje debe caer en la banda declarada
    note = "" if score == r.score else f" [ajustado a la banda {r.rubric_band}]"
    return FactorScore(score=score, method="agent", rationale=r.rationale + note,
                       evidence=[e.source_url or e.claim[:120] for e in r.evidence], missing=r.missing_information)


def _final_action(cur: float, final: float, band: float | None = None) -> str:
    """Acción realmente ejecutada (después de restricciones), no la propuesta del PM. La banda HOLD es la misma
    que usa el rebalanceo para no operar (ledger.min_trade_fraction): HOLD nunca genera una operación."""
    band = ledger.min_trade_fraction() if band is None else band
    if final <= 0:
        return "SELL" if cur > 0 else "AVOID"
    if cur <= 0:
        return "BUY"
    if final >= cur + band:
        return "ADD"
    if final <= cur - band:
        return "TRIM"
    return "HOLD"


def _status(d: Dossier, agent: str) -> str:
    r = d.agents.get(agent)
    return "no ejecutado" if r is None else r.status


def _scenario_summary(ss: ScenarioSet) -> dict:
    return {"price0": ss.price0, "expected_return": ss.expected_return, "prob_loss": ss.prob_loss,
            "prob_permanent_loss": ss.prob_permanent_loss, "upside_downside_ratio": ss.upside_downside_ratio,
            "warnings": ss.warnings,
            "by_scenario": {r.name: {"p": round(r.probability, 3), "targets": r.price_target, "returns": r.returns,
                                     "financing_need": r.financing_need,
                                     "dilution_from_financing": r.dilution_from_financing} for r in ss.results}}


def _primary_bottleneck(d: Dossier, reg: dict[str, BottleneckRecord]) -> str | None:
    bs = company_bottlenecks(d.company.nodes, reg)
    return max(bs, key=lambda b: b["severity"])["id"] if bs else None


def _walk_evidence(x):  # noqa: ANN001, ANN202
    if isinstance(x, dict):
        if "claim" in x and "source_url" in x:
            yield x
        for v in x.values():
            yield from _walk_evidence(v)
    elif isinstance(x, list):
        for v in x:
            yield from _walk_evidence(v)
