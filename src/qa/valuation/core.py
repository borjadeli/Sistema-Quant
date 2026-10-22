"""Valuación: múltiplos con reglas de aplicabilidad, escenarios bear/base/bull, sensibilidad y reverse DCF.

Distinción epistemológica:
  - Los SUPUESTOS de cada escenario (crecimiento, márgenes, múltiplo, probabilidad) son ESTIMACIONES
    con autor y justificación. Las probabilidades son SUBJETIVAS, no estadísticamente calibradas;
    su calibración se mide ex-post (portfolio/calibration.py).
  - Los RESULTADOS (precio objetivo, retorno) son cálculos determinísticos sobre esos supuestos.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from qa.fundamentals.metrics import Fundamentals

HORIZONS_M = (12, 24, 36)
MultipleType = Literal["EV/EBIT", "EV/EBITDA", "EV/Sales", "P/E", "EV/FCF"]


# =========================================================================== mercado / múltiplos
@dataclass
class MarketInputs:
    price: float                        # en moneda de cotización
    shares: float                       # acciones (diluidas preferentemente)
    price_currency: str
    price_time: str                     # ISO timestamp de la cotización
    price_source: str
    fx_report_to_price: float = 1.0     # 1 unidad de moneda de reporte -> moneda de cotización
    adr_ratio: float = 1.0              # acciones subyacentes por ADR

    @property
    def market_cap(self) -> float:
        return self.price * self.shares


@dataclass
class Multiples:
    ev: float | None
    market_cap: float
    values: dict[str, float | None] = field(default_factory=dict)
    not_meaningful: dict[str, str] = field(default_factory=dict)   # múltiplo -> razón


def compute_multiples(f: Fundamentals, mk: MarketInputs) -> Multiples:
    fx = mk.fx_report_to_price
    cash = f.v("cash_total")
    debt = f.v("debt")
    ev = None
    if cash is not None and debt is not None:
        ev = mk.market_cap + (debt - cash) * fx
    out = Multiples(ev=ev, market_cap=mk.market_cap)
    rev, ebitda, ebit = f.v("revenue_ttm"), f.v("ebitda_ttm"), f.v("operating_income_ttm")
    ni, fcf = f.v("net_income_ttm"), f.v("fcf_ttm")

    def put(name: str, num: float | None, den: float | None, need_positive: bool, why: str) -> None:
        if num is None or den is None:
            out.values[name] = None
            out.not_meaningful[name] = "datos faltantes"
        elif need_positive and den <= 0:
            out.values[name] = None
            out.not_meaningful[name] = why
        else:
            out.values[name] = num / (den * fx)

    put("EV/Sales", ev, rev, True, "sin ingresos")
    put("EV/EBITDA", ev, ebitda, True, "EBITDA negativo: múltiplo no significativo")
    put("EV/EBIT", ev, ebit, True, "EBIT negativo: múltiplo no significativo")
    put("P/E", mk.market_cap, ni, True, "pérdidas: P/E no significativo")
    if fcf is not None:
        out.values["FCF_yield"] = fcf * fx / mk.market_cap
    else:
        out.values["FCF_yield"] = None
        out.not_meaningful["FCF_yield"] = "datos faltantes"
    return out


# =========================================================================== escenarios
class ScenarioAssumptions(BaseModel):
    name: Literal["bear", "base", "bull"]
    probability: float = Field(ge=0, le=1)
    revenue_growth: list[float] | None = Field(None, description="crecimiento anual años 1..3")
    revenue_path: list[float] | None = Field(None, description="ingresos absolutos años 1..3 (pre-revenue)")
    ebit_margin: list[float] = Field(description="margen EBIT años 1..3")
    tax_rate: float = 0.21
    da_pct_revenue: float = 0.04
    capex_pct_revenue: float = 0.05
    nwc_pct_incremental_revenue: float = 0.15
    annual_dilution: float = 0.01       # SBC / emisiones no ligadas a financiamiento
    interest_rate: float = 0.05         # costo de la deuda financiera (se paga con caja)
    exit_multiple: float
    exit_multiple_type: MultipleType = "EV/EBIT"
    rationale: str = ""
    author: str = "analyst"

    @model_validator(mode="after")
    def _paths(self) -> "ScenarioAssumptions":
        if (self.revenue_growth is None) == (self.revenue_path is None):
            raise ValueError("definir exactamente uno: revenue_growth o revenue_path")
        n = len(self.revenue_growth or self.revenue_path or [])
        if n != 3 or len(self.ebit_margin) != 3:
            raise ValueError("se requieren 3 años de crecimiento/ingresos y márgenes")
        return self

    @field_validator("ebit_margin")
    @classmethod
    def _margins(cls, v: list[float]) -> list[float]:
        if any(m < -2 or m > 0.8 for m in v):
            raise ValueError("márgenes EBIT fuera de rango razonable [-200%, 80%]")
        return v


class ScenarioResult(BaseModel):
    name: str
    probability: float
    years: list[dict]                       # proyección anual
    price_target: dict[int, float | None]   # horizonte (meses) -> precio
    returns: dict[int, float | None]
    financing_need: float                   # financiamiento externo acumulado requerido (moneda de reporte)
    dilution_from_financing: float          # % acciones nuevas por financiamiento
    notes: list[str] = Field(default_factory=list)


class ScenarioSet(BaseModel):
    base_revenue: float | None
    price0: float
    results: list[ScenarioResult]
    expected_return: dict[int, float | None]
    prob_loss: dict[int, float]             # P(retorno<0) bajo las probabilidades subjetivas
    prob_permanent_loss: float              # P(retorno 36m < -30%)
    bear_downside_36m: float | None
    upside_downside_ratio: float | None
    probability_basis: str = "subjective — no calibrada estadísticamente"
    warnings: list[str] = Field(default_factory=list)


def _metric_for_multiple(kind: str, rev: float, ebit: float, da: float, ni: float, fcf: float) -> float:
    return {"EV/EBIT": ebit, "EV/EBITDA": ebit + da, "EV/Sales": rev, "P/E": ni, "EV/FCF": fcf}[kind]


def balance_inputs(f: Fundamentals) -> tuple[float, float, list[str]]:
    """Caja y deuda iniciales. Nunca se inventa un balance:
    - sin caja reportada (o sin fecha de balance) -> ValueError: la valuación no es calculable;
    - con balance pero sin deuda financiera reportada a esa fecha -> deuda 0 como SUPUESTO EXPLÍCITO (advertido).
    """
    cash, debt = f.v("cash_total"), f.v("debt")
    if cash is None or f.balance_date is None:
        raise ValueError("caja desconocida: no se asume 0 — valuación no calculable hasta tener balance")
    notes = []
    if debt is None:
        debt = 0.0
        notes.append(f"sin deuda financiera reportada al {f.balance_date}: se SUPONE 0 (verificar en el balance)")
    return cash, debt, notes


def run_scenario(a: ScenarioAssumptions, f: Fundamentals, mk: MarketInputs,
                 equity_raise_discount: float = 0.15) -> ScenarioResult:
    """Proyección anual a 3 años y valuación por múltiplo de salida en cada horizonte.

    El FCF proyectado es apalancado: los intereses de la deuda salen de la caja. El valor del equity tiene
    piso 0 (responsabilidad limitada): el peor retorno posible es -100%.
    """
    rev0 = f.v("revenue_ttm")
    if rev0 is None and f.v("revenue_last_q") is not None:
        rev0 = 4 * f.v("revenue_last_q")          # anualización explícita (se advierte en ScenarioSet)
    cash0, debt0, notes = balance_inputs(f)
    shares = mk.shares * mk.adr_ratio  # acciones subyacentes
    fx, adr = mk.fx_report_to_price, mk.adr_ratio
    years, cash, prev_rev = [], cash0, rev0 or 0.0
    fin_need_total, new_shares_fin = 0.0, 0.0
    for t in range(3):
        rev = a.revenue_path[t] if a.revenue_path else prev_rev * (1 + a.revenue_growth[t])
        ebit = rev * a.ebit_margin[t]
        interest = a.interest_rate * debt0
        pretax = ebit - interest
        tax = pretax * a.tax_rate if pretax > 0 else 0.0
        ni = pretax - tax
        da, capex = rev * a.da_pct_revenue, rev * a.capex_pct_revenue
        d_nwc = (rev - prev_rev) * a.nwc_pct_incremental_revenue
        fcf = ni + da - capex - d_nwc                # FCF al accionista (después de intereses e impuestos)
        shares *= (1 + a.annual_dilution)
        cash += fcf
        min_cash = 0.05 * rev
        if cash < min_cash:                         # necesita financiamiento externo: se asume equity
            need = min_cash - cash
            raise_price = mk.price * (1 - equity_raise_discount) / adr / fx   # en moneda de reporte por acción
            issued = need / raise_price if raise_price > 0 else 0.0
            shares += issued
            new_shares_fin += issued
            fin_need_total += need
            cash = min_cash
        years.append({"year": t + 1, "revenue": rev, "ebit": ebit, "ebit_margin": a.ebit_margin[t], "fcf": fcf,
                      "interest": interest, "cash": cash, "debt": debt0, "shares": shares, "net_income": ni, "da": da})
        prev_rev = rev
    targets, rets = {}, {}
    for h in HORIZONS_M:
        y = years[h // 12 - 1]
        metric = _metric_for_multiple(a.exit_multiple_type, y["revenue"], y["ebit"], y["da"], y["net_income"], y["fcf"])
        if metric <= 0 and a.exit_multiple_type != "EV/Sales":
            # múltiplo sobre métrica negativa no tiene sentido: valor = caja neta (piso contable) — explícito
            equity = y["cash"] - y["debt"]
        elif a.exit_multiple_type == "P/E":
            equity = a.exit_multiple * metric
        else:
            equity = a.exit_multiple * metric + y["cash"] - y["debt"]
        if equity < 0:
            notes.append(f"{h}m: deuda neta > valor de la empresa: equity con piso 0 (pérdida total)")
            equity = 0.0
        px = equity / y["shares"] * fx * adr if y["shares"] > 0 else None
        targets[h] = px
        rets[h] = (px / mk.price - 1) if px is not None else None
    base_shares = mk.shares * mk.adr_ratio
    return ScenarioResult(name=a.name, probability=a.probability, years=years, price_target=targets, returns=rets,
                          financing_need=fin_need_total, dilution_from_financing=new_shares_fin / base_shares,
                          notes=list(dict.fromkeys(notes)))


def run_scenarios(assumptions: list[ScenarioAssumptions], f: Fundamentals, mk: MarketInputs) -> ScenarioSet:
    names = sorted(a.name for a in assumptions)
    if names != ["base", "bear", "bull"]:
        raise ValueError("se requieren exactamente los escenarios bear, base y bull")
    total_p = sum(a.probability for a in assumptions)
    if abs(total_p - 1) > 1e-6:
        raise ValueError(f"las probabilidades deben sumar 1 (suman {total_p:.3f})")
    warnings = []
    if f.v("revenue_ttm") is None:
        warnings.append("sin ingresos TTM: base anualizada desde el último trimestre o revenue_path explícito")
    if f.data_quality in ("search_summary", "mixed"):
        warnings.append(f"calidad de datos: {f.data_quality} — verificar contra documentos primarios")
    if f.data_quality == "synthetic":
        warnings.append("DATOS SINTÉTICOS — resultado solo ilustrativo")
    res = [run_scenario(a, f, mk) for a in assumptions]
    for r in res:
        warnings += [f"{r.name}: {n}" if "piso 0" in n else n for n in r.notes]
    warnings = list(dict.fromkeys(warnings))
    by = {r.name: r for r in res}
    exp, ploss = {}, {}
    for h in HORIZONS_M:
        vals = [(r.probability, r.returns[h]) for r in res]
        exp[h] = None if any(v is None for _, v in vals) else sum(p * v for p, v in vals)
        ploss[h] = sum(p for p, v in vals if v is not None and v < 0)
    p_perm = sum(r.probability for r in res if r.returns[36] is not None and r.returns[36] < -0.30)
    bear = by["bear"].returns[36]
    bull = by["bull"].returns[36]
    ud = (bull / -bear) if (bull is not None and bear is not None and bear < 0) else None
    if bear is not None and bear > by["base"].returns[36]:
        warnings.append("bear > base: supuestos inconsistentes")
    return ScenarioSet(base_revenue=f.v("revenue_ttm"), price0=mk.price, results=res, expected_return=exp,
                       prob_loss=ploss, prob_permanent_loss=p_perm, bear_downside_36m=bear,
                       upside_downside_ratio=ud, warnings=warnings)


def sensitivity(base: ScenarioAssumptions, f: Fundamentals, mk: MarketInputs, horizon: int = 24,
                growth_shifts=(-0.10, -0.05, 0.0, 0.05, 0.10),
                margin_shifts=(-0.05, -0.025, 0.0, 0.025, 0.05)) -> dict:
    """Tabla de sensibilidad del retorno a `horizon` meses: crecimiento (filas) x margen (columnas)."""
    table = {}
    for g in growth_shifts:
        row = {}
        for m in margin_shifts:
            a = base.model_copy(update={
                "revenue_growth": [x + g for x in base.revenue_growth] if base.revenue_growth else None,
                "revenue_path": [x * (1 + g) for x in base.revenue_path] if base.revenue_path else None,
                "ebit_margin": [x + m for x in base.ebit_margin]})
            row[m] = run_scenario(a, f, mk).returns[horizon]
        table[g] = row
    return {"horizon_months": horizon, "rows": "shift crecimiento anual", "cols": "shift margen EBIT", "table": table}


# =========================================================================== reverse DCF
class ReverseDCF(BaseModel):
    implied_revenue_cagr_10y: float | None
    assumed_terminal_ebit_margin: float
    wacc: float
    terminal_growth: float
    sales_to_capital: float
    current_ev: float
    note: str


def dcf_value(rev0: float, cagr: float, margin_path: list[float], wacc: float, g_term: float,
              tax: float, sales_to_capital: float, years: int = 10, terminal_roic: float = 0.15) -> float:
    """EV por DCF tipo Damodaran: FCFF = NOPAT - reinversión (Δventas / sales_to_capital).

    Valor terminal: NOPAT_{n+1} * (1 - g/ROIC_terminal) / (WACC - g).
    """
    pv, rev = 0.0, rev0
    for t in range(1, years + 1):
        new_rev = rev * (1 + cagr)
        m = margin_path[min(t - 1, len(margin_path) - 1)]
        ebit = new_rev * m
        nopat = ebit * (1 - tax) if ebit > 0 else ebit
        reinvest = max(new_rev - rev, 0) / sales_to_capital
        fcff = nopat - reinvest
        pv += fcff / (1 + wacc) ** t
        rev = new_rev
    term_nopat = rev * (1 + g_term) * margin_path[-1] * (1 - tax)
    term_fcff = term_nopat * (1 - g_term / terminal_roic)
    tv = term_fcff / (wacc - g_term)
    return pv + tv / (1 + wacc) ** years


def reverse_dcf(f: Fundamentals, mk: MarketInputs, terminal_margin: float | None = None, wacc: float = 0.10,
                terminal_growth: float = 0.03, tax: float = 0.21, sales_to_capital: float = 1.5) -> ReverseDCF | None:
    """Crecimiento de ingresos a 10 años implícito en el EV actual, dado un margen EBIT objetivo.

    El margen converge linealmente desde el actual hasta `terminal_margin` en 5 años.
    Responde: ¿qué tiene que pasar para justificar el precio actual?
    """
    rev0 = f.v("revenue_ttm")
    if not rev0 or rev0 <= 0:
        return None
    mult = compute_multiples(f, mk)
    if mult.ev is None or mult.ev <= 0:
        return None
    ev_report = mult.ev / mk.fx_report_to_price
    cur_m = f.v("ebit_margin") if f.v("ebit_margin") is not None else 0.0
    term_m = terminal_margin if terminal_margin is not None else max(cur_m, 0.10)
    path = [cur_m + (term_m - cur_m) * min(t, 5) / 5 for t in range(1, 11)]
    lo, hi = -0.30, 1.50

    def val(g: float) -> float:
        return dcf_value(rev0, g, path, wacc, terminal_growth, tax, sales_to_capital)

    if val(hi) < ev_report:
        implied, note = None, f"el EV exige crecimiento > {hi:.0%} anual con margen {term_m:.0%}: expectativas extremas"
    elif val(lo) > ev_report:
        implied, note = lo, f"el EV es consistente incluso con contracción de {lo:.0%} anual"
    else:
        for _ in range(80):
            mid = (lo + hi) / 2
            if val(mid) < ev_report:
                lo = mid
            else:
                hi = mid
        implied, note = (lo + hi) / 2, "bisección convergida"
    return ReverseDCF(implied_revenue_cagr_10y=implied, assumed_terminal_ebit_margin=term_m, wacc=wacc,
                      terminal_growth=terminal_growth, sales_to_capital=sales_to_capital, current_ev=mult.ev, note=note)
