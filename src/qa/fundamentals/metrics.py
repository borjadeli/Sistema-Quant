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

from qa.config import utc_today
from qa.provenance import DataPoint

FLOW_METRICS = ["revenue", "cost_of_revenue", "gross_profit", "operating_income", "net_income", "d_and_a",
                "ocf", "capex", "sbc", "rd_expense", "interest_expense", "income_tax", "pretax_income"]
STOCK_METRICS = ["cash", "short_term_investments", "debt_total", "debt_noncurrent", "debt_current", "equity",
                 "total_assets", "current_assets", "current_liabilities", "inventory", "receivables", "rpo",
                 "shares_outstanding"]
SHARE_METRICS = {"diluted_shares", "shares_outstanding"}
MONETARY = (set(FLOW_METRICS) | set(STOCK_METRICS)) - SHARE_METRICS
# Cuentas que fijan la fecha de balance. Las acciones en circulación de portada (dei) llevan la fecha de la
# PORTADA del filing (semanas después del cierre) y el RPO es una nota: no anclan el balance.
BALANCE_ANCHORS = [m for m in STOCK_METRICS if m not in ("shares_outstanding", "rpo")]
DEBT_METRICS = ("debt_total", "debt_noncurrent", "debt_current")
SOURCE_PRECEDENCE = {"confirmed_by_filing": 0, "company_statement": 1, "media_report": 2, "search_summary": 3,
                     "analyst_prior": 4, "hypothesis": 5, "synthetic_test_data": 6}
CLUSTER_DAYS = 10
BALANCE_SOFT_STALE_DAYS = 200      # advertencia
# > 17 meses: más que cierre + 12 meses + plazo del 20-F (4 meses) + prórroga 12b-25 (15 días) -> falta un reporte
BALANCE_HARD_STALE_DAYS = 520
# deuda no nula reportada hasta ~13 meses antes del balance (cubre la deuda etiquetada solo en el 10-K/20-F anterior)
# bloquea "suponer 0"; más vieja que eso se trata como deuda ya repagada (camino "SUPONE 0" con alerta)
DEBT_LOOKBACK_DAYS = 400
FY_FALLBACK_DAYS = 400             # un FY sigue siendo el TTM si los trimestres posteriores no llegan a 4 contiguos
# grupos de cuentas que un balance posterior de menor precedencia debe cubrir para reemplazar al de la mejor fuente
BALANCE_GROUPS = {"cash": ("cash",), "short_term_investments": ("short_term_investments",), "equity": ("equity",),
                  "debt": DEBT_METRICS}
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
    balance_stale: bool = False              # balance demasiado antiguo: apalancamiento y valuación no calculables
    balance_rank: int | None = None          # precedencia de la fuente que ancla el balance (0 = filing)
    alt_balance_date: date | None = None     # balance posterior PARCIAL (no usado): se le aplica igual el control K7
    debt_assumed_zero: bool = False          # filing sin etiqueta de deuda: deuda SUPUESTA 0 (alerta)
    debt_elsewhere: bool = False             # hay deuda reciente no nula, pero no a la fecha de balance
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
                if len(vals) > 1 and any(";material" in (x.note or "") for x in same):
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
        if p.note and ("RE-EXPRESADO" in p.note or ";material" in p.note) and (as_of - p.known_at).days <= 730:
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
        prior = sorted(d for d in q if timedelta(days=60) < end - d < timedelta(days=300))
        if len(prior) >= 3 and _contiguous(prior[-3:] + [end]):
            last3 = prior[-3:]
            fy_use, subs, note = _consistent_sum(fy, [(d, q[d]) for d in last3], by["Q"], vint)
            r = max([rank(fy_use)] + [q[d][2] for d in last3])
            if slot(end, r):     # solo se reemplaza un trimestre directo si la derivación es posible y no peor
                q[end] = (fy_use.value - sum(subs), f"derived:FY-Q1Q2Q3({fy_use.key()})", r)
                if note and warnings is not None:
                    warnings.append(f"{metric}: {note}")
    return {d: v[:2] for d, v in sorted(q.items())}


def _consistent_sum(fy: DataPoint, quarters: list, direct: dict[date, DataPoint],
                    vint: dict) -> tuple[DataPoint, list[float], str | None]:
    """Q4 = FY - (Q1+Q2+Q3) con vintages compatibles: cada trimestre directo se toma en el vintage conocido al
    presentar el FY; si el FY es una re-expresión y ningún trimestre se re-expresó después, se usa el FY original."""
    fv = vint.get(fy.key(), [fy])
    qv = []
    for d, (val, key, _) in quarters:
        p = direct.get(d)
        qv.append(vint.get(key, [p]) if p is not None and p.key() == key else None)   # None = trimestre derivado
    fy_use, note = fy, None
    known = [x.known_at for vs in qv if vs for x in vs if x.known_at]
    if len(fv) > 1 and fy.known_at and not any(k >= fy.known_at - timedelta(days=CLUSTER_DAYS) for k in known):
        fy_use = fv[0]
        note = f"re-expresión del FY {fy.period_end} sin re-expresión de los trimestres: vintages originales"
    subs = []
    for (d, (val, _, _)), vs in zip(quarters, qv):
        if not vs:
            subs.append(val)
            continue
        cands = [x for x in vs if x.known_at and fy_use.known_at and x.known_at <= fy_use.known_at]
        subs.append((cands[-1] if cands else vs[0]).value)
    return fy_use, subs, note


def _contiguous(dates: list[date]) -> bool:
    return all(75 <= (b - a).days <= 105 for a, b in zip(dates, dates[1:]))


def _ttm(series: dict[date, tuple[float, str]], end: date | None = None,
         fy: dict[date, DataPoint] | None = None) -> Metric:
    lim = end + timedelta(days=CLUSTER_DAYS) if end is not None else None
    dates = [d for d in series if lim is None or d <= lim]
    q_ttm = None
    if len(dates) >= 4 and _contiguous(dates[-4:]):
        last4 = dates[-4:]
        q_ttm = Metric(sum(series[d][0] for d in last4), period=f"TTM a {last4[-1]}",
                       inputs=[series[d][1] for d in last4])
    # El FY ES el TTM cuando es el último período: sin trimestres que lo cubran, o más reciente que el último
    # TTM trimestral (un ejercicio posterior que no se puede partir en trimestres reemplaza un TTM viejo).
    fy_d = max((d for d in (fy or {}) if lim is None or d <= lim), default=None)
    if fy_d is not None:
        last = dates[-1] if dates else None
        if q_ttm is not None and fy_d > last + timedelta(days=7):
            return Metric(fy[fy_d].value, period=f"FY a {fy_d}", inputs=[fy[fy_d].key()], note="TTM = ejercicio fiscal")
        if q_ttm is None and (last is None or fy_d >= last - timedelta(days=7)):
            return Metric(fy[fy_d].value, period=f"FY a {fy_d}", inputs=[fy[fy_d].key()], note="TTM = ejercicio fiscal")
        if q_ttm is None and last - fy_d <= timedelta(days=FY_FALLBACK_DAYS):
            # emisor anual (20-F/40-F) con un trimestre posterior aislado (p.ej. comunicado): el último ejercicio
            # sigue siendo el período completo de 12 meses más reciente
            return Metric(fy[fy_d].value, period=f"FY a {fy_d}", inputs=[fy[fy_d].key()],
                          note="TTM = último ejercicio; trimestres posteriores aislados (sin 4 contiguos)")
    return q_ttm or Metric(None, note="menos de 4 trimestres contiguos")


def _period_end(m: Metric) -> date | None:
    try:
        return date.fromisoformat((m.period or "").rsplit(" a ", 1)[1])
    except (IndexError, ValueError):
        return None


def _ttm_year_ago(series: dict, fy: dict, cur: Metric, tol: int = 20) -> Metric | None:
    """TTM (o FY) que termina ~365 días antes del fin del TTM actual; None si no hay uno comparable por fecha."""
    cur_end = _period_end(cur)
    if cur.value is None or cur_end is None:
        return None
    target = cur_end - timedelta(days=365)
    prev = _ttm(series, end=target, fy=fy)
    pe = _period_end(prev)
    return prev if prev.value is not None and pe is not None and abs((pe - target).days) <= tol else None


def _yoy_point(dates: list[date], last: date, tol: int = 20) -> date | None:
    target = last - timedelta(days=365)
    cands = [d for d in dates if abs((d - target).days) <= tol]
    return min(cands, key=lambda d: abs((d - target).days)) if cands else None


def _div(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return a / b


def _balance_date(f: Fundamentals, stocks: dict[str, dict[date, DataPoint]], as_of: date) -> int:
    """Fija f.balance_date y devuelve la precedencia de la fuente que la ancla.

    1. Ancla = la fecha más reciente de la MEJOR fuente entre cuentas patrimoniales (no la portada dei).
    2. Si una fuente aceptable de menor precedencia (p.ej. el comunicado de resultados, antes del 10-Q) trae un
       balance POSTERIOR con caja y deuda que además cubre todos los grupos que reportaba el de la mejor fuente
       (caja, inversiones CP, deuda, patrimonio), ese balance es el vigente. Si no, se conserva el de la mejor fuente
       y se advierte; si el posterior trae caja y deuda, igual se le aplica el control de apalancamiento (K7).
    3. Un balance de más de BALANCE_HARD_STALE_DAYS no es utilizable (balance_stale -> K4, valuación bloqueada).
    """
    anchors = [p for m in BALANCE_ANCHORS for p in stocks[m].values()]
    if not anchors:
        return 9
    best = min(rank(p) for p in anchors)
    f.balance_date = max(p.period_end for p in anchors if rank(p) == best)
    newer = [p for p in anchors if rank(p) <= max(best, 3)
             and p.period_end > f.balance_date + timedelta(days=CLUSTER_DAYS)]
    if newer:
        nd = max(p.period_end for p in newer)
        at_nd = [p for p in anchors if abs((p.period_end - nd).days) <= CLUSTER_DAYS]
        at_best = [p for p in anchors if abs((p.period_end - f.balance_date).days) <= CLUSTER_DAYS]

        def groups(ps: list[DataPoint]) -> set[str]:
            return {g for g, ms in BALANCE_GROUPS.items() if any(p.metric in ms for p in ps)}
        g_new, g_best = groups(at_nd), groups(at_best)
        if {"cash", "debt"} <= g_new and g_best <= g_new:
            f.warnings.append(f"balance al {nd} de una fuente de menor precedencia, posterior al último de la mejor "
                              f"fuente ({f.balance_date}) y con las mismas cuentas: se usa ese balance")
            f.balance_date, best = nd, min(rank(p) for p in at_nd)
        else:
            falta = sorted(({"cash", "debt"} | g_best) - g_new)
            f.warnings.append(f"datos de balance posteriores ({nd}) incompletos (faltan {', '.join(falta)}): se "
                              f"conserva el balance al {f.balance_date}")
            if {"cash", "debt"} <= g_new:
                f.alt_balance_date = nd
    age = (as_of - f.balance_date).days
    if age > BALANCE_HARD_STALE_DAYS:
        f.balance_stale = True
        f.warnings.append(f"balance demasiado antiguo: {f.balance_date} (> {BALANCE_HARD_STALE_DAYS} días): "
                          "apalancamiento y valuación no calculables")
    elif age > BALANCE_SOFT_STALE_DAYS:
        f.warnings.append(f"balance desactualizado: {f.balance_date} (> {BALANCE_SOFT_STALE_DAYS} días)")
    return best


# =========================================================================== cálculo principal
def compute_fundamentals(entity: str, points: list[DataPoint], as_of: date | None = None) -> Fundamentals:
    as_of = as_of or utc_today()
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

    # ---- balance: una sola fecha (ver _balance_date); las cuentas de otra fecha no se mezclan
    stocks = {m: {p.period_end: p for p in pts if p.metric == m and p.period_type == "instant"} for m in STOCK_METRICS}
    anchor_rank = _balance_date(f, stocks, as_of)
    f.balance_rank = anchor_rank if f.balance_date is not None else None
    for m in STOCK_METRICS:
        if m == "shares_outstanding":
            continue
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
    so = stocks["shares_outstanding"]          # acciones de portada: última cifra conocida, con su propia fecha
    if so:
        r_so = min(rank(p) for p in so.values())
        d_so = max(d for d, p in so.items() if rank(p) == r_so)
        M["shares_outstanding"] = Metric(so[d_so].value, period=str(d_so), inputs=[so[d_so].key()],
                                         note="última cifra conocida (fecha de portada del filing)")
    else:
        M["shares_outstanding"] = Metric(None, note="sin dato")
    if f.balance_date is not None and M["cash"].value is None:
        f.warnings.append(f"sin caja a la fecha de balance {f.balance_date}: valuación no calculable")
    debt_points = [p for m in DEBT_METRICS for p in stocks[m].values()]

    # Si solo hay un trimestre (p.ej. investigación web), exponer el último trimestre sin anualizar.
    for m in ("revenue", "net_income"):
        if series[m]:
            d = max(series[m])
            M[f"{m}_last_q"] = Metric(series[m][d][0], period=f"Q a {d}", inputs=[series[m][d][1]])

    rev = M["revenue_ttm"].value
    rev_dates = list(series["revenue"])
    M["revenue_growth_yoy"] = Metric(None, note="historia insuficiente")
    M["revenue_growth_q_yoy"] = Metric(None, note="historia insuficiente")
    prev = _ttm_year_ago(series["revenue"], fy_by["revenue"], M["revenue_ttm"])
    if prev is not None and prev.value and rev is not None:
        M["revenue_growth_yoy"] = Metric(rev / prev.value - 1, period=f"{M['revenue_ttm'].period} vs {prev.period}",
                                         inputs=M["revenue_ttm"].inputs + prev.inputs)
    if rev_dates:
        last = rev_dates[-1]
        ya = _yoy_point(rev_dates, last)
        if ya is not None:
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
    if debt is None and f.balance_date is not None:
        # Deuda NO NULA reportada cerca (≈2 balances previos) o después del balance, de una fuente aceptable:
        # no se puede suponer 0. Un dato viejo (deuda ya repagada) o un 0 explícito van por el camino "SUPONE 0".
        lo = f.balance_date - timedelta(days=DEBT_LOOKBACK_DAYS)
        recent = [p for p in debt_points if p.value and rank(p) <= max(anchor_rank, 3) and p.period_end >= lo
                  and abs((p.period_end - f.balance_date).days) > CLUSTER_DAYS]
        f.debt_elsewhere = bool(recent)
        if recent:
            p = max(recent, key=lambda x: x.period_end)
            f.warnings.append(f"deuda {p.metric} reportada al {p.period_end} pero no a la fecha de balance "
                              f"{f.balance_date}: no se asume 0 (valuación bloqueada)")
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
    # Filing sin etiqueta de deuda (y sin deuda reciente en otra fecha): la valuación ya SUPONE 0 con alerta; los
    # ratios usan el mismo supuesto, explícito, en lugar de tratar apalancamiento y ROIC como faltantes.
    debt_r = debt
    if debt is None and cash is not None and f.balance_rank == 0 and not f.debt_elsewhere:
        f.debt_assumed_zero, debt_r = True, 0.0
    M["net_debt"] = Metric(debt_r - cash if debt_r is not None and cash is not None else None,
                           note="deuda SUPUESTA 0 (sin etiqueta de deuda en el filing)" if f.debt_assumed_zero else None)
    M["net_debt_to_ebitda"] = Metric(_div(M["net_debt"].value, ebitda) if ebitda and ebitda > 0 else None)
    if f.alt_balance_date is not None:
        # balance posterior parcial (no usado para valuar): igual se controla su apalancamiento (K7 con ambos)
        def at_alt(m: str) -> float | None:
            d = next((x for x in stocks[m] if abs((x - f.alt_balance_date).days) <= CLUSTER_DAYS), None)
            return stocks[m][d].value if d is not None else None
        c_alt = at_alt("cash")
        d_alt = at_alt("debt_total")
        if d_alt is None:
            comps = [v for v in (at_alt("debt_noncurrent"), at_alt("debt_current")) if v is not None]
            d_alt = sum(comps) if comps else None
        if c_alt is not None and d_alt is not None and ebitda and ebitda > 0:
            M["net_debt_to_ebitda_alt"] = Metric((d_alt - c_alt - (at_alt("short_term_investments") or 0.0)) / ebitda,
                                                 period=str(f.alt_balance_date),
                                                 note="balance posterior parcial de menor precedencia")
    M["interest_coverage"] = Metric(_div(ebit, M["interest_expense_ttm"].value))
    M["current_ratio"] = Metric(_div(M["current_assets"].value, M["current_liabilities"].value))
    tax_rate = _div(M["income_tax_ttm"].value, M["pretax_income_ttm"].value)
    tax_rate = min(max(tax_rate, 0.0), 0.35) if tax_rate is not None else 0.21
    invested = None
    if M["equity"].value is not None and debt_r is not None and cash is not None:
        invested = M["equity"].value + debt_r - cash
    if ebit is not None and ebit > 0 and invested is not None and invested <= 0:
        M["roic"] = Metric(None, note="n.m.: EBIT > 0 con capital invertido (equity+deuda-caja) <= 0")
    else:
        M["roic"] = Metric(_div(ebit * (1 - tax_rate), invested) if ebit is not None and invested and invested > 0
                           else None, note=f"NOPAT/(equity+deuda-caja); tasa {tax_rate:.0%}")
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
    if rev is not None and ebit is not None and \
            _period_end(M["revenue_ttm"]) == _period_end(M["operating_income_ttm"]):
        p_rev = _ttm_year_ago(series["revenue"], fy_by["revenue"], M["revenue_ttm"])
        p_oi = _ttm_year_ago(series["operating_income"], fy_by["operating_income"], M["operating_income_ttm"])
        if p_rev is not None and p_oi is not None and _period_end(p_rev) == _period_end(p_oi) and rev != p_rev.value:
            M["incremental_margin"] = Metric((ebit - p_oi.value) / (rev - p_rev.value), period="TTM vs TTM-1")

    key_needed = ["revenue_ttm", "operating_income_ttm", "ocf_ttm", "capex_ttm", "cash", "equity", "diluted_shares"]
    f.missing = [k for k in key_needed if (M.get(k) is None or M[k].value is None)]
    return f
