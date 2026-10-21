"""Registro de cuellos de botella: evidencia + mapeo al mapa + severidad reproducible + radar."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
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
    out = {}
    for bid, nodes in reg["mapping"].items():
        raw = evidence.get(bid)
        if raw is None:
            continue
        rec = BottleneckRecord(id=bid, name=raw["name"], layer=raw.get("layer", ""), status=raw["status"],
                               confidence=str(raw.get("confidence", "low")), map_nodes=nodes, raw=raw)
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


def company_bottlenecks(company_nodes: list[str], registry: dict[str, BottleneckRecord]) -> list[dict]:
    """Bottlenecks que tocan los nodos de una empresa, con el formato que espera el scoring."""
    out = []
    for b in registry.values():
        if set(company_nodes) & set(b.map_nodes):
            out.append({"id": b.id, "severity": b.severity, "status": b.status,
                        "confidence": b.confidence.split()[0].lower(), "evidence": b.raw.get("evidence", [])})
    return out
