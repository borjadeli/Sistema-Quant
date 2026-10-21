"""Interfaces de proveedores de datos (sustituibles: SEC, Yahoo, IBKR, FMP, Polygon, CSV manual...)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol, runtime_checkable

import pandas as pd

from qa.provenance import DataPoint
from qa.universe.model import Company


class ProviderUnavailable(RuntimeError):
    """El proveedor no está accesible (red, credenciales, cobertura). Nunca se reemplaza por datos inventados."""


@dataclass
class MarketSnapshotData:
    company_id: str
    as_of: datetime
    price: float | None
    currency: str | None
    shares_outstanding: float | None
    market_cap: float | None
    market_cap_usd: float | None
    adv_usd_20d: float | None
    source: str


@dataclass
class OptionQuoteData:
    underlying: str
    retrieved_at: datetime
    expiry: date
    strike: float
    right: str
    bid: float | None
    ask: float | None
    last: float | None
    volume: float | None
    open_interest: float | None
    implied_vol: float | None
    underlying_price: float | None
    source: str


@dataclass
class OptionChain:
    underlying: str
    retrieved_at: datetime
    underlying_price: float | None
    quotes: list[OptionQuoteData] = field(default_factory=list)
    source: str = ""


@runtime_checkable
class FundamentalsProvider(Protocol):
    name: str

    def supports(self, company: Company) -> bool: ...
    def get_facts(self, company: Company) -> list[DataPoint]: ...


@runtime_checkable
class PriceProvider(Protocol):
    name: str

    def history(self, symbol: str, start: date, end: date | None = None) -> pd.DataFrame: ...
    def snapshot(self, company: Company) -> MarketSnapshotData: ...


@runtime_checkable
class OptionsProvider(Protocol):
    name: str

    def chain(self, symbol: str, max_expiries: int = 6) -> OptionChain: ...
