"""Métricas fundamentales DERIVADAS a partir de datapoints originales (point-in-time).

Cada métrica devuelta conserva qué datapoints la originaron (`inputs`) y el período que cubre.
Si falta un insumo, la métrica es None y se registra en `missing`: nunca se rellena.

Reglas de selección (en este orden):
  1. Point-in-time: solo datapoints con `known_at` conocido y <= as_of.
  2. Unidades: se normalizan a la unidad base (USD_millions -> USD x 1e6). Unidad desconocida = descartado.
  3. Vintage y fuente: el mismo período puede venir de varias fuentes o re-expresiones. Se agrupan fechas a
     <= 10 días (años de 52/53 semanas) y se elige por precedencia de fuente (filing > declaración > prensa >
     resumen de buscador > ...) y luego el vintage más reciente conocido a as_of (re-expresión vigente).
  4. Moneda: se usa la moneda de reporte dominante; puntos en otra moneda se descartan y se marca conflicto.
  5. Trimestres: los flujos de caja del 10-Q son acumulados del año (YTD). Se reconstruye
     Q2 = H - Q1, Q3 = 9M - H, Q4 = FY - 9M (o FY - Q1 - Q2 - Q3). TTM exige 4 trimestres contiguos.
  6. Balance: todas las cuentas patrimoniales se toman de UNA misma fecha de balance.
  7. Comparaciones interanuales por FECHA (~365 días), no por posición en la serie.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta

from qa.provenance import DataPoint

FLOW_METRICS = ["revenue", "cost_of_revenue", "gross_profit", "operating_income", "net_income", "d_and_a",
                "ocf", "capex", "sbc", "rd_expense", "interest_expense", "income_tax", "pretax_income"]
STOCK_METRICS = ["cash", "short_term_investments", "debt_total", "debt_noncurrent", "debt_current", "equity",
                 "total_assets", "current_assets", "current_liabilities", "inventory", "receivables", "rpo",
                 "shares_outstanding"]
SHARE_METRICS = {"diluted_shares", "shares_outstanding"}
MONETARY = (set(FLOW_METRICS) | set(STOCK_METRICS)) - SHARE_METRICS
SOURCE_PRECEDENCE = {"confirmed_by_filing": 0, "company_statement": 1, "media_report": 2, "search_summary": 3,
                     "analyst_prior": 4, "hypothesis": 5, "synthetic_test_data": 6}
CLUSTER_DAYS = 10
_SCALED = re.compile(r"^([A-Z]{3})[_ ](thousands|millions|billions)$", re.I)
_SCALE = {"thousands": 1e3, "millions": 1e6, "billions": 1e9}


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
    has_synthetic: bool = False              # cualquier dato sintético contamina el resultado (K4)
    currency_conflict: bool = False          # puntos en varias monedas de reporte (se descartaron los minoritarios)
    balance_date: date | None = None
    warnings: list[str] = field(default_factory=list)

    def v(self, name: str) -> float | None:
        m = self.metrics.get(name)
        return m.value if m else None

    def as_dict(self) -> dict:
        return {k: m.value for k, m in self.metrics.items()}


# =========================================================================== selección de datapoints
def _normalize_unit(p: DataPoint) -> DataPoint | None:
    """Lleva el valor a unidad base. Devuelve None si la unidad no es interpretable para la métrica."""
    unit = (p.unit or "").strip()
    if p.metric in SHARE_METRICS:
        return p if unit.lower() in ("shares", "share", "") else None
    if p.metric not in MONETARY:
        return p
    if re.fullmatch(r"[A-Z]{3}", unit):
        ccy = p.currency or unit
        return p if ccy == unit else p.model_copy(update={"currency": unit})
    m = _SCALED.match(unit)
    if m and p.value is not None:
        ccy = m.group(1).upper()
        return p.model_copy(update={"value": p.value * _SCALE[m.group(2).lower()], "unit": ccy, "currency": ccy,
                                    "note": ((p.note or "") + f"; escalado desde {unit}").lstrip("; ")})
    return None


def _cluster_select(points: list[DataPoint]) -> list[DataPoint]:
    """Por (métrica, tipo de período): agrupa fechas a <= CLUSTER_DAYS y elige un punto por grupo."""
    groups: dict[tuple[str, str | None], list[DataPoint]] = {}
    for p in points:
        groups.setdefault((p.metric, p.period_type), []).append(p)
    out = []
    for pts in groups.values():
        pts.sort(key=lambda x: x.period_end or date.min)
        cluster: list[DataPoint] = []
        for p in pts + [None]:  # centinela para vaciar el último grupo
            if p is not None and cluster and p.period_end and cluster[0].period_end and \
                    (p.period_end - cluster[0].period_end).days <= CLUSTER_DAYS:
                cluster.append(p)
                continue
            if cluster:
                best = min(cluster, key=lambda x: (SOURCE_PRECEDENCE.get(x.verification.value, 9),
                                                   -(x.known_at.toordinal() if x.known_at else 0)))
                vals = {round(x.value, 6) for x in cluster if x.source.name == best.source.name and x.value is not None}
                if len(vals) > 1:
                    best = best.model_copy(update={"note": ((best.note or "") + "; RE-EXPRESADO: vintages "
                                                            + ",".join(str(v) for v in sorted(vals))).lstrip("; ")})
                out.append(best)
            cluster = [p] if p is not None else []
    return out


def select_points(points: list[DataPoint], as_of: date, f: Fundamentals) -> list[DataPoint]:
    pit = [p for p in points if p.known_at is not None and p.known_at <= as_of
           and p.value is not None and p.period_end is not None]
    dropped_unknown_date = sum(1 for p in points if p.known_at is None)
    if dropped_unknown_date:
        f.warnings.append(f"{dropped_unknown_date} datapoints sin known_at excluidos (no se sabe cuándo se conocieron)")
    norm = []
    for p in pit:
        q = _normalize_unit(p)
        if q is None:
            f.warnings.append(f"unidad no interpretable descartada: {p.metric} '{p.unit}' ({p.source.name})")
        else:
            norm.append(q)
    sel = _cluster_select(norm)
    ccys = Counter(p.currency for p in sel if p.metric in MONETARY and p.currency)
    if not ccys and any(p.metric in MONETARY for p in sel):
        f.warnings.append("moneda de reporte desconocida")
    if len(ccys) > 1:
        f.currency_conflict = True
        main = ccys.most_common(1)[0][0]
        f.warnings.append(f"monedas mezcladas {dict(ccys)}: se usa {main} y se descartan las demás")
        sel = [p for p in sel if p.metric not in MONETARY or p.currency == main]
    f.currency = ccys.most_common(1)[0][0] if ccys else None
    return sel


# =========================================================================== trimestres y TTM
def _near(d: date, candidates, lo: int, hi: int):  # noqa: ANN001, ANN202
    """Primer candidato cuyo fin está entre lo y hi días antes de d."""
    best = None
    for c in candidates:
        gap = (d - c).days
        if lo <= gap <= hi and (best is None or abs(gap - 91) < abs((d - best).days - 91)):
            best = c
    return best


def _quarters(points: list[DataPoint], metric: str, additive: bool = True) -> dict[date, tuple[float, str]]:
    """Serie trimestral de una métrica de flujo, reconstruyendo trimestres a partir de acumulados YTD.

    `additive=False` (p.ej. acciones promedio) desactiva toda derivación por resta.
    """
    by: dict[str, dict[date, DataPoint]] = {"Q": {}, "H": {}, "9M": {}, "FY": {}}
    for p in points:
        if p.metric == metric and p.period_type in by:
            by[p.period_type][p.period_end] = p
    q: dict[date, tuple[float, str]] = {d: (p.value, p.key()) for d, p in by["Q"].items()}
    if not additive:
        return dict(sorted(q.items()))

    def has_q(end: date) -> bool:
        return any(abs((end - d).days) <= 7 for d in q)

    for end, h in sorted(by["H"].items()):                         # Q2 = H - Q1
        if has_q(end):
            continue
        q1 = _near(end, [d for d, p in by["Q"].items()], 80, 100)
        if q1 is not None:
            q[end] = (h.value - by["Q"][q1].value, f"derived:H-Q1({h.key()};{by['Q'][q1].key()})")
    for end, n9 in sorted(by["9M"].items()):                       # Q3 = 9M - H
        if has_q(end):
            continue
        h_end = _near(end, list(by["H"]), 80, 100)
        if h_end is not None:
            q[end] = (n9.value - by["H"][h_end].value, f"derived:9M-H({n9.key()};{by['H'][h_end].key()})")
    for end, fy in sorted(by["FY"].items()):                       # Q4 = FY - 9M  o  FY - (Q1+Q2+Q3)
        if has_q(end):
            continue
        n9_end = _near(end, list(by["9M"]), 80, 100)
        if n9_end is not None:
            q[end] = (fy.value - by["9M"][n9_end].value, f"derived:FY-9M({fy.key()};{by['9M'][n9_end].key()})")
            continue
        prior = sorted(d for d in q if timedelta(days=60) < end - d < timedelta(days=300))
        if len(prior) >= 3 and _contiguous(prior[-3:] + [end]):
            last3 = prior[-3:]
            q[end] = (fy.value - sum(q[d][0] for d in last3), f"derived:FY-Q1Q2Q3({fy.key()})")
    return dict(sorted(q.items()))


def _contiguous(dates: list[date]) -> bool:
    return all(75 <= (b - a).days <= 105 for a, b in zip(dates, dates[1:]))


def _ttm(series: dict[date, tuple[float, str]], end: date | None = None,
         fy: dict[date, DataPoint] | None = None) -> Metric:
    dates = [d for d in series if end is None or d <= end + timedelta(days=CLUSTER_DAYS)]
    if len(dates) >= 4 and _contiguous(dates[-4:]):
        last4 = dates[-4:]
        return Metric(sum(series[d][0] for d in last4), period=f"TTM a {last4[-1]}",
                      inputs=[series[d][1] for d in last4])
    # Respaldo: si el último ejercicio cerrado coincide con el último período, el FY ES el TTM.
    if fy:
        last = dates[-1] if dates else None
        cands = [d for d in fy if (end is None or d <= end + timedelta(days=CLUSTER_DAYS))
                 and (last is None or d >= last - timedelta(days=7))]
        if cands:
            d = max(cands)
            return Metric(fy[d].value, period=f"FY a {d}", inputs=[fy[d].key()], note="TTM = ejercicio fiscal")
    return Metric(None, note="menos de 4 trimestres contiguos")


def _yoy_point(dates: list[date], last: date, tol: int = 20) -> date | None:
    target = last - timedelta(days=365)
    cands = [d for d in dates if abs((d - target).days) <= tol]
    return min(cands, key=lambda d: abs((d - target).days)) if cands else None


def _div(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return a / b


# =========================================================================== cálculo principal
def compute_fundamentals(entity: str, points: list[DataPoint], as_of: date | None = None) -> Fundamentals:
    as_of = as_of or date.today()
    f = Fundamentals(entity=entity, as_of=as_of, currency=None)
    pts = select_points(points, as_of, f)
    vers = {p.verification.value for p in pts}
    f.has_synthetic = "synthetic_test_data" in vers
    f.data_quality = ("none" if not pts else "synthetic" if f.has_synthetic
                      else "filing" if vers <= {"confirmed_by_filing"} else
                      "search_summary" if vers == {"search_summary"} else "mixed")
    M = f.metrics

    fy_by = {m: {p.period_end: p for p in pts if p.metric == m and p.period_type == "FY"} for m in FLOW_METRICS}
    series = {m: _quarters(pts, m) for m in FLOW_METRICS}
    f.quarterly = {m: {d: v for d, (v, _) in s.items()} for m, s in series.items() if s}
    for m in FLOW_METRICS:
        M[f"{m}_ttm"] = _ttm(series[m], fy=fy_by[m])

    # ---- balance: una sola fecha
    stocks = {m: {p.period_end: p for p in pts if p.metric == m and p.period_type == "instant"} for m in STOCK_METRICS}
    anchor = stocks["cash"] or next((stocks[m] for m in STOCK_METRICS if stocks[m]), {})
    f.balance_date = max(anchor) if anchor else None
    for m in STOCK_METRICS:
        at = None
        if f.balance_date is not None:
            at = next((d for d in stocks[m] if abs((d - f.balance_date).days) <= CLUSTER_DAYS), None)
        if at is not None:
            p = stocks[m][at]
            M[m] = Metric(p.value, period=str(at), inputs=[p.key()])
        else:
            older = max((d for d in stocks[m]), default=None)
            M[m] = Metric(None, note=f"sin dato a la fecha de balance {f.balance_date}" +
                          (f" (último disponible {older}, no se mezcla)" if older else ""))
    if f.balance_date and (as_of - f.balance_date).days > 200:
        f.warnings.append(f"balance desactualizado: {f.balance_date} ({(as_of - f.balance_date).days} días)")

    # Si solo hay un trimestre (p.ej. investigación web), exponer el último trimestre sin anualizar.
    for m in ("revenue", "net_income"):
        if series[m]:
            d = max(series[m])
            M[f"{m}_last_q"] = Metric(series[m][d][0], period=f"Q a {d}", inputs=[series[m][d][1]])

    rev = M["revenue_ttm"].value
    rev_dates = list(series["revenue"])
    M["revenue_growth_yoy"] = Metric(None, note="historia insuficiente")
    M["revenue_growth_q_yoy"] = Metric(None, note="historia insuficiente")
    if rev_dates:
        last = rev_dates[-1]
        ya = _yoy_point(rev_dates, last)
        if ya is not None:
            prev = _ttm(series["revenue"], end=ya)
            if prev.value and rev is not None and prev.period and prev.period.startswith("TTM"):
                M["revenue_growth_yoy"] = Metric(rev / prev.value - 1, period=f"TTM {last} vs TTM {ya}",
                                                 inputs=M["revenue_ttm"].inputs + prev.inputs)
            if series["revenue"][ya][0]:
                M["revenue_growth_q_yoy"] = Metric(series["revenue"][last][0] / series["revenue"][ya][0] - 1,
                                                   period=f"{last} vs {ya}")
    gp = M["gross_profit_ttm"].value
    if gp is None and rev is not None and M["cost_of_revenue_ttm"].value is not None:
        gp = rev - M["cost_of_revenue_ttm"].value
    ebit = M["operating_income_ttm"].value
    da = M["d_and_a_ttm"].value
    ebitda = ebit + da if ebit is not None and da is not None else None
    ocf, capex = M["ocf_ttm"].value, M["capex_ttm"].value
    fcf = ocf - abs(capex) if ocf is not None and capex is not None else None
    cash = None
    if M["cash"].value is not None:
        sti = M["short_term_investments"].value
        cash = M["cash"].value + (sti or 0.0)
    debt, debt_note = M["debt_total"].value, "debt_total"
    if debt is None and (M["debt_noncurrent"].value is not None or M["debt_current"].value is not None):
        debt = (M["debt_noncurrent"].value or 0) + (M["debt_current"].value or 0)
        debt_note = "no corriente + corriente a la fecha de balance"
    M["gross_margin"] = Metric(_div(gp, rev), "TTM")
    M["ebitda_ttm"] = Metric(ebitda, "TTM", note="EBIT + D&A (no ajustado)")
    M["ebitda_margin"] = Metric(_div(ebitda, rev), "TTM")
    M["ebit_margin"] = Metric(_div(ebit, rev), "TTM")
    M["net_margin"] = Metric(_div(M["net_income_ttm"].value, rev), "TTM")
    M["fcf_ttm"] = Metric(fcf, "TTM", note="OCF - capex")
    M["fcf_margin"] = Metric(_div(fcf, rev), "TTM")
    M["capex_intensity"] = Metric(_div(abs(capex) if capex is not None else None, rev), "TTM")
    M["sbc_pct_revenue"] = Metric(_div(M["sbc_ttm"].value, rev), "TTM")
    M["cash_total"] = Metric(cash, M["cash"].period, note="caja + inversiones CP (misma fecha de balance)")
    M["debt"] = Metric(debt, str(f.balance_date) if debt is not None else None,
                       note=f"deuda financiera ({debt_note}); excluye leases operativos")
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
    if fcf is not None and fcf < 0 and cash is not None:
        M["cash_runway_quarters"] = Metric(max(cash, 0.0) / (-fcf / 4), note="caja / quema trimestral de FCF")
    else:
        M["cash_runway_quarters"] = Metric(None, note="FCF positivo o sin datos")
    # Dilución: acciones diluidas promedio, mismo trimestre un año antes (por fecha)
    sh = _quarters(pts, "diluted_shares", additive=False)
    if sh:
        d = list(sh)
        M["diluted_shares"] = Metric(sh[d[-1]][0], period=str(d[-1]), inputs=[sh[d[-1]][1]])
        ya = _yoy_point(d, d[-1])
        M["dilution_yoy"] = (Metric(_div(sh[d[-1]][0], sh[ya][0]) - 1, period=f"{d[-1]} vs {ya}")
                             if ya is not None and sh[ya][0] else Metric(None, note="sin trimestre comparable"))
    # Margen incremental (apalancamiento operativo): ΔEBIT / ΔRevenue TTM interanual
    oi_dates = list(series["operating_income"])
    if rev_dates and oi_dates and rev is not None and ebit is not None:
        ya_r = _yoy_point(rev_dates, rev_dates[-1])
        ya_o = _yoy_point(oi_dates, oi_dates[-1])
        if ya_r is not None and ya_o is not None:
            prev_rev = _ttm(series["revenue"], end=ya_r).value
            prev_oi = _ttm(series["operating_income"], end=ya_o).value
            if None not in (prev_rev, prev_oi) and rev != prev_rev:
                M["incremental_margin"] = Metric((ebit - prev_oi) / (rev - prev_rev), period="TTM vs TTM-1")

    key_needed = ["revenue_ttm", "operating_income_ttm", "ocf_ttm", "capex_ttm", "cash", "equity", "diluted_shares"]
    f.missing = [k for k in key_needed if (M.get(k) is None or M[k].value is None)]
    return f
