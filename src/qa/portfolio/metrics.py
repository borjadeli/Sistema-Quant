"""Métricas de performance y calibración de la cartera simulada."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def periods_per_year(index) -> float:  # noqa: ANN001
    """Frecuencia de observación inferida del índice (252 diario hábil, ~12 mensual, ...)."""
    d = pd.to_datetime(pd.Index(index)).sort_values()
    if len(d) < 2:
        return 252.0
    gap = float(np.median(np.diff(d.values).astype("timedelta64[D]").astype(float)))
    if gap <= 1.5:
        return 252.0           # serie diaria (días hábiles)
    return 365.25 / max(gap, 1.0)


def performance(nav: pd.Series, bench: pd.Series | None = None, rf: float = 0.04,
                periods: float | None = None) -> dict:
    """nav, bench: series indexadas por fecha. Devuelve métricas; None cuando no hay historia suficiente.

    - La frecuencia se infiere de las fechas del NAV (no se asume diaria).
    - La tasa libre de riesgo se convierte al intervalo real: (1+rf)^(1/periods) - 1.
    - El benchmark se alinea por as-of (último precio <= cada fecha del NAV) sobre la MISMA ventana.
    """
    nav = nav.dropna().sort_index()
    out: dict = {"start": str(nav.index[0]) if len(nav) else None, "end": str(nav.index[-1]) if len(nav) else None,
                 "n_obs": len(nav)}
    if len(nav) < 2:
        return out | {"note": "historia insuficiente"}
    ppy = periods or periods_per_year(nav.index)
    out["periods_per_year"] = round(ppy, 2)
    rf_p = (1 + rf) ** (1 / ppy) - 1
    r = nav.pct_change().dropna()
    years = max((pd.Timestamp(nav.index[-1]) - pd.Timestamp(nav.index[0])).days / 365.25, 1 / 365)
    out["total_return"] = nav.iloc[-1] / nav.iloc[0] - 1
    out["cagr"] = (1 + out["total_return"]) ** (1 / years) - 1 if years >= 1 else None   # no anualizar < 1 año
    out["volatility"] = float(r.std() * math.sqrt(ppy)) if len(r) > 1 else None
    min_obs = 20 if ppy > 50 else 12
    out["sharpe"] = (float((r - rf_p).mean() / r.std() * math.sqrt(ppy))
                     if len(r) >= min_obs and r.std() > 0 else None)
    dd = nav / nav.cummax() - 1
    out["max_drawdown"] = float(dd.min())
    if bench is not None and len(bench.dropna()) > 1:
        b = bench.dropna().sort_index()
        b.index = pd.to_datetime(b.index)
        nav_ts = pd.Series(nav.values, index=pd.to_datetime(nav.index))
        tol = pd.Timedelta(days=5)
        b_al = b.reindex(nav_ts.index, method="ffill")          # as-of: último precio disponible a cada fecha
        if pd.isna(b_al.iloc[0]) and b.index[0] - nav_ts.index[0] <= tol:
            b_al.iloc[0] = b.iloc[0]                            # inicio en fin de semana / feriado
        if b_al.isna().any() or b.index[-1] < nav_ts.index[-1] - tol:
            out["bench_note"] = "benchmark no cubre toda la ventana del NAV: comparación no válida"
            return out
        al = pd.concat([nav_ts, b_al], axis=1)
        out["bench_total_return"] = al.iloc[-1, 1] / al.iloc[0, 1] - 1
        out["excess_return"] = out["total_return"] - out["bench_total_return"]
        rr = al.pct_change().dropna()
        if len(rr) >= min_obs:
            beta = float(np.cov(rr.iloc[:, 0], rr.iloc[:, 1])[0, 1] / rr.iloc[:, 1].var())
            alpha_p = rr.iloc[:, 0].mean() - rf_p - beta * (rr.iloc[:, 1].mean() - rf_p)
            te = (rr.iloc[:, 0] - rr.iloc[:, 1]).std() * math.sqrt(ppy)
            out.update(beta=beta, alpha_annual=alpha_p * ppy, tracking_error=te,
                       information_ratio=(rr.iloc[:, 0] - rr.iloc[:, 1]).mean() * ppy / te if te > 0 else None,
                       alpha_note="alpha CAPM vs benchmark; alpha multifactor requiere factores (Fama-French) — pendiente")
    return out


def concentration(weights: dict[str, float]) -> dict:
    w = np.array([v for v in weights.values() if v > 0])
    if not len(w):
        return {"hhi": None, "effective_n": None, "top5": None}
    hhi = float((w ** 2).sum() / (w.sum() ** 2))
    return {"hhi": hhi, "effective_n": 1 / hhi, "top5": float(np.sort(w)[::-1][:5].sum())}


def turnover(trades: list[dict], avg_nav: float) -> float | None:
    """Rotación = min(compras, ventas) / NAV promedio."""
    if not avg_nav:
        return None
    buys = sum(t["quantity"] * t["price"] for t in trades if t["side"] == "BUY")
    sells = sum(t["quantity"] * t["price"] for t in trades if t["side"] == "SELL")
    return min(buys, sells) / avg_nav


def scenario_outcome(price_targets: dict[str, float], realized: float) -> str:
    """Escenario 'realizado' = el de precio objetivo más cercano (en log) al precio realizado."""
    return min(price_targets, key=lambda k: abs(math.log(realized) - math.log(max(price_targets[k], 1e-9))))


def brier(forecasts: list[tuple[dict[str, float], str]]) -> float | None:
    """forecasts: [({bear: p, base: p, bull: p}, escenario_realizado)]. Brier multiclase (0 = perfecto, 2 = peor)."""
    if not forecasts:
        return None
    tot = 0.0
    for probs, outcome in forecasts:
        tot += sum((p - (1.0 if k == outcome else 0.0)) ** 2 for k, p in probs.items())
    return tot / len(forecasts)
