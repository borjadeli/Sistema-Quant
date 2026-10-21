"""Métricas de performance y calibración de la cartera simulada."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def performance(nav: pd.Series, bench: pd.Series | None = None, rf: float = 0.04, periods: int = 252) -> dict:
    """nav, bench: series indexadas por fecha. Devuelve métricas; None cuando no hay historia suficiente."""
    nav = nav.dropna().sort_index()
    out: dict = {"start": str(nav.index[0]) if len(nav) else None, "end": str(nav.index[-1]) if len(nav) else None,
                 "n_obs": len(nav)}
    if len(nav) < 2:
        return out | {"note": "historia insuficiente"}
    r = nav.pct_change().dropna()
    years = max((pd.Timestamp(nav.index[-1]) - pd.Timestamp(nav.index[0])).days / 365.25, 1 / 365)
    out["total_return"] = nav.iloc[-1] / nav.iloc[0] - 1
    out["cagr"] = (1 + out["total_return"]) ** (1 / years) - 1 if years >= 1 else None   # no anualizar < 1 año
    out["volatility"] = float(r.std() * math.sqrt(periods)) if len(r) > 1 else None
    ex = r - rf / periods
    out["sharpe"] = float(ex.mean() / r.std() * math.sqrt(periods)) if len(r) > 20 and r.std() > 0 else None
    dd = nav / nav.cummax() - 1
    out["max_drawdown"] = float(dd.min())
    if bench is not None and len(bench.dropna()) > 1:
        b = bench.dropna().sort_index()
        al = pd.concat([nav, b], axis=1, join="inner").dropna()
        if len(al) > 1:
            out["bench_total_return"] = al.iloc[-1, 1] / al.iloc[0, 1] - 1
            out["excess_return"] = out["total_return"] - out["bench_total_return"]
            rr = al.pct_change().dropna()
            if len(rr) > 20:
                beta = float(np.cov(rr.iloc[:, 0], rr.iloc[:, 1])[0, 1] / rr.iloc[:, 1].var())
                alpha_d = rr.iloc[:, 0].mean() - rf / periods - beta * (rr.iloc[:, 1].mean() - rf / periods)
                te = (rr.iloc[:, 0] - rr.iloc[:, 1]).std() * math.sqrt(periods)
                out.update(beta=beta, alpha_annual=alpha_d * periods, tracking_error=te,
                           information_ratio=(rr.iloc[:, 0] - rr.iloc[:, 1]).mean() * periods / te if te > 0 else None,
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
