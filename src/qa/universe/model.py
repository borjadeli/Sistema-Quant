"""Universo de inversión: carga, validación y relaciones entre empresas."""
from __future__ import annotations

from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from qa.config import RELATIONSHIPS_PATH, UNIVERSE_PATH
from qa.map.model import IndustryMap
from qa.provenance import Verification

Level = Literal["H", "M", "L", "N"]
LEVEL_VALUE = {"H": 1.0, "M": 0.6, "L": 0.25, "N": 0.0}

# Sufijos de Yahoo por bolsa (solo para construir el símbolo de datos si no se especifica).
YAHOO_SUFFIX = {"TSE": ".T", "TWSE": ".TW", "TPEX": ".TWO", "KRX": ".KS", "HKEX": ".HK", "ASX": ".AX",
                "TSX": ".TO", "LSE": ".L", "XETRA": ".DE", "SZSE": ".SZ", "SSE": ".SS", "B3": ".SA"}
US_EXCHANGES = {"NASDAQ", "NYSE", "NYSEAMERICAN"}
# Moneda de cotización por bolsa (para altas de Discovery; nunca se asume USD fuera de EE.UU.).
EXCHANGE_CURRENCY = {"NASDAQ": "USD", "NYSE": "USD", "NYSEAMERICAN": "USD", "TSE": "JPY", "TWSE": "TWD",
                     "TPEX": "TWD", "KRX": "KRW", "KOSDAQ": "KRW", "HKEX": "HKD", "ASX": "AUD", "TSX": "CAD",
                     "TSXV": "CAD", "LSE": "GBP", "XETRA": "EUR", "EURONEXT": "EUR", "BIT": "EUR", "HEL": "EUR",
                     "SIX": "CHF", "CPH": "DKK", "STO": "SEK", "OSE": "NOK", "SZSE": "CNY", "SSE": "CNY",
                     "B3": "BRL", "NSE": "INR", "BSE": "INR"}


class Exposure(BaseModel):
    ai: Level = "N"
    energy: Level = "N"
    commodities: Level = "N"
    basis: str = "analyst_prior"      # se reemplaza por 'reported_segments' cuando se cuantifica


class Company(BaseModel):
    ticker: str
    name: str
    exchange: str
    country: str
    currency: str
    nodes: list[str]
    exposure: Exposure
    yahoo: str | None = None
    seed: bool = False
    status: Literal["active", "candidate", "delisted", "excluded"] = "active"
    tags: list[str] = Field(default_factory=list)
    ibkr: Literal["yes", "limited", "unknown", "no"] | None = None
    verify: str | None = None
    notes: str | None = None
    verification: Verification = Verification.ANALYST_PRIOR

    @property
    def id(self) -> str:
        return self.ticker

    @property
    def yahoo_symbol(self) -> str:
        if self.yahoo:
            return self.yahoo
        if self.exchange in US_EXCHANGES or "." in self.ticker:
            return self.ticker
        return self.ticker + YAHOO_SUFFIX.get(self.exchange, "")

    @property
    def is_us_listed(self) -> bool:
        return self.exchange in US_EXCHANGES

    @property
    def ibkr_accessible(self) -> str:
        if self.ibkr:
            return self.ibkr
        return "yes" if self.exchange in US_EXCHANGES | {"TSX", "LSE", "XETRA", "EURONEXT", "SIX", "TSE",
                                                         "HKEX", "ASX", "BIT", "CPH", "HEL"} else "unknown"

    @property
    def primary_node(self) -> str:
        return self.nodes[0]

    def ai_energy_commodity_vector(self) -> tuple[float, float, float]:
        e = self.exposure
        return LEVEL_VALUE[e.ai], LEVEL_VALUE[e.energy], LEVEL_VALUE[e.commodities]


class Relationship(BaseModel):
    source: str                 # proveedor (ticker o nombre si no está en el universo)
    target: str                 # cliente
    relation: Literal["supplier_of", "partner", "offtake", "lease", "competitor", "investor_in", "acquired"]
    detail: str | None = None
    verification: Verification
    source_url: str | None = None
    source_date: str | None = None


class Universe(BaseModel):
    version: str
    companies: dict[str, Company]
    relationships: list[Relationship] = Field(default_factory=list)

    def by_node(self, node_id: str) -> list[Company]:
        return [c for c in self.companies.values() if node_id in c.nodes]

    def seeds(self) -> list[Company]:
        return [c for c in self.companies.values() if c.seed]


def load_universe(path: Path | str = UNIVERSE_PATH, rel_path: Path | str = RELATIONSHIPS_PATH) -> Universe:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    companies: dict[str, Company] = {}
    discovered = Path(path).with_name("discovered.yaml")     # altas aprobadas del agente Discovery
    extra = (yaml.safe_load(discovered.read_text(encoding="utf-8")) or {}).get("companies", []) if discovered.exists() else []
    for row in raw["companies"] + extra:
        c = Company(**row)
        if c.id in companies:
            raise ValueError(f"empresa duplicada: {c.id}")
        companies[c.id] = c
    rels = []
    if Path(rel_path).exists():
        with open(rel_path, encoding="utf-8") as f:
            rraw = yaml.safe_load(f) or {}
        rels = [Relationship(**r) for r in rraw.get("relationships", [])]
    return Universe(version=str(raw["version"]), companies=companies, relationships=rels)


@lru_cache(maxsize=1)
def default_universe() -> Universe:
    return load_universe()


def validate_universe(u: Universe, m: IndustryMap) -> list[str]:
    errors = []
    for c in u.companies.values():
        for n in c.nodes:
            if n not in m.nodes:
                errors.append(f"{c.id}: nodo inexistente '{n}'")
        if not c.nodes:
            errors.append(f"{c.id}: sin nodos")
        if len(c.currency) != 3:
            errors.append(f"{c.id}: moneda inválida '{c.currency}'")
    symbols = Counter(c.yahoo_symbol.upper() for c in u.companies.values())     # 'besi.as' == 'BESI.AS'
    errors += [f"símbolo de datos duplicado: {s}" for s, k in symbols.items() if k > 1]
    for r in u.relationships:
        if r.verification in (Verification.CONFIRMED_FILING, Verification.COMPANY_STATEMENT,
                              Verification.MEDIA_REPORT) and not r.source_url:
            errors.append(f"relación {r.source}->{r.target} marcada {r.verification.value} sin source_url")
    return errors


def coverage_report(u: Universe, m: IndustryMap) -> dict:
    """Cobertura del universo sobre el mapa: qué nodos no tienen ninguna empresa listada."""
    covered = {n for c in u.companies.values() for n in c.nodes}
    uncovered = sorted(set(m.nodes) - covered - {n for n, d in m.nodes.items() if d.layer == "demand"})
    by_layer = Counter(m.nodes[c.primary_node].layer for c in u.companies.values())
    return {"n_companies": len(u.companies), "n_seeds": len(u.seeds()),
            "nodes_covered": len(covered & set(m.nodes)), "nodes_total": len(m.nodes),
            "uncovered_nodes": uncovered, "by_layer": dict(by_layer)}
