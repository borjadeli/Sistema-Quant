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


def _cadence(index) -> tuple[bool, np.ndarray]:  # noqa: ANN001
    """(es_diaria_regular, gaps en días calendario)."""
    d = pd.to_datetime(pd.Index(index)).sort_values()
    gaps = np.diff(d.values).astype("timedelta64[D]").astype(float)
    daily = len(gaps) > 0 and float(np.median(gaps)) <= 1.5 and float(np.max(gaps)) <= 5
    return daily, gaps


def performance(nav: pd.Series, bench: pd.Series | None = None, rf: float = 0.04,
                periods: float | None = None) -> dict:
    """nav, bench: series indexadas por fecha. Devuelve métricas; None cuando no hay historia suficiente.

    - Serie diaria regular: anualización clásica (252).
    - Serie mensual o IRREGULAR (comités mensuales + revisiones por evento): estimador en tiempo calendario.
      Cada retorno logarítmico se normaliza por la raíz de su intervalo (l_i/sqrt(dt_i)); la media anual es
      sum(l)/sum(dt)*365.25. No asume un intervalo único.
    - El benchmark se alinea por as-of (último precio <= cada fecha del NAV) sobre la MISMA ventana; nunca se usa
      un precio posterior a la fecha del NAV.
    """
    nav = nav.dropna()
    nav = nav[~pd.Index(nav.index).duplicated(keep="last")].sort_index()
    out: dict = {"start": str(nav.index[0]) if len(nav) else None, "end": str(nav.index[-1]) if len(nav) else None,
                 "n_obs": len(nav)}
    if len(nav) < 2:
        return out | {"note": "historia insuficiente"}
    daily, gaps = _cadence(nav.index)
    if periods is not None:
        daily = True
    ppy = periods or (252.0 if daily else periods_per_year(nav.index))
    out["periods_per_year"] = round(ppy, 2)
    out["method"] = "periodos_fijos" if daily else "tiempo_calendario"
    r = nav.pct_change().dropna()
    years = max((pd.Timestamp(nav.index[-1]) - pd.Timestamp(nav.index[0])).days / 365.25, 1 / 365)
    out["total_return"] = nav.iloc[-1] / nav.iloc[0] - 1
    out["cagr"] = (1 + out["total_return"]) ** (1 / years) - 1 if years >= 1 else None   # no anualizar < 1 año
    min_obs = 20 if daily else 12
    if daily:
        rf_p = (1 + rf) ** (1 / ppy) - 1
        out["volatility"] = float(r.std() * math.sqrt(ppy)) if len(r) > 1 else None
        out["sharpe"] = (float((r - rf_p).mean() / r.std() * math.sqrt(ppy))
                         if len(r) >= min_obs and r.std() > 0 else None)
    else:
        lr = np.log1p(r.values)
        dt = np.maximum(gaps, 1.0)
        z = lr / np.sqrt(dt)
        vol = float(np.std(z, ddof=1) * math.sqrt(365.25)) if len(z) > 1 else None
        mu = float(lr.sum() / dt.sum() * 365.25)
        out["volatility"] = vol
        out["sharpe"] = ((mu - math.log1p(rf)) / vol) if vol and len(lr) >= min_obs else None
    dd = nav / nav.cummax() - 1
    out["max_drawdown"] = float(dd.min())
    if bench is not None and len(bench.dropna()) > 1:
        b = bench.dropna()
        b.index = pd.to_datetime(b.index)
        b = b[~b.index.duplicated(keep="last")].sort_index()     # misma fecha desde dos fuentes de precios
        nav_ts = pd.Series(nav.values, index=pd.to_datetime(nav.index))
        b_al = b.reindex(nav_ts.index, method="ffill")          # as-of: último precio <= cada fecha del NAV
        out["bench_last_date"] = str(b.index[-1].date())
        if b_al.isna().any():
            out["bench_note"] = "benchmark sin precio en o antes del inicio del NAV: comparación no válida"
            return out
        if b.index[-1] < nav_ts.index[-1] - pd.Timedelta(days=4):
            out["bench_note"] = (f"benchmark desactualizado (último {b.index[-1].date()} vs NAV "
                                 f"{nav_ts.index[-1].date()}): comparación no válida")
            return out
        al = pd.concat([nav_ts, b_al], axis=1)
        out["bench_total_return"] = al.iloc[-1, 1] / al.iloc[0, 1] - 1
        out["excess_return"] = out["total_return"] - out["bench_total_return"]
        rr = al.pct_change().dropna()
        if len(rr) >= min_obs:
            beta = float(np.cov(rr.iloc[:, 0], rr.iloc[:, 1])[0, 1] / rr.iloc[:, 1].var())
            if daily:
                rf_p = (1 + rf) ** (1 / ppy) - 1
                alpha_ann = (rr.iloc[:, 0].mean() - rf_p - beta * (rr.iloc[:, 1].mean() - rf_p)) * ppy
                diff = rr.iloc[:, 0] - rr.iloc[:, 1]
                te = diff.std() * math.sqrt(ppy)
                ir = diff.mean() * ppy / te if te > 0 else None
            else:
                dt = np.maximum(gaps, 1.0)
                lp, lb = np.log1p(rr.iloc[:, 0].values), np.log1p(rr.iloc[:, 1].values)
                mu_p, mu_b = lp.sum() / dt.sum() * 365.25, lb.sum() / dt.sum() * 365.25
                rfl = math.log1p(rf)
                alpha_ann = (mu_p - rfl) - beta * (mu_b - rfl)
                te = float(np.std((lp - lb) / np.sqrt(dt), ddof=1) * math.sqrt(365.25))
                ir = (mu_p - mu_b) / te if te > 0 else None
            out.update(beta=beta, alpha_annual=alpha_ann, tracking_error=te, information_ratio=ir,
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
