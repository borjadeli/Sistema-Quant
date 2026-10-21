"""Scoring de inversión (0-10 por factor) con rúbricas reproducibles, topes y condiciones eliminatorias.

Factores y método:
  bottleneck_importance  20%  híbrido: regla sobre el registro de bottlenecks (+-2 por agente con evidencia)
  economic_capture       20%  agente con rúbrica + anclas cuantitativas (exposición %, margen bruto)
  valuation_asymmetry    25%  REGLA pura sobre escenarios (retorno esperado, asimetría, pérdida permanente)
  catalysts              15%  agente con rúbrica (catalizadores fechados y verificables)
  financial_quality      10%  REGLA pura sobre métricas derivadas
  informational_edge     10%  híbrido: proxies (tamaño, listado, cobertura) + agente

Principio: una narrativa atractiva no compensa problemas graves. Se aplican topes (`caps`) y
condiciones eliminatorias (`kill switches`) que bloquean la entrada a cartera aunque el puntaje sea alto.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from qa.config import settings
from qa.fundamentals.metrics import Fundamentals
from qa.valuation.core import ScenarioSet

FACTORS = ["bottleneck_importance", "economic_capture", "valuation_asymmetry", "catalysts",
           "financial_quality", "informational_edge"]

# Rúbricas: anclas textuales que reciben los agentes y que documentan las reglas (docs/05_scoring_rubricas.md).
RUBRICS: dict[str, dict[str, str]] = {
    "bottleneck_importance": {
        "9-10": "Escasez ACTUAL verificada con >=2 fuentes primarias independientes; oferta muy concentrada; sin sustituto; duración esperada >2 años.",
        "7-8": "Escasez actual verificada; duración 1-2 años o sustitutos parciales en desarrollo.",
        "5-6": "Escasez PROYECTADA con evidencia creíble, o escasez actual que se está aliviando.",
        "3-4": "Nodo estructuralmente crítico (concentrado/poco sustituible) sin evidencia de escasez.",
        "0-2": "Insumo abundante o commodity; la empresa no está en un nodo restringido.",
    },
    "economic_capture": {
        "9-10": ">50% de ingresos del nodo restringido (reportado), proveedor top-2, evidencia de suba de precios/expansión de margen, capacidad de expansión financiada.",
        "7-8": "30-50% de ingresos expuestos, posición fuerte, márgenes en expansión.",
        "5-6": "15-30% de exposición, o exposición alta con poder de precio limitado (p.ej. manufactura por contrato).",
        "3-4": "<15% de exposición o precios tipo commodity; captura económica diluida.",
        "0-2": "Exposición solo narrativa (menciones de IA sin ingresos atribuibles).",
    },
    "catalysts": {
        "9-10": "Catalizador duro y fechado en <6 meses con impacto material verificable (contrato a firmar, capacidad pre-vendida que entra en operación, resolución regulatoria).",
        "7-8": "Varios catalizadores fechados en 6-12 meses con impacto material probable.",
        "5-6": "Catalizadores plausibles pero sin fecha firme, o impacto incierto.",
        "3-4": "Solo catalizadores genéricos (earnings, ciclo de la industria).",
        "0-2": "Sin catalizadores identificables o catalizadores negativos pendientes.",
    },
    "informational_edge": {
        "9-10": "Cambio fundamental reciente no reflejado en consenso; <5 analistas; listado no-US o complejidad que aleja inversores; nuestro mapa revela una dependencia poco conocida.",
        "7-8": "Cobertura baja (5-10 analistas) o small/mid cap con tesis de cadena de suministro poco difundida.",
        "5-6": "Cobertura media; la tesis se conoce pero hay un ángulo diferencial documentado.",
        "3-4": "Empresa ampliamente cubierta; tesis de consenso.",
        "0-2": "Megacap hiper-cubierta; ventaja informacional improbable.",
    },
}

# Solo `qa demo` lo activa (datos sintéticos etiquetados) para mostrar la cartera simulada; nunca en producción.
ALLOW_SYNTHETIC_FOR_DEMO = False

STATUS_MULT = {"current_shortage": 1.0, "projected_shortage": 0.8, "speculative_hypothesis": 0.5, "easing": 0.45}


class FactorScore(BaseModel):
    score: float = Field(ge=0, le=10)
    rationale: str
    evidence: list[str] = Field(default_factory=list)
    method: str = "rule"                 # rule | agent | hybrid
    missing: list[str] = Field(default_factory=list)


@dataclass
class ScoreCard:
    company_id: str
    factors: dict[str, FactorScore]
    total: float
    total_uncapped: float
    caps_applied: list[str] = field(default_factory=list)
    kill_switches: list[str] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)
    missing_data: list[str] = field(default_factory=list)
    confidence: str = "low"

    @property
    def eligible(self) -> bool:
        return not self.kill_switches


def _clamp(x: float, lo: float = 0.0, hi: float = 10.0) -> float:
    return max(lo, min(hi, x))


# =========================================================================== factores por regla
def score_bottleneck(company_bottlenecks: list[dict], structural_criticality: float) -> FactorScore:
    """Regla: max(severidad * multiplicador de estado) sobre los bottlenecks del nodo de la empresa.

    Sin bottleneck con evidencia, se usa el prior estructural del mapa con tope 4 (rúbrica 3-4).
    """
    if not company_bottlenecks:
        s = min(structural_criticality * 0.5, 4.0)
        return FactorScore(score=round(s, 2), method="rule",
                           rationale=f"sin bottleneck con evidencia; prior estructural del mapa {structural_criticality:.1f} (tope 4)",
                           missing=["evidencia de escasez para los nodos de la empresa"])
    best, why, ev = 0.0, "", []
    for b in company_bottlenecks:
        sev = b.get("severity")
        if sev is None:
            continue
        val = sev * STATUS_MULT.get(b.get("status"), 0.5)
        if b.get("confidence") == "low":
            val *= 0.8
        if val > best:
            best, why = val, f"{b['id']}: severidad {sev} x estado {b.get('status')} (conf {b.get('confidence')})"
            ev = [e.get("source_url", "") for e in b.get("evidence", [])][:3]
    return FactorScore(score=round(_clamp(best), 2), rationale=why or "bottlenecks sin severidad", evidence=ev, method="rule")


def score_valuation(ss: ScenarioSet | None) -> FactorScore:
    """Regla sobre los escenarios.

    s_er: retorno esperado anualizado a 36m r -> 5 + 20*(r - 10%)  (10%->5, 35%->10, -15%->0)
    s_ud: asimetría bull/|bear| a 36m -> 2.5 * ratio (4:1 -> 10)
    score = 0.6*s_er + 0.4*s_ud - 2*[P(pérdida permanente) > 30%]
    """
    if ss is None or ss.expected_return.get(36) is None:
        return FactorScore(score=0, rationale="sin escenarios de valuación", method="rule",
                           missing=["escenarios bear/base/bull"])
    er36 = ss.expected_return[36]
    ann = (1 + er36) ** (1 / 3) - 1 if er36 > -1 else -1.0
    s_er = _clamp(5 + 20 * (ann - 0.10))
    ud = ss.upside_downside_ratio
    if ud is None:   # el bear no pierde: asimetría máxima
        s_ud = 10.0 if (ss.bear_downside_36m or 0) >= 0 else 0.0
    else:
        s_ud = _clamp(2.5 * ud)
    pen = 2.0 if ss.prob_permanent_loss > 0.30 else 0.0
    s = _clamp(0.6 * s_er + 0.4 * s_ud - pen)
    return FactorScore(score=round(s, 2), method="rule",
                       rationale=(f"ER36={er36:+.0%} ({ann:+.1%}/año) -> {s_er:.1f}; asimetría "
                                  f"{'n/a' if ud is None else f'{ud:.1f}x'} -> {s_ud:.1f}; "
                                  f"P(pérdida permanente)={ss.prob_permanent_loss:.0%}"),
                       evidence=ss.warnings)


def score_financial_quality(f: Fundamentals) -> FactorScore:
    """Regla: promedio de sub-puntajes disponibles (cada uno 0-10)."""
    subs, miss = {}, []

    def add(name: str, v: float | None, fn) -> None:  # noqa: ANN001
        if v is None or (isinstance(v, float) and math.isnan(v)):
            miss.append(name)
        else:
            subs[name] = _clamp(fn(v))

    add("gross_margin", f.v("gross_margin"), lambda x: 20 * x)                    # 50% -> 10
    add("fcf_margin", f.v("fcf_margin"), lambda x: 5 + 33 * x)                   # 15% -> 10, -15% -> 0
    add("roic", f.v("roic"), lambda x: 5 + 25 * (x - 0.10))                       # 30% -> 10
    nd = f.v("net_debt_to_ebitda")
    if f.v("net_debt") is not None and f.v("net_debt") <= 0:
        subs["leverage"] = 10.0
    else:
        add("leverage", nd, lambda x: 10 - 2.5 * x)                               # 4x -> 0
    add("dilution", f.v("dilution_yoy"), lambda x: 10 - 100 * max(x, 0))         # 10% -> 0
    runway = f.v("cash_runway_quarters")
    if runway is not None:
        subs["runway"] = _clamp(runway * 10 / 12)                                # 12 trimestres -> 10
    if not subs:
        return FactorScore(score=0, rationale="sin datos financieros", method="rule", missing=miss)
    s = sum(subs.values()) / len(subs)
    # penaliza cobertura incompleta para no premiar la falta de datos
    coverage = len(subs) / 5
    s = s * min(1.0, 0.5 + 0.5 * coverage)
    return FactorScore(score=round(_clamp(s), 2), method="rule",
                       rationale="; ".join(f"{k}={v:.1f}" for k, v in subs.items()), missing=miss)


def edge_proxy(market_cap_usd: float | None, is_us_listed: bool, analyst_count: int | None) -> FactorScore:
    """Proxy cuantitativo de ventaja informacional (el agente puede ajustar +-2 con evidencia)."""
    s, why = 5.0, []
    if market_cap_usd is not None:
        if market_cap_usd < 2e9:
            s += 2; why.append("small cap")
        elif market_cap_usd > 200e9:
            s -= 3; why.append("megacap")
        elif market_cap_usd > 50e9:
            s -= 1.5; why.append("large cap")
    if not is_us_listed:
        s += 1; why.append("listado no-US")
    if analyst_count is not None:
        s += 1.5 if analyst_count < 5 else (-1.5 if analyst_count > 25 else 0)
        why.append(f"{analyst_count} analistas")
    return FactorScore(score=round(_clamp(s), 2), rationale=", ".join(why) or "sin proxies", method="rule")


# =========================================================================== combinación
def combine(company_id: str, factors: dict[str, FactorScore], *, fundamentals: Fundamentals | None = None,
            scenarios: ScenarioSet | None = None, adv_usd: float | None = None,
            red_flags: dict[str, bool] | None = None, alerts_in: list[str] | None = None,
            n_open_disagreements: int = 0, audit_verdict: str | None = None,
            is_candidate: bool = False) -> ScoreCard:
    cfg = settings()["scoring"]
    w = cfg["weights"]
    missing = [f"{k}: factor no evaluado" for k in FACTORS if k not in factors]
    total_raw = sum(w[k] * factors[k].score for k in FACTORS if k in factors)
    total = total_raw
    caps = []
    for c in cfg["caps"]:
        fs = factors.get(c["factor"])
        if fs is not None and fs.score < c["below"] and total > c["max_total"]:
            total = c["max_total"]
            caps.append(f"{c['factor']} < {c['below']} -> total limitado a {c['max_total']}")
    kills, alerts = kill_switches(fundamentals, scenarios, adv_usd, red_flags or {}, audit_verdict, is_candidate)
    alerts += list(alerts_in or [])
    for fs in factors.values():
        missing += fs.missing
    # confianza: calidad de datos, faltantes, discrepancias abiertas
    pts = 3
    if fundamentals is None or fundamentals.data_quality in ("none", "synthetic"):
        pts -= 2
    elif fundamentals.data_quality != "filing":
        pts -= 1
    if len(missing) > 4:
        pts -= 1
    if n_open_disagreements >= 2:
        pts -= 1
    conf = "high" if pts >= 3 else "medium" if pts == 2 else "low"
    return ScoreCard(company_id=company_id, factors=factors, total=round(total, 2), total_uncapped=round(total_raw, 2),
                     caps_applied=caps, kill_switches=kills, alerts=alerts, missing_data=missing, confidence=conf)


def kill_switches(f: Fundamentals | None, ss: ScenarioSet | None, adv_usd: float | None,
                  red_flags: dict[str, bool], audit_verdict: str | None, is_candidate: bool) -> tuple[list[str], list[str]]:
    """Condiciones eliminatorias (bloquean cartera) y alertas (requieren atención, no bloquean)."""
    kills, alerts = [], []
    min_adv = settings()["funnel"]["L0"]["min_adv_usd"]
    if f is None or f.data_quality == "none":
        kills.append("K4: sin datos fundamentales verificables")
    elif f.data_quality == "synthetic":
        if ALLOW_SYNTHETIC_FOR_DEMO:
            alerts.append("DEMO: datos sintéticos (K4 desactivado solo en demo)")
        else:
            kills.append("K4: datos sintéticos — no invertible")
    if red_flags.get("going_concern"):
        kills.append("K1: duda de empresa en marcha (going concern)")
    if f is not None:
        runway = f.v("cash_runway_quarters")
        if runway is not None and runway < 4 and not red_flags.get("financing_committed"):
            kills.append(f"K2: runway de caja {runway:.1f} trimestres sin financiamiento comprometido")
        nd = f.v("net_debt_to_ebitda")
        if nd is not None and nd > 6:
            kills.append(f"K7: deuda neta/EBITDA {nd:.1f}x > 6x")
        ic = f.v("interest_coverage")
        if ic is not None and 0 < ic < 1.5:
            alerts.append(f"cobertura de intereses baja ({ic:.1f}x)")
        dil = f.v("dilution_yoy")
        if dil is not None and dil > 0.08:
            alerts.append(f"dilución elevada {dil:.0%} interanual")
        sbc = f.v("sbc_pct_revenue")
        if sbc is not None and sbc > 0.15:
            alerts.append(f"SBC {sbc:.0%} de ingresos")
        if f.data_quality in ("search_summary", "mixed"):
            alerts.append("datos no verificados contra documento primario")
    if adv_usd is not None and adv_usd < min_adv:
        kills.append(f"K3: liquidez diaria ${adv_usd/1e6:.1f}M < mínimo ${min_adv/1e6:.1f}M")
    if ss is not None:
        er = ss.expected_return.get(24)
        if er is not None and er < 0:
            kills.append(f"K5: retorno esperado 24m negativo ({er:+.0%})")
        if ss.prob_permanent_loss > 0.5:
            kills.append(f"K6: P(pérdida permanente) {ss.prob_permanent_loss:.0%} > 50%")
        if any(r.financing_need > 0 for r in ss.results if r.name == "base"):
            alerts.append("el escenario base requiere financiamiento externo (dilución)")
    if audit_verdict == "fail":
        kills.append("K8: el Research Auditor rechazó el análisis (errores sin resolver)")
    for flag in ("customer_concentration_gt_40", "export_control_exposure", "restatement", "recent_equity_raise"):
        if red_flags.get(flag):
            alerts.append(flag)
    if is_candidate:
        alerts.append("empresa especulativa/pre-ingresos: tamaño máximo reducido")
    return kills, alerts
