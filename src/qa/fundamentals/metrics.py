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
    debt_elsewhere: bool = False             # hay deuda reportada, pero no a la fecha de balance
    warnings: list[str] = field(default_factory=list)

    def v(self, name: str) -> float | None:
        m = self.metrics.get(name)
        return m.value if m else None

    def as_dict(self) -> dict:
        return {k: m.value for k, m in self.metrics.items()}


# =========================================================================== selección de datapoints
def rank(p: DataPoint) -> int:
    return SOURCE_PRECEDENCE.get(p.verification.value, 9)


def _normalize_unit(p: DataPoint) -> tuple[DataPoint | None, str | None]:
    """Lleva el valor a unidad base. Devuelve (punto, None) o (None, motivo) si la unidad no es interpretable
    o contradice la moneda declarada."""
    unit = (p.unit or "").strip()
    if p.metric in SHARE_METRICS:
        return (p, None) if unit.lower() in ("shares", "share", "") else (None, f"unidad '{unit}' no es de acciones")
    if p.metric not in MONETARY:
        return p, None
    if re.fullmatch(r"[A-Z]{3}", unit):
        if p.currency and p.currency != unit:
            return None, f"unidad {unit} contradice moneda {p.currency}"
        return (p if p.currency == unit else p.model_copy(update={"currency": unit})), None
    m = _SCALED.match(unit)
    if m and p.value is not None:
        ccy = m.group(1).upper()
        if p.currency and p.currency != ccy:
            return None, f"unidad {unit} contradice moneda {p.currency}"
        return p.model_copy(update={"value": p.value * _SCALE[m.group(2).lower()], "unit": ccy, "currency": ccy,
                                    "note": ((p.note or "") + f"; escalado desde {unit}").lstrip("; ")}), None
    return None, f"unidad '{unit}' no interpretable"


def _cluster_select(points: list[DataPoint]) -> tuple[list[DataPoint], dict[str, list[DataPoint]]]:
    """Por (métrica, tipo de período): agrupa fechas a <= CLUSTER_DAYS y elige un punto por grupo
    (mejor fuente; dentro de ella, el vintage más reciente). Devuelve también todos los vintages de esa fuente
    para el punto elegido (clave = DataPoint.key()), necesarios para derivar trimestres con vintages consistentes."""
    groups: dict[tuple[str, str | None], list[DataPoint]] = {}
    for p in points:
        groups.setdefault((p.metric, p.period_type), []).append(p)
    out, vint = [], {}
    for pts in groups.values():
        pts.sort(key=lambda x: x.period_end or date.min)
        cluster: list[DataPoint] = []
        for p in pts + [None]:  # centinela para vaciar el último grupo
            if p is not None and cluster and p.period_end and cluster[0].period_end and \
                    (p.period_end - cluster[0].period_end).days <= CLUSTER_DAYS:
                cluster.append(p)
                continue
            if cluster:
                best = min(cluster, key=lambda x: (rank(x), -(x.known_at.toordinal() if x.known_at else 0)))
                same = sorted((x for x in cluster if x.source.name == best.source.name),
                              key=lambda x: x.known_at or date.min)
                vals = {round(x.value, 6) for x in same if x.value is not None}
                if len(vals) > 1:
                    best = best.model_copy(update={"note": ((best.note or "") + "; RE-EXPRESADO: vintages "
                                                            + ",".join(str(v) for v in sorted(vals))).lstrip("; ")})
                out.append(best)
                vint[best.key()] = same
            cluster = [p] if p is not None else []
    return out, vint


def select_points(points: list[DataPoint], as_of: date, f: Fundamentals) -> tuple[list[DataPoint], dict]:
    pit = [p for p in points if p.known_at is not None and p.known_at <= as_of
           and p.value is not None and p.period_end is not None]
    dropped_unknown_date = sum(1 for p in points if p.known_at is None)
    if dropped_unknown_date:
        f.warnings.append(f"{dropped_unknown_date} datapoints sin known_at excluidos (no se sabe cuándo se conocieron)")
    norm = []
    for p in pit:
        q, why = _normalize_unit(p)
        if q is None:
            f.warnings.append(f"descartado {p.metric} {p.period_end} ({p.source.name}): {why}")
        else:
            norm.append(q)
    sel, vint = _cluster_select(norm)
    monetary = [p for p in sel if p.metric in MONETARY and p.currency]
    if not monetary and any(p.metric in MONETARY for p in sel):
        f.warnings.append("moneda de reporte desconocida")
    if monetary:
        # moneda de reporte = la del dato MÁS RECIENTE de la MEJOR fuente disponible (no un conteo histórico)
        best_r = min(rank(x) for x in monetary)
        latest = max((x for x in monetary if rank(x) == best_r), key=lambda x: x.period_end)
        main = latest.currency
        others = [p for p in monetary if p.currency != main]
        recent_others = [p for p in others if (as_of - p.period_end).days <= 800]
        if recent_others:
            f.currency_conflict = True
            f.warnings.append(f"monedas mezcladas en los últimos ~2 años ({sorted({p.currency for p in recent_others})} "
                              f"vs {main}): valuación bloqueada hasta resolver")
        elif others:
            f.warnings.append(f"cambio de moneda de reporte: se descartan {len(others)} datos antiguos en "
                              f"{sorted({p.currency for p in others})}")
        sel = [p for p in sel if p.metric not in MONETARY or p.currency == main]
        f.currency = main
    for p in sel:
        if p.note and ("RE-EXPRESADO" in p.note or "restates=" in p.note) and (as_of - p.known_at).days <= 730:
            f.warnings.append(f"re-expresión: {p.metric} {p.period_type} {p.period_end} (vintage {p.known_at})")
    return sel, vint


# =========================================================================== trimestres y TTM
def _near(d: date, candidates, lo: int, hi: int):  # noqa: ANN001, ANN202
    """Candidato cuyo fin está entre lo y hi días antes de d (el más cercano a 91 días)."""
    best = None
    for c in candidates:
        gap = (d - c).days
        if lo <= gap <= hi and (best is None or abs(gap - 91) < abs((d - best).days - 91)):
            best = c
    return best


def _consistent_pair(m: DataPoint, s: DataPoint, vint: dict) -> tuple[DataPoint, DataPoint, str | None]:
    """Elige vintages compatibles del minuendo (H/9M/FY) y del sustraendo (Q1/H/9M).

    - El sustraendo es el vintage conocido al presentar el minuendo (lo que la empresa reportó junto).
    - Si el minuendo es una re-expresión y el sustraendo nunca se re-expresó después, se usan los vintages
      ORIGINALES de ambos: restar un acumulado re-expresado de uno original inventa un trimestre.
    """
    mv = vint.get(m.key(), [m])
    sv = vint.get(s.key(), [s])
    note = None
    m_use = m
    if len(mv) > 1 and m.known_at and not any(x.known_at and x.known_at >= m.known_at - timedelta(days=CLUSTER_DAYS)
                                              for x in sv):
        m_use = mv[0]
        note = f"re-expresión de {m.period_type} {m.period_end} sin re-expresión del acumulado previo: vintages originales"
    cands = [x for x in sv if x.known_at and m_use.known_at and x.known_at <= m_use.known_at]
    s_use = cands[-1] if cands else sv[0]
    return m_use, s_use, note


def _quarters(points: list[DataPoint], metric: str, additive: bool = True, vint: dict | None = None,
              warnings: list[str] | None = None) -> dict[date, tuple[float, str]]:
    """Serie trimestral de una métrica de flujo, reconstruyendo trimestres a partir de acumulados YTD.

    Un trimestre reportado directamente por una fuente de MENOR precedencia no bloquea la derivación desde
    fuentes de mayor precedencia (p.ej. Q4 de un resumen de buscador vs FY-9M de los filings).
    `additive=False` (p.ej. acciones promedio) desactiva toda derivación por resta.
    """
    vint = vint or {}
    by: dict[str, dict[date, DataPoint]] = {"Q": {}, "H": {}, "9M": {}, "FY": {}}
    for p in points:
        if p.metric == metric and p.period_type in by:
            by[p.period_type][p.period_end] = p
    q: dict[date, tuple[float, str, int]] = {d: (p.value, p.key(), rank(p)) for d, p in by["Q"].items()}
    if not additive:
        return {d: v[:2] for d, v in sorted(q.items())}

    def slot(end: date, r: int) -> bool:
        """True si hay que (re)calcular el trimestre que termina en `end` con insumos de precedencia r."""
        near = [d for d in q if abs((end - d).days) <= 7]
        if not near:
            return True
        if all(q[d][2] > r for d in near):          # el directo es de peor fuente: lo reemplaza el derivado
            for d in near:
                q.pop(d)
            return True
        return False

    def derive(end: date, minuend: DataPoint, sub: DataPoint, label: str) -> None:
        m_use, s_use, note = _consistent_pair(minuend, sub, vint)
        r = max(rank(m_use), rank(s_use))
        if slot(end, r):
            q[end] = (m_use.value - s_use.value, f"derived:{label}({m_use.key()};{s_use.key()})", r)
            if note and warnings is not None:
                warnings.append(f"{metric}: {note}")

    for end, h in sorted(by["H"].items()):                         # Q2 = H - Q1
        q1 = _near(end, list(by["Q"]), 80, 100)
        if q1 is not None:
            derive(end, h, by["Q"][q1], "H-Q1")
    for end, n9 in sorted(by["9M"].items()):                       # Q3 = 9M - H
        h_end = _near(end, list(by["H"]), 80, 100)
        if h_end is not None:
            derive(end, n9, by["H"][h_end], "9M-H")
    for end, fy in sorted(by["FY"].items()):                       # Q4 = FY - 9M  o  FY - (Q1+Q2+Q3)
        n9_end = _near(end, list(by["9M"]), 80, 100)
        if n9_end is not None:
            derive(end, fy, by["9M"][n9_end], "FY-9M")
            continue
        if not slot(end, rank(fy)):
            continue
        prior = sorted(d for d in q if timedelta(days=60) < end - d < timedelta(days=300))
        if len(prior) >= 3 and _contiguous(prior[-3:] + [end]):
            last3 = prior[-3:]
            q[end] = (fy.value - sum(q[d][0] for d in last3), f"derived:FY-Q1Q2Q3({fy.key()})",
                      max([rank(fy)] + [q[d][2] for d in last3]))
    return {d: v[:2] for d, v in sorted(q.items())}


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
    pts, vint = select_points(points, as_of, f)
    vers = {p.verification.value for p in pts}
    f.has_synthetic = "synthetic_test_data" in vers
    f.data_quality = ("none" if not pts else "synthetic" if f.has_synthetic
                      else "filing" if vers <= {"confirmed_by_filing"} else
                      "search_summary" if vers == {"search_summary"} else "mixed")
    M = f.metrics

    fy_by = {m: {p.period_end: p for p in pts if p.metric == m and p.period_type == "FY"} for m in FLOW_METRICS}
    series = {m: _quarters(pts, m, vint=vint, warnings=f.warnings) for m in FLOW_METRICS}
    f.quarterly = {m: {d: v for d, (v, _) in s.items()} for m, s in series.items() if s}
    for m in FLOW_METRICS:
        M[f"{m}_ttm"] = _ttm(series[m], fy=fy_by[m])

    # ---- balance: una sola fecha, la más reciente de la MEJOR fuente disponible (entre todas las cuentas)
    stocks = {m: {p.period_end: p for p in pts if p.metric == m and p.period_type == "instant"} for m in STOCK_METRICS}
    all_bs = [p for m in STOCK_METRICS for p in stocks[m].values()]
    if all_bs:
        best = min(rank(p) for p in all_bs)
        f.balance_date = max(p.period_end for p in all_bs if rank(p) == best)
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
    if f.balance_date is not None and M["cash"].value is None:
        f.warnings.append(f"sin caja a la fecha de balance {f.balance_date}: valuación no calculable")
    debt_points = [p for m in ("debt_total", "debt_noncurrent", "debt_current") for p in stocks[m].values()]
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
    # deuda: total vs componentes a la fecha de balance; gana la combinación de mejor fuente
    def _r(metric: str) -> int:
        at = M[metric].inputs
        return min((rank(p) for p in debt_points if at and p.key() == at[0]), default=99)
    debt, debt_note = None, None
    comp = [m for m in ("debt_noncurrent", "debt_current") if M[m].value is not None]
    r_total = _r("debt_total") if M["debt_total"].value is not None else 99
    r_comp = max((_r(m) for m in comp), default=99)
    if M["debt_total"].value is not None and r_total <= r_comp:
        debt, debt_note = M["debt_total"].value, "debt_total"
    elif comp:
        debt = sum(M[m].value for m in comp)
        debt_note = "no corriente + corriente a la fecha de balance"
    f.debt_elsewhere = debt is None and bool(debt_points)
    if f.debt_elsewhere:
        f.warnings.append("deuda reportada en otra fecha distinta del balance: no se asume 0 (valuación bloqueada)")
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
    sh = _quarters(pts, "diluted_shares", additive=False, vint=vint)
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
