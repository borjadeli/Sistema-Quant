"""Métricas fundamentales DERIVADAS a partir de datapoints originales (point-in-time).

Cada métrica devuelta conserva qué datapoints la originaron (`inputs`) y el período que cubre.
Si falta un insumo, la métrica es None y se registra en `missing`: nunca se rellena.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from qa.provenance import DataPoint

FLOW_METRICS = ["revenue", "cost_of_revenue", "gross_profit", "operating_income", "net_income", "d_and_a",
                "ocf", "capex", "sbc", "rd_expense", "interest_expense", "income_tax", "pretax_income"]
STOCK_METRICS = ["cash", "short_term_investments", "debt_total", "debt_noncurrent", "debt_current", "equity",
                 "total_assets", "current_assets", "current_liabilities", "inventory", "receivables", "rpo",
                 "shares_outstanding"]


@dataclass
class Metric:
    value: float | None
    period: str | None = None
    inputs: list[str] = field(default_factory=list)
    note: str | None = None


@dataclass
class Fundamentals:
    entity: str
    as_of: date
    currency: str | None
    metrics: dict[str, Metric] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    quarterly: dict[str, dict[date, float]] = field(default_factory=dict)   # serie trimestral (para gráficos)
    data_quality: str = "unknown"            # filing | search_summary | synthetic | mixed | none

    def v(self, name: str) -> float | None:
        m = self.metrics.get(name)
        return m.value if m else None

    def as_dict(self) -> dict:
        return {k: m.value for k, m in self.metrics.items()}


def _quarters(points: list[DataPoint], metric: str, additive: bool = True) -> dict[date, tuple[float, str]]:
    """Serie trimestral de una métrica de flujo; deriva Q4 = FY - 9M o FY - (Q1+Q2+Q3) si hace falta.

    `additive=False` (p.ej. acciones promedio) desactiva la derivación por resta.
    """
    q: dict[date, tuple[float, str]] = {}
    fy: dict[date, DataPoint] = {}
    nine: dict[date, DataPoint] = {}
    for p in points:
        if p.metric != metric or p.value is None or p.period_end is None:
            continue
        if p.period_type == "Q":
            q[p.period_end] = (p.value, p.key())
        elif p.period_type == "FY":
            fy[p.period_end] = p
        elif p.period_type == "9M":
            nine[p.period_end] = p
    for end, p in fy.items():
        if not additive or any(abs((end - d).days) <= 7 for d in q):
            continue
        n9 = next((n for d, n in nine.items() if 60 <= (end - d).days <= 110), None)
        if n9 is not None and n9.value is not None:
            q[end] = (p.value - n9.value, f"derived:FY-9M({p.key()};{n9.key()})")
            continue
        prior = sorted(d for d in q if timedelta(days=60) < end - d < timedelta(days=300))
        if len(prior) >= 3:
            last3 = prior[-3:]
            q[end] = (p.value - sum(q[d][0] for d in last3), f"derived:FY-Q1Q2Q3({p.key()})")
    return dict(sorted(q.items()))


def _ttm(series: dict[date, tuple[float, str]], end: date | None = None) -> Metric:
    dates = [d for d in series if end is None or d <= end]
    if len(dates) < 4:
        return Metric(None, note="menos de 4 trimestres")
    last4 = dates[-4:]
    if (last4[-1] - last4[0]).days > 300:          # trimestres no contiguos
        return Metric(None, note="trimestres no contiguos")
    return Metric(sum(series[d][0] for d in last4), period=f"TTM a {last4[-1]}", inputs=[series[d][1] for d in last4])


def _latest_instant(points: list[DataPoint], metric: str) -> Metric:
    cands = [p for p in points if p.metric == metric and p.period_type == "instant" and p.value is not None]
    if not cands:
        return Metric(None)
    p = max(cands, key=lambda x: x.period_end or date.min)
    return Metric(p.value, period=str(p.period_end), inputs=[p.key()])


def _div(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return a / b


def compute_fundamentals(entity: str, points: list[DataPoint], as_of: date | None = None) -> Fundamentals:
    as_of = as_of or date.today()
    pts = [p for p in points if p.known_at is None or p.known_at <= as_of]
    ccys = {p.currency for p in pts if p.currency and p.metric in FLOW_METRICS}
    f = Fundamentals(entity=entity, as_of=as_of, currency=next(iter(ccys)) if len(ccys) == 1 else None)
    vers = {p.verification.value for p in pts}
    f.data_quality = ("none" if not pts else "synthetic" if vers == {"synthetic_test_data"}
                      else "filing" if vers <= {"confirmed_by_filing"} else
                      "search_summary" if vers == {"search_summary"} else "mixed")
    M = f.metrics

    series = {m: _quarters(pts, m) for m in FLOW_METRICS}
    f.quarterly = {m: {d: v for d, (v, _) in s.items()} for m, s in series.items() if s}
    for m in FLOW_METRICS:
        M[f"{m}_ttm"] = _ttm(series[m])
    for m in STOCK_METRICS:
        M[m] = _latest_instant(pts, m)

    # Si solo hay un trimestre (p.ej. investigación web), exponer el último trimestre sin anualizar.
    for m in ("revenue", "net_income"):
        if series[m]:
            d = max(series[m])
            M[f"{m}_last_q"] = Metric(series[m][d][0], period=f"Q a {d}", inputs=[series[m][d][1]])

    rev = M["revenue_ttm"].value
    # Crecimiento interanual TTM vs TTM de hace 4 trimestres
    rev_dates = list(series["revenue"])
    if len(rev_dates) >= 8:
        prev = _ttm(series["revenue"], end=rev_dates[-5])
        M["revenue_growth_yoy"] = Metric(_div(rev, prev.value) - 1 if rev and prev.value else None,
                                         period="TTM/TTM-1", inputs=M["revenue_ttm"].inputs + prev.inputs)
    else:
        M["revenue_growth_yoy"] = Metric(None, note="historia insuficiente")
    if len(rev_dates) >= 5:
        last, yago = rev_dates[-1], rev_dates[-5]
        M["revenue_growth_q_yoy"] = Metric(_div(series["revenue"][last][0], series["revenue"][yago][0]) - 1
                                           if series["revenue"][yago][0] else None, period=f"{last} vs {yago}")
    gp = M["gross_profit_ttm"].value
    if gp is None and rev is not None and M["cost_of_revenue_ttm"].value is not None:
        gp = rev - M["cost_of_revenue_ttm"].value
    ebit = M["operating_income_ttm"].value
    da = M["d_and_a_ttm"].value
    ebitda = ebit + da if ebit is not None and da is not None else None
    ocf, capex = M["ocf_ttm"].value, M["capex_ttm"].value
    fcf = ocf - abs(capex) if ocf is not None and capex is not None else None
    cash = (M["cash"].value or 0) + (M["short_term_investments"].value or 0) if M["cash"].value is not None else None
    debt = M["debt_total"].value
    if debt is None and (M["debt_noncurrent"].value is not None or M["debt_current"].value is not None):
        debt = (M["debt_noncurrent"].value or 0) + (M["debt_current"].value or 0)
    M["gross_margin"] = Metric(_div(gp, rev), "TTM")
    M["ebitda_ttm"] = Metric(ebitda, "TTM", note="EBIT + D&A (no ajustado)")
    M["ebitda_margin"] = Metric(_div(ebitda, rev), "TTM")
    M["ebit_margin"] = Metric(_div(ebit, rev), "TTM")
    M["net_margin"] = Metric(_div(M["net_income_ttm"].value, rev), "TTM")
    M["fcf_ttm"] = Metric(fcf, "TTM", note="OCF - capex")
    M["fcf_margin"] = Metric(_div(fcf, rev), "TTM")
    M["capex_intensity"] = Metric(_div(abs(capex) if capex is not None else None, rev), "TTM")
    M["sbc_pct_revenue"] = Metric(_div(M["sbc_ttm"].value, rev), "TTM")
    M["cash_total"] = Metric(cash, M["cash"].period, note="caja + inversiones CP")
    M["debt"] = Metric(debt, M["debt_total"].period or M["debt_noncurrent"].period,
                       note="deuda financiera reportada; excluye leases operativos")
    M["net_debt"] = Metric(debt - cash if debt is not None and cash is not None else None)
    M["net_debt_to_ebitda"] = Metric(_div(M["net_debt"].value, ebitda) if ebitda and ebitda > 0 else None)
    M["interest_coverage"] = Metric(_div(ebit, M["interest_expense_ttm"].value))
    M["current_ratio"] = Metric(_div(M["current_assets"].value, M["current_liabilities"].value))
    tax_rate = _div(M["income_tax_ttm"].value, M["pretax_income_ttm"].value)
    tax_rate = min(max(tax_rate, 0.0), 0.35) if tax_rate is not None else 0.21
    invested = None
    if M["equity"].value is not None and debt is not None and cash is not None:
        invested = M["equity"].value + debt - cash
    M["roic"] = Metric(_div(ebit * (1 - tax_rate), invested) if ebit is not None and invested and invested > 0 else None,
                       note=f"NOPAT/(equity+deuda-caja); tasa {tax_rate:.0%}")
    # Runway de caja si quema caja
    if fcf is not None and fcf < 0 and cash is not None:
        M["cash_runway_quarters"] = Metric(cash / (-fcf / 4), note="caja / quema trimestral de FCF")
    else:
        M["cash_runway_quarters"] = Metric(None, note="FCF positivo o sin datos")
    # Dilución: acciones diluidas promedio, último trimestre vs hace un año
    sh = _quarters(pts, "diluted_shares", additive=False)
    if len(sh) >= 5:
        d = list(sh)
        M["dilution_yoy"] = Metric(_div(sh[d[-1]][0], sh[d[-5]][0]) - 1, period=f"{d[-1]} vs {d[-5]}")
        M["diluted_shares"] = Metric(sh[d[-1]][0], period=str(d[-1]))
    elif sh:
        d = list(sh)
        M["diluted_shares"] = Metric(sh[d[-1]][0], period=str(d[-1]))
    # Margen incremental (apalancamiento operativo): ΔEBIT / ΔRevenue TTM interanual
    if len(rev_dates) >= 8 and len(series["operating_income"]) >= 8:
        prev_rev = _ttm(series["revenue"], end=rev_dates[-5]).value
        oi_dates = list(series["operating_income"])
        prev_oi = _ttm(series["operating_income"], end=oi_dates[-5]).value
        if None not in (rev, prev_rev, ebit, prev_oi) and rev != prev_rev:
            M["incremental_margin"] = Metric((ebit - prev_oi) / (rev - prev_rev), period="TTM vs TTM-1")

    key_needed = ["revenue_ttm", "operating_income_ttm", "ocf_ttm", "capex_ttm", "cash", "equity", "diluted_shares"]
    f.missing = [k for k in key_needed if (M.get(k) is None or M[k].value is None)]
    return f
