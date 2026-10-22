"""Análisis de calls con potencial de multiplicación de capital.

Modelos explícitos (y sus limitaciones):
  1. Risk-neutral lognormal (Black-Scholes) con la IV de cada contrato. Da probabilidades IMPLÍCITAS del
     mercado bajo medida neutral al riesgo: no son probabilidades del mundo real (ignoran prima de riesgo,
     colas gordas, saltos en earnings, skew entre strikes más allá de usar la IV de cada strike).
  2. Mezcla de escenarios (subjetiva): para cada escenario bear/base/bull, S_T ~ lognormal con mediana en el
     precio objetivo del escenario interpolado a la fecha de vencimiento y dispersión `dispersion_frac * IV*sqrt(T)`.
     Hereda la subjetividad de las probabilidades de escenarios.
Reglas: sin cadena de opciones reciente y verificable (timestamp < max_age_hours) no se evalúa nada.
Siempre se compara con comprar la acción.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from scipy.stats import norm

from qa.providers.base import OptionChain
from qa.valuation.core import ScenarioSet


# --------------------------------------------------------------------------- Black-Scholes
def _d1d2(S: float, K: float, T: float, r: float, sigma: float) -> tuple[float, float]:
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    return d1, d1 - sigma * math.sqrt(T)


def bs_call(S: float, K: float, T: float, r: float, sigma: float) -> float:
    if T <= 0 or sigma <= 0:
        return max(S - K, 0.0)
    d1, d2 = _d1d2(S, K, T, r, sigma)
    return S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)


def bs_greeks(S: float, K: float, T: float, r: float, sigma: float) -> dict[str, float]:
    d1, d2 = _d1d2(S, K, T, r, sigma)
    theta = (-S * norm.pdf(d1) * sigma / (2 * math.sqrt(T)) - r * K * math.exp(-r * T) * norm.cdf(d2)) / 365
    return {"delta": norm.cdf(d1), "theta_per_day": theta, "vega_per_pt": S * norm.pdf(d1) * math.sqrt(T) / 100,
            "gamma": norm.pdf(d1) / (S * sigma * math.sqrt(T))}


def rn_prob_above(S: float, X: float, T: float, r: float, sigma: float) -> float:
    """P_Q(S_T > X) bajo lognormal risk-neutral."""
    if X <= 0:
        return 1.0
    _, d2 = _d1d2(S, X, T, r, sigma)
    return float(norm.cdf(d2))


# --------------------------------------------------------------------------- mezcla de escenarios
def interpolate_target(price0: float, targets: dict[int, float | None], months: float) -> float | None:
    """Interpolación log-lineal del precio objetivo entre 0, 12, 24 y 36 meses."""
    pts = [(0.0, price0)] + [(float(h), v) for h, v in sorted(targets.items()) if v is not None and v > 0]
    if len(pts) < 2:
        return None
    months = min(max(months, 0.0), pts[-1][0])
    for (m0, p0), (m1, p1) in zip(pts, pts[1:]):
        if m0 <= months <= m1:
            w = (months - m0) / (m1 - m0)
            return math.exp(math.log(p0) * (1 - w) + math.log(p1) * w)
    return pts[-1][1]


def mixture_prob_above(components: list[tuple[float, float, float]], X: float) -> float:
    """components: (peso, mediana, sigma_total). P(S_T > X) para la mezcla de lognormales."""
    if X <= 0:
        return 1.0
    return sum(w * float(norm.sf((math.log(X) - math.log(med)) / s)) for w, med, s in components)


def mixture_expected_call(components: list[tuple[float, float, float]], K: float) -> float:
    """E[max(S_T-K,0)] para mezcla de lognormales con mediana `med` (mu = ln med)."""
    tot = 0.0
    for w, med, s in components:
        mu = math.log(med)
        mean = math.exp(mu + s * s / 2)
        d1 = (mu + s * s - math.log(K)) / s
        d2 = d1 - s
        tot += w * (mean * norm.cdf(d1) - K * norm.cdf(d2))
    return tot


@dataclass
class CallCandidate:
    expiry: date
    strike: float
    bid: float | None
    ask: float | None
    premium: float                    # mid
    spread_pct: float
    open_interest: float | None
    volume: float | None
    iv: float
    T_years: float
    breakeven: float
    breakeven_move: float
    greeks: dict[str, float]
    rn: dict[str, float]              # probabilidades risk-neutral
    subj: dict[str, float]            # probabilidades bajo mezcla de escenarios
    stock_expected_return: float | None
    option_expected_return: float | None
    edge_vs_stock: float | None       # ER opción - ER de posición apalancada equivalente en delta
    catalysts_before_expiry: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)


@dataclass
class OptionsReport:
    underlying: str
    spot: float
    chain_time: datetime
    chain_source: str
    candidates: list[CallCandidate]
    rejected: dict[str, int]
    model_notes: list[str]
    status: str                       # ok | stale_chain | no_scenarios | no_liquid_calls


def analyze_calls(chain: OptionChain, scenarios: ScenarioSet | None, *, r: float = 0.04,
                  catalysts: list[tuple[date, str]] | None = None, max_age_hours: float = 24,
                  min_open_interest: float = 100, max_spread_pct: float = 0.15, min_days: int = 60,
                  dispersion_frac: float = 0.5, now: datetime | None = None,
                  scenario_age_days: float = 0.0) -> OptionsReport:
    """`scenario_age_days`: antigüedad de los escenarios; sus horizontes (12/24/36m) se miden desde su creación."""
    now = now or datetime.now(timezone.utc)
    notes = ["Probabilidades risk-neutral (IV) ≠ probabilidades reales.",
             "Mezcla de escenarios = probabilidades subjetivas del análisis, no calibradas.",
             "No modela saltos por earnings, skew dinámico ni cambios de IV (vega risk).",
             "Precios mid: la ejecución real puede ser peor que el mid, sobre todo con spreads amplios."]
    age_h = (now - chain.retrieved_at).total_seconds() / 3600
    spot = chain.underlying_price
    if age_h > max_age_hours or spot is None:
        return OptionsReport(chain.underlying, spot or 0.0, chain.retrieved_at, chain.source, [], {}, notes,
                             status="stale_chain")
    if scenarios is None:
        notes.append("Sin escenarios: solo se reportan probabilidades risk-neutral; no hay recomendación.")
    rejected = {"puts": 0, "too_short": 0, "illiquid_oi": 0, "wide_spread": 0, "no_quote": 0, "no_iv": 0}
    out: list[CallCandidate] = []
    today = now.date()
    for q in chain.quotes:
        if q.right != "C":
            rejected["puts"] += 1
            continue
        days = (q.expiry - today).days
        if days < min_days:
            rejected["too_short"] += 1
            continue
        if not q.bid or not q.ask or q.ask <= 0 or q.bid <= 0:
            rejected["no_quote"] += 1
            continue
        mid = (q.bid + q.ask) / 2
        spread = (q.ask - q.bid) / mid
        if spread > max_spread_pct:
            rejected["wide_spread"] += 1
            continue
        if (q.open_interest or 0) < min_open_interest:
            rejected["illiquid_oi"] += 1
            continue
        if not q.implied_vol or q.implied_vol <= 0.01:
            rejected["no_iv"] += 1
            continue
        T = days / 365.0
        K, iv = q.strike, q.implied_vol
        rn = {"p_itm": rn_prob_above(spot, K, T, r, iv),
              "p_profit": rn_prob_above(spot, K + mid, T, r, iv),
              "p_2x": rn_prob_above(spot, K + 2 * mid, T, r, iv),
              "p_3x": rn_prob_above(spot, K + 3 * mid, T, r, iv),
              "p_5x": rn_prob_above(spot, K + 5 * mid, T, r, iv)}
        rn["p_total_loss"] = 1 - rn["p_itm"]
        subj, er_opt, er_stock, edge = {}, None, None, None
        greeks = bs_greeks(spot, K, T, r, iv)
        if scenarios is not None:
            comps = []
            for res in scenarios.results:
                # los precios objetivo salen de fundamentals: NO se reescalan con el spot; el horizonte se corre por la edad
                tgt = interpolate_target(scenarios.price0, res.price_target, (days + scenario_age_days) / 30.44)
                if tgt:
                    comps.append((res.probability, tgt, dispersion_frac * iv * math.sqrt(T)))
            if comps:
                subj = {"p_itm": mixture_prob_above(comps, K),
                        "p_2x": mixture_prob_above(comps, K + 2 * mid),
                        "p_3x": mixture_prob_above(comps, K + 3 * mid),
                        "p_5x": mixture_prob_above(comps, K + 5 * mid)}
                subj["p_total_loss"] = 1 - subj["p_itm"]
                exp_payoff = mixture_expected_call(comps, K)
                er_opt = exp_payoff / mid - 1
                exp_spot = sum(w * med * math.exp(s * s / 2) for w, med, s in comps)
                er_stock = exp_spot / spot - 1
                # posición réplica: Ω acciones por unidad de prima, financiando (Ω-1) a la tasa libre de riesgo
                leverage = greeks["delta"] * spot / mid
                edge = er_opt - (leverage * er_stock - (leverage - 1) * (math.exp(r * T) - 1))
        flags = []
        if rn["p_total_loss"] > 0.7:
            flags.append("P(pérdida total) risk-neutral > 70%")
        if spread > 0.08:
            flags.append(f"spread {spread:.0%}")
        if iv > 1.0:
            flags.append(f"IV muy alta {iv:.0%}: el mercado ya descuenta movimientos grandes")
        cats = [f"{d.isoformat()}: {t}" for d, t in (catalysts or []) if today <= d <= q.expiry]
        if not cats:
            flags.append("sin catalizadores identificados antes del vencimiento")
        out.append(CallCandidate(
            expiry=q.expiry, strike=K, bid=q.bid, ask=q.ask, premium=mid, spread_pct=spread,
            open_interest=q.open_interest, volume=q.volume, iv=iv, T_years=T, breakeven=K + mid,
            breakeven_move=(K + mid) / spot - 1, greeks=greeks, rn=rn, subj=subj,
            stock_expected_return=er_stock, option_expected_return=er_opt, edge_vs_stock=edge,
            catalysts_before_expiry=cats, flags=flags))
    out.sort(key=lambda c: (c.edge_vs_stock is None, -(c.edge_vs_stock or 0)))
    status = "ok" if out else "no_liquid_calls"
    if scenarios is None and out:
        status = "no_scenarios"
    return OptionsReport(chain.underlying, spot, chain.retrieved_at, chain.source, out, rejected, notes, status)
