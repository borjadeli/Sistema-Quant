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
    warnings: dict[str, list[str]] = field(default_factory=dict)  # company -> avisos de calidad (datos incompletos)

    def add(self, cid: str, step: str, err: str | None = None) -> None:
        (self.failed if err else self.ok).setdefault(cid, []).append(f"{step}: {err}" if err else step)


_PROVIDERS: list | None = None


def fundamentals_providers() -> list:
    """Instancias únicas por proceso (el CSV manual se parsea una vez por ingesta, no por empresa)."""
    global _PROVIDERS
    if _PROVIDERS is None:
        _PROVIDERS = [FUNDAMENTALS[n]() for n in settings()["providers"]["fundamentals"] if n in FUNDAMENTALS]
    return _PROVIDERS


def price_provider():
    return PRICES[settings()["providers"]["prices"][0]]()


OPTIONS = {"yahoo": YahooProvider}


def options_provider():
    """Proveedor de cadenas de opciones según settings.providers.options (el primero configurado y conocido)."""
    names = settings()["providers"].get("options") or []
    for n in names:
        if n in OPTIONS:
            return OPTIONS[n]()
    raise ProviderUnavailable(f"sin proveedor de opciones configurado/soportado (providers.options={names}; "
                              f"disponibles: {sorted(OPTIONS)})")


def ingest_company(s: Session, c: Company, report: IngestReport, history_days: int = 3 * 365) -> None:
    for prov in fundamentals_providers():
        try:
            if getattr(prov, "editable", False):
                # proveedor editable: se reconcilia SIEMPRE (también con lote vacío) para borrar filas huérfanas
                pts = prov.get_facts(c) if prov.supports(c) else []
                if prov.reconcile_safe:
                    repo.upsert_datapoints(s, pts, provider=prov.name, entities={c.id})
                else:                     # lote incompleto: solo agrega/actualiza, nunca borra
                    repo.upsert_datapoints(s, pts, provider=prov.name, entities={c.id}, delete=False)
                    report.add(c.id, f"fundamentals[{prov.name}]", "archivos ilegibles: no se reconcilian filas viejas "
                                                                    f"({'; '.join(prov.file_errors)[:300]})")
                if pts:
                    report.add(c.id, f"fundamentals[{prov.name}]={len(pts)}")
                continue
            if not prov.supports(c):
                continue
            pts = prov.get_facts(c)
            repo.upsert_datapoints(s, pts, provider=prov.name)
            report.add(c.id, f"fundamentals[{prov.name}]={len(pts)}")
            gap = getattr(prov, "gaps", {}).get(c.id)
            if gap:
                report.warnings.setdefault(c.id, []).append(gap)
                _gap_event(s, c.id, gap)
        except ProviderUnavailable as e:
            report.add(c.id, f"fundamentals[{prov.name}]", str(e))
        except (ValueError, KeyError) as e:      # datos mal formados de UN proveedor no abortan la ingesta
            report.add(c.id, f"fundamentals[{prov.name}]", f"datos inválidos: {e}")
    pp = price_provider()
    try:
        snap = pp.snapshot(c)
        repo.save_snapshot(s, snap)
        report.add(c.id, f"snapshot[{pp.name}]")
        df = pp.history(c.yahoo_symbol, start=date.today() - timedelta(days=history_days))
        repo.save_prices(s, c.yahoo_symbol, df, pp.name, df.attrs.get("currency") or snap.currency)
        report.add(c.id, f"prices[{pp.name}]={len(df)}")
    except ProviderUnavailable as e:
        report.add(c.id, f"market[{pp.name}]", str(e))
        return
    # moneda de reporte != cotización (p.ej. 40-F en CAD que cotiza en USD): se guarda el tipo de cambio EXPLÍCITO
    # (serie histórica, para usarlo point-in-time); nunca se asume 1
    for rep_ccy in reporting_currencies(s, c.id) - {snap.currency, None}:
        sym = fx_symbol(rep_ccy, snap.currency)
        try:
            fx = pp.history(f"{rep_ccy}{snap.currency}=X", start=date.today() - timedelta(days=history_days))
            repo.save_prices(s, sym, fx, pp.name, snap.currency)
            report.add(c.id, f"fx[{sym}]={len(fx)}")
        except ProviderUnavailable as e:
            report.add(c.id, f"fx[{sym}]", str(e))


def fx_symbol(report_ccy: str, quote_ccy: str) -> str:
    """Símbolo interno de la serie de tipo de cambio: unidades de `quote_ccy` por 1 de `report_ccy`."""
    return f"FX:{report_ccy}{quote_ccy}"


def reporting_currencies(s: Session, cid: str) -> set[str | None]:
    from sqlalchemy import select  # noqa: PLC0415

    from qa.db.schema import DataPointRow  # noqa: PLC0415
    return set(s.scalars(select(DataPointRow.currency).where(DataPointRow.entity == cid,
                                                             DataPointRow.currency.is_not(None)).distinct()))


def _gap_event(s: Session, cid: str, gap: str) -> None:
    """Aviso en el feed (lo ven los agentes y el dashboard): faltan datos de un filing ya presentado."""
    from sqlalchemy import select  # noqa: PLC0415

    from qa.config import utc_today  # noqa: PLC0415
    from qa.db.schema import FeedEvent  # noqa: PLC0415
    title = "Datos incompletos: " + gap.split(" (período")[0].replace("el XBRL de la SEC (companyfacts) no incluye ", "falta ")
    if s.scalars(select(FeedEvent).where(FeedEvent.company_id == cid, FeedEvent.event_type == "data_gap",
                                         FeedEvent.title == title)).first() is None:
        s.add(FeedEvent(event_date=utc_today(), company_id=cid, event_type="data_gap", title=title, detail=gap,
                        verification="system", impact="unknown", triggers_review=True))


def ingest_benchmarks(s: Session, report: IngestReport, history_days: int = 3 * 365) -> None:
    pp = price_provider()
    for sym in settings()["portfolio"]["benchmarks"]:
        try:
            df = pp.history(sym, start=date.today() - timedelta(days=history_days))
            repo.save_prices(s, sym, df, pp.name, "USD")
            report.add(sym, "benchmark")
        except ProviderUnavailable as e:
            report.add(sym, "benchmark", str(e))
