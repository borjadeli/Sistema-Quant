"""Proveedores sin red: CSV manual (con fuente obligatoria), import de investigación y fixtures sintéticos.

CSV manual (data/manual/*.csv) — para emisores fuera de EDGAR (TSX/SEDAR+, TSE, TWSE...). Columnas:
  entity,metric,value,unit,currency,period_end,period_type,fiscal_label,source_name,source_url,document_type,known_at
Filas sin source_url o sin known_at se rechazan: no hay dato sin fuente.
"""
from __future__ import annotations

import csv
import json
import re
from datetime import date
from pathlib import Path

from qa.config import DATA_DIR, FIXTURES_DIR, RESEARCH_DIR
from qa.provenance import DataKind, DataPoint, Source, Verification
from qa.universe.model import Company

REQUIRED = ["entity", "metric", "value", "unit", "period_end", "period_type", "source_name", "source_url", "known_at"]


class ManualCsvProvider:
    name = "manual_csv"

    def __init__(self, folder: Path = DATA_DIR / "manual"):
        self.folder = folder

    def supports(self, company: Company) -> bool:
        return any(p.entity == company.id for p in self._load())

    def _load(self) -> list[DataPoint]:
        out = []
        for path in sorted(self.folder.glob("*.csv")) if self.folder.exists() else []:
            out += load_manual_csv(path)
        return out

    def get_facts(self, company: Company) -> list[DataPoint]:
        return [p for p in self._load() if p.entity == company.id]


def load_manual_csv(path: Path) -> list[DataPoint]:
    out = []
    with open(path, encoding="utf-8") as f:
        for i, row in enumerate(csv.DictReader(f), start=2):
            missing = [k for k in REQUIRED if not (row.get(k) or "").strip()]
            if missing:
                raise ValueError(f"{path.name}:{i} faltan campos obligatorios {missing}")
            out.append(DataPoint(
                entity=row["entity"], metric=row["metric"], value=float(row["value"]), unit=row["unit"],
                currency=row.get("currency") or None, period_end=date.fromisoformat(row["period_end"]),
                period_type=row["period_type"], fiscal_label=row.get("fiscal_label") or None,
                kind=DataKind.ORIGINAL,
                verification=Verification(row.get("verification") or Verification.COMPANY_STATEMENT.value),
                source=Source(name=row["source_name"], url=row["source_url"], document_type=row.get("document_type")),
                known_at=date.fromisoformat(row["known_at"]), author=row.get("author") or "manual_entry"))
    return out


# --------------------------------------------------------------------------- research import
_UNIT_RE = re.compile(r"^(USD|CAD|EUR|JPY|GBP|AUD)\s+(millions|thousands|billions)?", re.I)
_SCALE = {"millions": 1e6, "thousands": 1e3, "billions": 1e9, None: 1.0}


def import_seed_research(path: Path = RESEARCH_DIR / "seed_companies_verified.json") -> list[DataPoint]:
    """Convierte la investigación web de las semillas en DataPoints.

    Solo se importan valores numéricos explícitos con URL de fuente. Se etiquetan `search_summary`
    porque fueron leídos en resúmenes del buscador, no en el documento primario: el auditor debe
    contrastarlos con EDGAR/SEDAR cuando la red lo permita.
    """
    if not path.exists():
        return []
    rows = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for c in rows:
        q = c.get("latest_reported_quarter") or {}
        if not q.get("source_url") or not q.get("period_end_date"):
            continue
        m = _UNIT_RE.match(q.get("revenue_unit") or "")
        if not m:
            continue
        ccy, scale = m.group(1).upper(), _SCALE[(m.group(2) or "").lower() or None]
        known = _parse_date(q.get("source_date")) or date.fromisoformat(q["period_end_date"])
        for metric, field in (("revenue", "revenue"), ("net_income", "gaap_net_income"),
                              ("cash", "cash_and_equivalents"), ("debt_reported", "total_debt")):
            v = q.get(field)
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                continue
            out.append(DataPoint(
                entity=c["ticker"], metric=metric, value=float(v) * scale, unit=ccy, currency=ccy,
                period_end=date.fromisoformat(q["period_end_date"]), period_type="Q",
                fiscal_label=q.get("period_label"), kind=DataKind.ORIGINAL,
                verification=Verification.SEARCH_SUMMARY,
                source=Source(name="web_research_seed", url=q["source_url"], document_type="press_release"),
                known_at=known, author="research_agent",
                note="leído en resumen de buscador; verificar contra documento primario"))
    return out


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    for fmt_len in (10, 7):
        try:
            part = s[:fmt_len]
            return date.fromisoformat(part if fmt_len == 10 else part + "-28")
        except ValueError:
            continue
    return None


# --------------------------------------------------------------------------- fixtures sintéticos
class FixtureProvider:
    """Datos SINTÉTICOS para tests y demo offline. Cada punto lleva verification=synthetic_test_data.

    Nunca representan a una empresa real: las entidades usan el prefijo `TEST_`.
    """
    name = "fixture"

    def __init__(self, path: Path = FIXTURES_DIR / "synthetic_companies.json"):
        self.path = path

    def supports(self, company: Company) -> bool:
        return company.id.startswith("TEST_")

    def get_facts(self, company: Company) -> list[DataPoint]:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        rows = data["companies"].get(company.id, {}).get("facts", [])
        return [DataPoint(entity=company.id, metric=r["metric"], value=r["value"], unit="USD", currency="USD",
                          period_end=date.fromisoformat(r["period_end"]), period_type=r["period_type"],
                          kind=DataKind.ORIGINAL, verification=Verification.SYNTHETIC,
                          source=Source(name="synthetic_fixture", document_type="fixture"),
                          known_at=date.fromisoformat(r["known_at"]), note="SYNTHETIC — NOT REAL DATA")
                for r in rows]
