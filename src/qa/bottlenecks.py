"""Registro de cuellos de botella: evidencia + mapeo al mapa + severidad reproducible + radar."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from functools import lru_cache

import yaml

from qa.config import BOTTLENECKS_PATH, DATA_DIR
from qa.map.model import IndustryMap
from qa.universe.model import Universe

STATUS_BASE = {"current_shortage": 6.0, "projected_shortage": 4.5, "speculative_hypothesis": 3.0, "easing": 2.5}  # máx. por regla = 9; 10 solo por override justificado
CONF_ADJ = {"high": 1.5, "medium": 0.5, "low": -0.5}
CONC_ADJ = {"monopoly": 1.0, "very_high": 1.0, "high": 0.5}
PRIMARY_TYPES = {"company_statement", "government_data"}


@dataclass
class BottleneckRecord:
    id: str
    name: str
    layer: str
    status: str
    confidence: str
    map_nodes: list[str]
    raw: dict
    severity: float = 0.0
    severity_explain: str = ""
    companies: list[str] = field(default_factory=list)
    reviewed_at: date | None = None     # fecha de compilación del registro (todo se conocía a esta fecha)

    def as_dict(self) -> dict:
        d = dict(self.raw)
        d.update({"map_nodes": self.map_nodes, "severity": self.severity, "severity_explain": self.severity_explain,
                  "companies_in_universe": self.companies})
        return d


def severity(b: dict, m: IndustryMap, nodes: list[str]) -> tuple[float, str]:
    """Severidad 0-10 (regla documentada en docs/05_scoring_rubricas.md)."""
    s = STATUS_BASE.get(b["status"], 3.0)
    parts = [f"estado {b['status']}={s}"]
    c = CONF_ADJ.get(str(b.get("confidence", "")).split()[0].lower(), 0.0)
    s += c
    parts.append(f"confianza {c:+}")
    conc = max((CONC_ADJ.get(m.nodes[n].concentration, 0.0) for n in nodes if n in m.nodes), default=0.0)
    s += conc
    parts.append(f"concentración {conc:+}")
    ev, ce = b.get("evidence", []), b.get("counter_evidence", [])
    prim = sum(1 for e in ev if e.get("evidence_type") in PRIMARY_TYPES)
    if prim >= 2:
        s += 0.5
        parts.append("fuentes primarias>=2 +0.5")
    if len(ce) >= len(ev):
        s -= 0.5
        parts.append("contra-evidencia>=evidencia -0.5")
    return round(max(0.0, min(10.0, s)), 2), "; ".join(parts)


def load_bottlenecks(m: IndustryMap, u: Universe | None = None) -> dict[str, BottleneckRecord]:
    reg = yaml.safe_load(BOTTLENECKS_PATH.read_text(encoding="utf-8"))
    ev_path = DATA_DIR / reg["evidence_file"]
    evidence = {b["id"]: b for b in json.loads(ev_path.read_text(encoding="utf-8"))} if ev_path.exists() else {}
    try:
        reviewed = date.fromisoformat(str(reg.get("evidence_reviewed_at"))[:10])
    except ValueError:
        reviewed = None
    out = {}
    for bid, nodes in reg["mapping"].items():
        raw = evidence.get(bid)
        if raw is None:
            continue
        rec = BottleneckRecord(id=bid, name=raw["name"], layer=raw.get("layer", ""), status=raw["status"],
                               confidence=str(raw.get("confidence", "low")), map_nodes=nodes, raw=raw,
                               reviewed_at=reviewed)
        rec.severity, rec.severity_explain = severity(raw, m, nodes)
        ov = (reg.get("overrides") or {}).get(bid)
        if ov:
            rec.severity = float(ov["severity"])
            rec.severity_explain += f"; OVERRIDE {ov['severity']} ({ov['reason']})"
        if u is not None:
            rec.companies = sorted(c.id for c in u.companies.values() if set(c.nodes) & set(nodes))
        out[bid] = rec
    return out


@lru_cache(maxsize=1)
def default_bottlenecks():
    from qa.map.model import default_map
    from qa.universe.model import default_universe
    return load_bottlenecks(default_map(), default_universe())


# Fecha al INICIO del campo: "2026-10-16", "2026-08 (approx.)", "2026", y rangos "2026-10-16/19", "2026-08/09",
# "2025-2026". Las menciones posteriores en el texto (p.ej. "Q1 FY2026") no se interpretan.
_LEAD = re.compile(r"^\s*(\d{4})(?:-(\d{2})(?!\d))?(?:-(\d{2})(?!\d))?(?:\s*/\s*(\d{2})(?!\d)|-(\d{4})(?!\d))?")


def _parse_evidence_date(e: dict) -> tuple[date | None, bool]:
    """(fecha, es_exacta). Fechas parciales o rangos se llevan al FINAL del período (conservador para
    point-in-time); un día exacto (o un rango de días) es exacto."""
    m = _LEAD.match(str(e.get("date") or ""))
    if not m:
        return None, False
    y, mth, d, rng, y2 = m.groups()
    try:
        if y2:
            return date(int(y2), 12, 31), False
        if d:
            return date(int(y), int(mth), int(rng or d)), True
        if mth:
            mm = int(rng or mth)
            if not 1 <= mm <= 12:
                return None, False
            return date(int(y) + (mm == 12), mm % 12 + 1, 1) - timedelta(days=1), False
        return date(int(y), 12, 31), False
    except ValueError:
        return None, False


def evidence_date(e: dict) -> date | None:
    """Fecha de publicación de una evidencia (fin del período si es parcial). None si no es interpretable."""
    return _parse_evidence_date(e)[0]


def known_date(e: dict, reviewed_at: date | None) -> date | None:
    """Desde cuándo el sistema puede usar una evidencia. Una fecha EXACTA es su fecha (aunque sea posterior a la
    revisión: entonces la revisión está desactualizada, ver stale_evidence). Una fecha parcial o ausente se acota a
    la revisión del registro (lo que estaba en el registro se conocía al compilarlo)."""
    d, exact = _parse_evidence_date(e)
    if exact or reviewed_at is None:
        return d
    return reviewed_at if d is None else min(d, reviewed_at)


def stale_evidence(registry: dict[str, BottleneckRecord]) -> list[str]:
    """Evidencias con fecha exacta posterior a `evidence_reviewed_at`: la fecha de revisión quedó vieja."""
    out = []
    for b in registry.values():
        for e in b.raw.get("evidence", []) + b.raw.get("counter_evidence", []):
            d, exact = _parse_evidence_date(e)
            if exact and b.reviewed_at is not None and d > b.reviewed_at:
                out.append(f"bottleneck {b.id}: evidencia del {d} posterior a evidence_reviewed_at {b.reviewed_at} "
                           "(actualizar la fecha de revisión en data/bottlenecks/bottlenecks.yaml)")
    return out


def registry_as_of(registry: dict[str, BottleneckRecord], m: IndustryMap, as_of: date) -> dict[str, BottleneckRecord]:
    """Vista point-in-time del registro (replays/backtests). Determinista: depende solo de as_of y de las fechas.

    - Evidencia y contra-evidencia: solo las conocidas a as_of (ver known_date); sin fecha ni revisión, se excluyen.
    - Si se excluyó algo, la severidad se recalcula con la evidencia visible y los textos sin fecha del registro
      actual (límite de oferta, duración estimada) no se pasan; el estado se marca como 'registro actual'.
    - Un bottleneck sin evidencia visible a esa fecha no existía para el sistema: se omite.
    Con as_of posterior a la revisión del registro devuelve el registro sin cambios.
    """
    out = {}
    for bid, b in registry.items():
        def seen(e: dict) -> bool:
            k = known_date(e, b.reviewed_at)
            return k is not None and k <= as_of
        ev = [e for e in b.raw.get("evidence", []) if seen(e)]
        ce = [e for e in b.raw.get("counter_evidence", []) if seen(e)]
        if len(ev) == len(b.raw.get("evidence", [])) and len(ce) == len(b.raw.get("counter_evidence", [])):
            out[bid] = b
            continue
        if not ev:
            continue
        raw = {k: v for k, v in b.raw.items() if k not in ("supply_limit", "estimated_duration")}
        raw.update(evidence=ev, counter_evidence=ce,
                   pit_note="vista point-in-time: evidencia posterior a la fecha de decisión excluida; estado y "
                            "confianza son del registro actual (no reconstruibles a esa fecha)")
        rec = replace(b, raw=raw)
        rec.severity, rec.severity_explain = severity(raw, m, b.map_nodes)
        rec.severity_explain += "; recalculada con la evidencia visible a la fecha de decisión"
        out[bid] = rec
    return out


def company_bottlenecks(company_nodes: list[str], registry: dict[str, BottleneckRecord]) -> list[dict]:
    """Bottlenecks que tocan los nodos de una empresa, con el formato que espera el scoring."""
    out = []
    for b in registry.values():
        if set(company_nodes) & set(b.map_nodes):
            out.append({"id": b.id, "severity": b.severity, "status": b.status,
                        "confidence": b.confidence.split()[0].lower(), "evidence": b.raw.get("evidence", [])})
    return out
