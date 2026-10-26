"""Proveedores sin red: CSV manual (con fuente obligatoria), import de investigación y fixtures sintéticos.

CSV manual (data/manual/*.csv) — para emisores fuera de EDGAR (TSX/SEDAR+, TSE, TWSE...). Columnas:
  entity,metric,value,unit,currency,period_end,period_type,fiscal_label,source_name,source_url,document_type,known_at
Filas sin source_url o sin known_at se rechazan: no hay dato sin fuente.
"""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import date, timedelta
from pathlib import Path

from qa.config import DATA_DIR, FIXTURES_DIR, RESEARCH_DIR
from qa.provenance import DataKind, DataPoint, Source, Verification
from qa.universe.model import Company

REQUIRED = ["entity", "metric", "value", "unit", "period_end", "period_type", "source_name", "source_url", "known_at"]
PERIOD_TYPES = {"Q", "H", "9M", "FY", "instant"}
NON_MONETARY = {"diluted_shares", "shares_outstanding"}
RESERVED_SOURCES = {"sec_edgar", "web_research_seed", "synthetic_fixture"}   # nombres de fuente de otros proveedores


class ManualCsvProvider:
    """Datos cargados a mano con fuente. Filas inválidas se RECHAZAN individualmente (quedan en `rejected`)
    sin abortar la ingesta del resto del universo.

    Proveedor EDITABLE: el contenido actual de los CSV es la verdad; la ingesta reconcilia la base (borra filas
    que ya no están o que ahora se rechazan). Si un archivo no se pudo leer entero (`file_errors`), no se
    reconcilia (no se borran datos por un archivo ilegible) y se avisa."""
    name = "manual_csv"
    editable = True

    def __init__(self, folder: Path = DATA_DIR / "manual"):
        self.folder = folder
        self._cache: list[DataPoint] | None = None
        self.rejected: list[str] = []
        self.file_errors: list[str] = []

    def _load(self) -> list[DataPoint]:
        if self._cache is None:
            out = []
            for path in sorted(self.folder.glob("*.csv")) if self.folder.exists() else []:
                pts, bad, file_err = load_manual_csv(path)
                out += pts
                self.rejected += bad
                if file_err:
                    self.file_errors.append(file_err)
            self._cache = out
        return self._cache

    @property
    def reconcile_safe(self) -> bool:
        self._load()
        return not self.file_errors

    def supports(self, company: Company) -> bool:
        return any(p.entity == company.id for p in self._load())

    def get_facts(self, company: Company) -> list[DataPoint]:
        return [p for p in self._load() if p.entity == company.id]


def _parse_row(row: dict, where: str) -> DataPoint:
    from qa.fundamentals.metrics import FLOW_METRICS, STOCK_METRICS  # noqa: PLC0415 — mismo vocabulario
    missing = [k for k in REQUIRED if not (row.get(k) or "").strip()]
    metric = (row.get("metric") or "").strip()
    if metric not in NON_MONETARY and not (row.get("currency") or "").strip():
        missing.append("currency")
    if missing:
        raise ValueError(f"{where}: faltan campos obligatorios {missing}")
    ptype = row["period_type"].strip()
    if ptype not in PERIOD_TYPES:
        raise ValueError(f"{where}: period_type '{ptype}' inválido (usar {sorted(PERIOD_TYPES)})")
    if row["source_name"].strip().lower() in RESERVED_SOURCES:
        raise ValueError(f"{where}: source_name '{row['source_name']}' está reservado para otro proveedor: citar el "
                         "documento (p.ej. 'SEC EDGAR 40-F', 'SEDAR+ MD&A')")
    if metric not in set(FLOW_METRICS) | set(STOCK_METRICS) | {"diluted_shares"}:
        raise ValueError(f"{where}: métrica desconocida '{metric}'")
    if metric in STOCK_METRICS and ptype != "instant":
        raise ValueError(f"{where}: {metric} es un saldo de balance: period_type debe ser 'instant' (no '{ptype}')")
    if metric not in STOCK_METRICS and ptype == "instant":
        raise ValueError(f"{where}: {metric} es un flujo del período: period_type no puede ser 'instant'")
    period_end = date.fromisoformat(row["period_end"].strip())
    known_at = date.fromisoformat(row["known_at"].strip())
    if known_at < period_end:
        raise ValueError(f"{where}: known_at {known_at} anterior al cierre del período {period_end} (look-ahead)")
    if "," in row["value"]:
        raise ValueError(f"{where}: valor '{row['value']}' ambiguo: usar punto decimal y sin separador de miles")
    value = float(row["value"].strip())
    return DataPoint(
        entity=row["entity"].strip(), metric=metric, value=value, unit=row["unit"].strip(),
        currency=(row.get("currency") or "").strip() or None, period_end=period_end, period_type=ptype,
        fiscal_label=row.get("fiscal_label") or None, kind=DataKind.ORIGINAL,
        verification=Verification(row.get("verification") or Verification.COMPANY_STATEMENT.value),
        source=Source(name=row["source_name"], url=row["source_url"], document_type=row.get("document_type")),
        known_at=known_at, author=row.get("author") or "manual_entry")


def load_manual_csv(path: Path) -> tuple[list[DataPoint], list[str], str | None]:
    """Devuelve (datapoints válidos, motivos de rechazo por fila con archivo:línea, error de archivo o None).

    Acepta UTF-8 con o sin BOM (Excel "CSV UTF-8"). Es un ERROR DE ARCHIVO (no aborta la ingesta, se conservan las
    filas válidas leídas antes y la base no se reconcilia con ese proveedor): otra codificación, comillas sin
    cerrar, encabezado sin las columnas obligatorias (p.ej. separador ';' de un Excel en español) o filas con más
    columnas que el encabezado."""
    out, rejected = [], []
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        ln = raw[:e.start].count(b"\n") + 1
        err = (f"{path.name}:{ln}: no es UTF-8 ({e.reason} en el byte {e.start}): guardar como 'CSV UTF-8'; "
               "no se usa el archivo")
        return out, rejected + [err], err
    reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
    start_line = 2
    try:
        header = reader.fieldnames or []
        lacking = [k for k in REQUIRED if k not in header]
        if lacking:
            sep = " (¿separador ';'? usar ',')" if len(header) <= 1 and ";" in (header[0] if header else "") else ""
            err = f"{path.name}:1: encabezado sin columnas obligatorias {lacking}{sep}; no se usa el archivo"
            return out, rejected + [err], err
        for row in reader:
            where = f"{path.name}:{start_line}"
            start_line = reader.line_num + 1
            if None in row:                      # más campos que columnas: comas sin comillas, columnas corridas
                err = f"{where}: la fila tiene más campos que el encabezado; no se usa el resto del archivo"
                return out, rejected + [err], err
            try:
                out.append(_parse_row(row, where))
            except (ValueError, KeyError) as e:
                msg = str(e)
                rejected.append(msg if msg.startswith(where) else f"{where}: {type(e).__name__}: {msg}")
    except csv.Error as e:
        err = f"{path.name}:{start_line}: CSV mal formado ({e}): se descarta desde esa línea"
        return out, rejected + [err], err
    return out, rejected, None


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
        period_end = date.fromisoformat(q["period_end_date"])
        known = _parse_date(q.get("source_date"))
        if known is None or known <= period_end:
            continue          # sin fecha de publicación confiable (posterior al cierre) no hay point-in-time
        # flujos = trimestre; caja y deuda = saldos (instant) a la fecha de cierre
        for metric, field, ptype in (("revenue", "revenue", "Q"), ("net_income", "gaap_net_income", "Q"),
                                     ("cash", "cash_and_equivalents", "instant"),
                                     ("debt_total", "total_debt", "instant")):
            v = q.get(field)
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                continue
            out.append(DataPoint(
                entity=c["ticker"], metric=metric, value=float(v) * scale, unit=ccy, currency=ccy,
                period_end=period_end, period_type=ptype,
                fiscal_label=q.get("period_label"), kind=DataKind.ORIGINAL,
                verification=Verification.SEARCH_SUMMARY,
                source=Source(name="web_research_seed", url=q["source_url"], document_type="press_release"),
                known_at=known, author="research_agent",
                note="leído en resumen de buscador; verificar contra documento primario"))
    return out


_YMD = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?!\d)")
_YM = re.compile(r"^(\d{4})-(\d{2})(?![\d-])")


def _parse_date(s: str | None) -> date | None:
    """YYYY-MM-DD exacto; YYYY-MM se interpreta como el ÚLTIMO día del mes (conservador: nunca antes de lo real).
    Mes o día inválidos -> None (no se importa)."""
    if not s:
        return None
    s = str(s).strip()
    m = _YMD.match(s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = _YM.match(s)
    if not m:
        return None
    y, mth = int(m.group(1)), int(m.group(2))
    if not 1 <= mth <= 12:
        return None
    return date(y + (mth == 12), mth % 12 + 1, 1) - timedelta(days=1)


# --------------------------------------------------------------------------- fixtures sintéticos
class FixtureProvider:
    """Datos SINTÉTICOS para tests y demo offline. Cada punto lleva verification=synthetic_test_data.

    Nunca representan a una empresa real: las entidades usan el prefijo `TEST_`.
    """
    name = "fixture"
    editable = True
    reconcile_safe = True

    def __init__(self, path: Path = FIXTURES_DIR / "synthetic_companies.json"):
        self.path = path

    def supports(self, company: Company) -> bool:
        return company.id.startswith("TEST_")

    def get_facts(self, company: Company) -> list[DataPoint]:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        rows = data["companies"].get(company.id, {}).get("facts", [])
        shares = {"diluted_shares", "shares_outstanding"}
        return [DataPoint(entity=company.id, metric=r["metric"], value=r["value"],
                          unit="shares" if r["metric"] in shares else "USD",
                          currency=None if r["metric"] in shares else "USD",
                          period_end=date.fromisoformat(r["period_end"]), period_type=r["period_type"],
                          kind=DataKind.ORIGINAL, verification=Verification.SYNTHETIC,
                          source=Source(name="synthetic_fixture", document_type="fixture"),
                          known_at=date.fromisoformat(r["known_at"]), note="SYNTHETIC — NOT REAL DATA")
                for r in rows]
