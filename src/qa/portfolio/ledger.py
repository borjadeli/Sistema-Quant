"""Cartera simulada (paper trading). Append-only. NUNCA envía órdenes reales.

Cada operación conserva: recomendación que la originó, precio, fuente y timestamp del precio, comisiones y slippage.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from qa.config import settings
from qa.db.schema import PaperTrade, PortfolioSnapshot


class LiveTradingBlocked(RuntimeError):
    pass


@dataclass
class Quote:
    price: float
    time: datetime
    source: str
    liquidity: str = "mid"          # large | mid | small -> slippage


def assert_simulated() -> None:
    if settings()["portfolio"].get("live_trading_enabled"):
        raise LiveTradingBlocked("live_trading_enabled=true pero no hay integración de órdenes reales: "
                                 "requiere implementación y autorización explícita (regla 14).")


def holdings(s: Session, portfolio: str) -> dict[str, float]:
    pos: dict[str, float] = {}
    for t in s.scalars(select(PaperTrade).where(PaperTrade.portfolio == portfolio)):
        pos[t.symbol] = pos.get(t.symbol, 0.0) + (t.quantity if t.side == "BUY" else -t.quantity)
    return {k: v for k, v in pos.items() if abs(v) > 1e-9}


def cash(s: Session, portfolio: str) -> float:
    c = settings()["portfolio"]["initial_capital_usd"]
    for t in s.scalars(select(PaperTrade).where(PaperTrade.portfolio == portfolio)):
        gross = t.quantity * t.price
        c += (-gross if t.side == "BUY" else gross) - t.fees
    return c


def nav(s: Session, portfolio: str, quotes: dict[str, Quote]) -> float:
    h = holdings(s, portfolio)
    missing = [k for k in h if k not in quotes]
    if missing:
        raise ValueError(f"sin cotización para posiciones: {missing}")
    return cash(s, portfolio) + sum(q * quotes[k].price for k, q in h.items())


def min_trade_fraction() -> float:
    """Ajustes menores a esta fracción del NAV no se operan. Es la MISMA banda que define HOLD en las
    recomendaciones: una recomendación HOLD nunca genera una operación."""
    return float(settings()["portfolio"].get("min_trade_pct_nav", 0.5)) / 100


def rebalance(s: Session, portfolio: str, targets: dict[str, float], quotes: dict[str, Quote],
              rec_ids: dict[str, int] | None = None, now: datetime | None = None) -> list[PaperTrade]:
    """Lleva la cartera a los pesos objetivo. Ventas primero, luego compras (sin apalancamiento)."""
    assert_simulated()
    cfg = settings()["portfolio"]
    now = now or datetime.now(timezone.utc)
    h = holdings(s, portfolio)
    for k in set(h) | set(targets):
        if k not in quotes:
            raise ValueError(f"sin cotización para {k}: no se opera con precios desconocidos")
    total = nav(s, portfolio, quotes)
    orders = []
    for k in set(h) | set(targets):
        q = quotes[k]
        target_qty = targets.get(k, 0.0) * total / q.price
        diff = target_qty - h.get(k, 0.0)
        if abs(diff * q.price) < min_trade_fraction() * total:   # banda sin operación (= banda HOLD)
            continue
        orders.append((k, diff))
    orders.sort(key=lambda x: x[1])                   # ventas (negativas) primero
    trades = []
    for k, diff in orders:
        q = quotes[k]
        slip_bps = cfg["slippage_bps_by_liquidity"].get(q.liquidity, 15)
        side = "BUY" if diff > 0 else "SELL"
        px = q.price * (1 + slip_bps / 1e4) if side == "BUY" else q.price * (1 - slip_bps / 1e4)
        qty = abs(diff)
        if side == "BUY":
            avail = cash(s, portfolio) - 1e-6
            max_qty = avail / (px * (1 + cfg["commission_bps"] / 1e4))
            qty = min(qty, max(max_qty, 0.0))
            if qty <= 0:
                continue
        fees = qty * px * cfg["commission_bps"] / 1e4
        t = PaperTrade(portfolio=portfolio, recommendation_id=(rec_ids or {}).get(k), symbol=k, side=side,
                       quantity=qty, price=px, price_source=q.source, price_time=q.time, fees=fees,
                       slippage=abs(px - q.price) * qty, mode="simulated", created_at=now)
        s.add(t)
        s.flush()
        trades.append(t)
    return trades


def mark_to_market(s: Session, portfolio: str, d: date, quotes: dict[str, Quote]) -> PortfolioSnapshot:
    h = holdings(s, portfolio)
    total = nav(s, portfolio, quotes)
    pos = {k: {"qty": q, "price": quotes[k].price, "value": q * quotes[k].price,
               "weight": q * quotes[k].price / total, "price_source": quotes[k].source} for k, q in h.items()}
    snap = s.scalars(select(PortfolioSnapshot).where(PortfolioSnapshot.portfolio == portfolio,
                                                     PortfolioSnapshot.date == d)).first()
    if snap is None:
        snap = PortfolioSnapshot(portfolio=portfolio, date=d, nav=total, cash=cash(s, portfolio), positions=pos)
        s.add(snap)
    else:
        snap.nav, snap.cash, snap.positions = total, cash(s, portfolio), pos
    return snap
