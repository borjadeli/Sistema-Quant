"""Registro de proveedores e ingesta. Cambiar de proveedor = cambiar config/settings.yaml."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy.orm import Session

from qa.config import settings
from qa.db import repo
from qa.providers.base import ProviderUnavailable
from qa.providers.manual import FixtureProvider, ManualCsvProvider
from qa.providers.sec_edgar import SecEdgarProvider
from qa.providers.yahoo import YahooProvider
from qa.universe.model import Company

log = logging.getLogger(__name__)

FUNDAMENTALS = {"sec_edgar": SecEdgarProvider, "manual_csv": ManualCsvProvider, "fixture": FixtureProvider}
PRICES = {"yahoo": YahooProvider}


@dataclass
class IngestReport:
    ok: dict[str, list[str]] = field(default_factory=dict)        # company -> pasos exitosos
    failed: dict[str, list[str]] = field(default_factory=dict)    # company -> errores (sin inventar reemplazos)

    def add(self, cid: str, step: str, err: str | None = None) -> None:
        (self.failed if err else self.ok).setdefault(cid, []).append(f"{step}: {err}" if err else step)


def fundamentals_providers() -> list:
    return [FUNDAMENTALS[n]() for n in settings()["providers"]["fundamentals"] if n in FUNDAMENTALS]


def price_provider():
    return PRICES[settings()["providers"]["prices"][0]]()


def ingest_company(s: Session, c: Company, report: IngestReport, history_days: int = 3 * 365) -> None:
    for prov in fundamentals_providers():
        try:
            if not prov.supports(c):
                continue
            pts = prov.get_facts(c)
            repo.upsert_datapoints(s, pts)
            report.add(c.id, f"fundamentals[{prov.name}]={len(pts)}")
        except ProviderUnavailable as e:
            report.add(c.id, f"fundamentals[{prov.name}]", str(e))
    pp = price_provider()
    try:
        snap = pp.snapshot(c)
        repo.save_snapshot(s, snap)
        report.add(c.id, f"snapshot[{pp.name}]")
        df = pp.history(c.yahoo_symbol, start=date.today() - timedelta(days=history_days))
        repo.save_prices(s, c.yahoo_symbol, df, pp.name, snap.currency)
        report.add(c.id, f"prices[{pp.name}]={len(df)}")
    except ProviderUnavailable as e:
        report.add(c.id, f"market[{pp.name}]", str(e))


def ingest_benchmarks(s: Session, report: IngestReport, history_days: int = 3 * 365) -> None:
    pp = price_provider()
    for sym in settings()["portfolio"]["benchmarks"]:
        try:
            df = pp.history(sym, start=date.today() - timedelta(days=history_days))
            repo.save_prices(s, sym, df, pp.name, "USD")
            report.add(sym, "benchmark")
        except ProviderUnavailable as e:
            report.add(sym, "benchmark", str(e))
