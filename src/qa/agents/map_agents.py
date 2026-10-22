"""Agentes que mantienen vivo el mapa: Map Curator (actualiza mapa/bottlenecks) y Discovery (busca empresas).

Ninguno modifica el mapa directamente: generan `MapChangeProposal` (pending). Al aprobarse se escriben a
overlays (`data/map/map_overlay.yaml`, `data/universe/discovered.yaml`) que los loaders combinan con la base
curada. Así el YAML humano queda limpio y todo cambio automático es auditable y reversible.
"""
from __future__ import annotations

import json
from datetime import date

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from qa.agents.base import RunContext, run_agent
from qa.bottlenecks import BottleneckRecord
from qa.config import DATA_DIR
from qa.db.schema import FeedEvent, MapChangeProposal
from qa.map.model import IndustryMap
from qa.universe.model import Universe, coverage_report

MAP_OVERLAY = DATA_DIR / "map" / "map_overlay.yaml"
DISCOVERED = DATA_DIR / "universe" / "discovered.yaml"


def map_summary(m: IndustryMap, reg: dict[str, BottleneckRecord], u: Universe) -> dict:
    cov = coverage_report(u, m)
    return {
        "map_version": m.version,
        "layers": {lid: [{"id": n.id, "name": n.name, "concentration": n.concentration}
                         for n in m.nodes.values() if n.layer == lid] for lid in m.layers},
        "bottlenecks": [{"id": b.id, "status": b.status, "severity": b.severity, "nodes": b.map_nodes,
                         "latest_evidence_dates": sorted({str(e.get("date")) for e in b.raw.get("evidence", [])})[-2:]}
                        for b in reg.values()],
        "uncovered_nodes": cov["uncovered_nodes"],
    }


def run_map_curator(ctx: RunContext, m: IndustryMap, reg: dict[str, BottleneckRecord], u: Universe) -> list[int]:
    pack = map_summary(m, reg, u)
    res = run_agent(ctx, "map_curator", "industry_map",
                    "Revisá el mapa y el registro de cuellos de botella con búsqueda web. Proponé cambios con evidencia.", pack)
    if not res.ok:
        return []
    ids = []
    for p in res.output.proposals:
        try:
            payload = json.loads(p.payload_json)
        except json.JSONDecodeError:
            payload = {"raw": p.payload_json}
        row = MapChangeProposal(agent_run_id=res.run_id, change_type=p.change_type, payload=payload,
                                evidence=[e.model_dump() for e in p.evidence], rationale=p.rationale)
        ctx.session.add(row)
        ctx.session.flush()
        ids.append(row.id)
        ctx.session.add(FeedEvent(event_date=date.today(), event_type="map_change", title=f"Propuesta: {p.change_type}",
                                  detail=p.rationale[:1000], verification="hypothesis", impact="unknown",
                                  url=(p.evidence[0].source_url if p.evidence else None)))
    return ids


def discovery_targets(m: IndustryMap, reg: dict[str, BottleneckRecord], u: Universe, k: int = 5) -> list[str]:
    """Nodos donde buscar: sin cobertura en el universo o con bottleneck severo y pocas empresas."""
    counts = {n: len(u.by_node(n)) for n in m.nodes}
    scored = []
    for b in reg.values():
        for n in b.map_nodes:
            scored.append((b.severity - 1.5 * min(counts.get(n, 0), 4), n))
    scored += [(6.0, n) for n in coverage_report(u, m)["uncovered_nodes"]]
    out = []
    for _, n in sorted(scored, reverse=True):
        if n not in out:
            out.append(n)
    return out[:k]


def run_discovery(ctx: RunContext, m: IndustryMap, reg: dict[str, BottleneckRecord], u: Universe,
                  nodes: list[str]) -> list[int]:
    ids = []
    for nid in nodes:
        node = m.nodes[nid]
        pack = {"target_node": node.model_dump(), "upstream": sorted(m.upstream(nid))[:20],
                "downstream": sorted(m.downstream(nid))[:20],
                "related_bottlenecks": [{"id": b.id, "status": b.status, "suppliers": b.raw.get("known_suppliers", [])}
                                        for b in reg.values() if nid in b.map_nodes],
                "companies_already_in_universe": [{"id": c.id, "name": c.name} for c in u.by_node(nid)],
                "all_universe_tickers": sorted(u.companies)}
        res = run_agent(ctx, "discovery", nid, f"Buscá empresas cotizantes que suministren el nodo '{node.name}' "
                                              "y no estén en el universo.", pack)
        if not res.ok:
            continue
        pending_tickers = {(x.payload or {}).get("ticker") for x in ctx.session.scalars(
            select(MapChangeProposal).where(MapChangeProposal.change_type == "add_company",
                                            MapChangeProposal.status.in_(("pending", "applied"))))}
        for cand in res.output.candidates:
            if cand.ticker and (cand.ticker in u.companies or cand.ticker in pending_tickers):
                continue
            bad_nodes = [n for n in cand.map_nodes if n not in m.nodes]
            if bad_nodes:
                cand = cand.model_copy(update={"map_nodes": [n for n in cand.map_nodes if n in m.nodes] or [nid]})
            pending_tickers.add(cand.ticker)
            row = MapChangeProposal(agent_run_id=res.run_id, change_type="add_company", payload=cand.model_dump(),
                                    evidence=[e.model_dump() for e in cand.evidence],
                                    rationale=f"{cand.role} — {cand.why_overlooked}")
            ctx.session.add(row)
            ctx.session.flush()
            ids.append(row.id)
    return ids


def review_proposal(s: Session, pid: int, approve: bool, reviewer: str, note: str = "") -> MapChangeProposal:
    p = s.get(MapChangeProposal, pid)
    if p is None or p.status != "pending":
        raise ValueError(f"propuesta {pid} inexistente o no pendiente")
    if not approve:
        p.status, p.reviewed_by, p.review_note = "rejected", reviewer, note
        return p
    apply_proposal(p)                     # si valida mal lanza ValueError y la propuesta queda pending
    p.status, p.reviewed_by, p.review_note = "applied", reviewer, note
    return p


def _validate_overlay(kind: str, entry: dict) -> None:
    """Simula el cambio sobre el mapa/universo actual y falla ANTES de escribir si rompe algo."""
    from qa.map.model import load_map, validate_map  # noqa: PLC0415
    from qa.universe.model import Company, load_universe, validate_universe  # noqa: PLC0415
    import networkx as nx  # noqa: PLC0415

    m, u = load_map(), load_universe()
    if kind == "company":
        if entry["ticker"] in u.companies:
            raise ValueError(f"{entry['ticker']} ya está en el universo")
        bad = [n for n in entry.get("nodes", []) if n not in m.nodes]
        if not entry.get("nodes") or bad:
            raise ValueError(f"nodos inexistentes en el mapa: {bad or 'ninguno indicado'}")
        Company(**entry)                                   # valida tipos/campos
    elif kind == "node":
        for k in ("id", "name", "type", "segment"):
            if not entry.get(k):
                raise ValueError(f"add_node requiere '{k}'")
        if entry["id"] in m.nodes:
            raise ValueError(f"nodo {entry['id']} ya existe")
        if entry["segment"] not in m.segments:
            raise ValueError(f"segmento inexistente: {entry['segment']}")
        bad = [n for n in entry.get("depends_on", []) if n not in m.nodes]
        if bad:
            raise ValueError(f"depends_on con nodos inexistentes: {bad}")
    elif kind == "edge":
        for k in ("source", "target"):
            if entry.get(k) not in m.nodes:
                raise ValueError(f"add_edge: {k} inexistente ({entry.get(k)})")
        g = m.graph()
        g.add_edge(entry["source"], entry["target"])
        if not nx.is_directed_acyclic_graph(g):
            raise ValueError("add_edge crearía un ciclo en el mapa")
    errs = validate_map(m) + validate_universe(u, m)
    if errs:
        raise ValueError(f"el mapa/universo actual ya tiene errores; corregir antes de aplicar: {errs[:3]}")


def apply_proposal(p: MapChangeProposal) -> None:
    """Escribe el cambio aprobado al overlay. Valida primero: si falla, no escribe nada (la propuesta sigue pending)."""
    if p.change_type == "add_company":
        c = p.payload
        if not c.get("ticker") or not c.get("exchange"):
            raise ValueError("add_company requiere ticker y bolsa verificados antes de aplicar")
        entry = {"ticker": c["ticker"], "name": c["name"], "exchange": c["exchange"],
                 "country": c.get("country") or "??", "currency": c.get("currency") or "USD",
                 "nodes": c.get("map_nodes") or [],
                 "exposure": {"ai": c.get("estimated_exposure") if c.get("estimated_exposure") in ("H", "M", "L") else "M",
                              "energy": "N", "commodities": "N"},
                 "status": "candidate", "verification": "search_summary", "notes": f"discovery proposal #{p.id}"}
        _validate_overlay("company", entry)
        data = yaml.safe_load(DISCOVERED.read_text(encoding="utf-8")) if DISCOVERED.exists() else {"companies": []}
        data["companies"].append(entry)
        DISCOVERED.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    elif p.change_type in ("add_node", "add_edge"):
        if not isinstance(p.payload, dict) or "raw" in p.payload:
            raise ValueError("payload no estructurado (JSON inválido del agente): editar antes de aprobar")
        _validate_overlay("node" if p.change_type == "add_node" else "edge", p.payload)
        data = yaml.safe_load(MAP_OVERLAY.read_text(encoding="utf-8")) if MAP_OVERLAY.exists() else {"nodes": [], "edges": []}
        data["nodes" if p.change_type == "add_node" else "edges"].append(p.payload | {"_proposal": p.id})
        MAP_OVERLAY.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    else:
        raise ValueError(f"{p.change_type}: aplicar manualmente en data/bottlenecks (requiere revisión de evidencia)")
    from qa.map.model import default_map  # noqa: PLC0415
    from qa.universe.model import default_universe  # noqa: PLC0415
    default_map.cache_clear()
    default_universe.cache_clear()


def pending(s: Session) -> list[MapChangeProposal]:
    return list(s.scalars(select(MapChangeProposal).where(MapChangeProposal.status == "pending")))
