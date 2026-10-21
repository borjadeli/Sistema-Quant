"""Trazabilidad de datos.

Todo número relevante del sistema es un `DataPoint` con fuente, fecha, moneda, unidad y período,
y con una *clase epistemológica* explícita (`DataKind`):

- ORIGINAL: dato tal como lo publica la fuente (10-K, press release, precio de mercado).
- DERIVED: cálculo determinístico sobre datos originales (margen, FCF, EV/EBITDA). Guarda sus insumos.
- ESTIMATE: supuesto o proyección (escenarios, crecimiento esperado). Guarda quién lo estimó.
- INTERPRETATION: juicio de un agente LLM o del analista. Nunca se mezcla con los anteriores.

`known_at` es la fecha en que el dato estuvo disponible públicamente (p.ej. fecha de presentación
del 10-Q). Es la clave para evitar look-ahead bias: una decisión tomada en t solo puede usar
datapoints con known_at <= t.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class DataKind(str, Enum):
    ORIGINAL = "original"
    DERIVED = "derived"
    ESTIMATE = "estimate"
    INTERPRETATION = "interpretation"


class Verification(str, Enum):
    """Grado de verificación de un hecho cualitativo (relación comercial, exposición, etc.)."""
    CONFIRMED_FILING = "confirmed_by_filing"          # documento regulatorio
    COMPANY_STATEMENT = "company_statement"           # press release / earnings call (declaración corporativa)
    MEDIA_REPORT = "media_report"                     # prensa / research de terceros
    SEARCH_SUMMARY = "search_summary"                 # leído en resúmenes de buscador, no en el documento primario
    ANALYST_PRIOR = "analyst_prior"                   # conocimiento previo NO verificado en esta base
    HYPOTHESIS = "hypothesis"                         # inferencia, requiere confirmación
    SYNTHETIC = "synthetic_test_data"                 # dataset de prueba, jamás usar para decisiones


class Source(BaseModel):
    name: str
    url: str | None = None
    document_type: str | None = None   # 10-K, 10-Q, 8-K, press_release, api, transcript...
    published_at: date | None = None
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DataPoint(BaseModel):
    entity: str                        # company_id / ticker / bottleneck_id
    metric: str                        # revenue, net_income, cash, ...
    value: float | None
    unit: str = "USD"                  # USD, USD_millions, shares, pct, ratio, days...
    currency: str | None = "USD"
    period_end: date | None = None
    period_type: str | None = None     # FY, Q, TTM, instant, spot
    fiscal_label: str | None = None    # "FY2025", "Q2 2026"
    kind: DataKind = DataKind.ORIGINAL
    verification: Verification = Verification.CONFIRMED_FILING
    source: Source
    known_at: date | None = None       # fecha de disponibilidad pública (point-in-time)
    inputs: list[str] = Field(default_factory=list)   # ids de datapoints usados (DERIVED)
    author: str | None = None          # agente / persona (ESTIMATE / INTERPRETATION)
    note: str | None = None

    @field_validator("value")
    @classmethod
    def _finite(cls, v: float | None) -> float | None:
        if v is not None and (v != v or v in (float("inf"), float("-inf"))):
            raise ValueError("value must be finite or None")
        return v

    def key(self) -> str:
        return f"{self.entity}|{self.metric}|{self.period_type}|{self.period_end}|{self.kind.value}"


def stable_hash(obj: Any) -> str:
    """Hash determinístico de cualquier estructura JSON-serializable (data packs, prompts)."""
    payload = json.dumps(obj, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def point_in_time(points: list[DataPoint], as_of: date) -> list[DataPoint]:
    """Filtra datapoints disponibles a una fecha (evita look-ahead bias).

    Un dato sin `known_at` se excluye: si no sabemos cuándo se conoció, no podemos usarlo
    en una decisión histórica.
    """
    return [p for p in points if p.known_at is not None and p.known_at <= as_of]
