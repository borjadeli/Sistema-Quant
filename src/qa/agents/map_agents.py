"""Agentes que mantienen vivo el mapa: Map Curator (actualiza mapa/bottlenecks) y Discovery (busca empresas).

Ninguno modifica el mapa directamente: generan `MapChangeProposal` (pending). Al aprobarse se escriben a
overlays (`data/map/map_overlay.yaml`, `data/universe/discovered.yaml`) que los loaders combinan con la base
curada. Así el YAML humano queda limpio y todo cambio automático es auditable y reversible.
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from qa.agents.base import RunContext, run_agent
from qa.bottlenecks import BottleneckRecord
from qa.config import DATA_DIR, MAP_PATH, RELATIONSHIPS_PATH, UNIVERSE_PATH
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
    if not res.ok or res.status == "reused":      # una salida reutilizada no son novedades: no se re-proponen
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
        ctx.session.add(FeedEvent(event_date=ctx.decision_date, event_type="map_change", title=f"Propuesta: {p.change_type}",
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
        if not res.ok or res.status == "reused":
            continue
        pending_tickers = {(x.payload or {}).get("ticker") for x in ctx.session.scalars(
            select(MapChangeProposal).where(MapChangeProposal.change_type == "add_company",
                                            MapChangeProposal.status.in_(("pending", "applied"))))}
        known = {t.upper() for t in u.companies} | {str(t).upper() for t in pending_tickers if t}
        for cand in res.output.candidates:
            if cand.ticker and cand.ticker.upper() in known:
                continue
            payload = cand.model_dump()
            rationale = f"{cand.role} — {cand.why_overlooked}"
            bad_nodes = [n for n in cand.map_nodes if n not in m.nodes]
            if bad_nodes:
                # no se reasigna en silencio: se conserva lo que dijo el agente y un humano elige el nodo
                payload["original_map_nodes"] = list(cand.map_nodes)
                payload["map_nodes"] = [n for n in cand.map_nodes if n in m.nodes]
                rationale += (f" [ATENCIÓN: nodos inexistentes {bad_nodes} descartados (buscado en '{nid}'); "
                              "asignar nodo antes de aprobar]" if not payload["map_nodes"] else
                              f" [nodos inexistentes descartados: {bad_nodes}]")
            if cand.ticker:
                known.add(cand.ticker.upper())
            row = MapChangeProposal(agent_run_id=res.run_id, change_type="add_company", payload=payload,
                                    evidence=[e.model_dump() for e in cand.evidence], rationale=rationale)
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


NODE_KEYS = {"id", "name", "type", "segment", "concentration", "substitutability", "depends_on", "notes"}
EDGE_KEYS = {"source", "target", "relation"}
RESERVED_KEYS = {"layer"}      # 'layer' lo deriva el segmento; '_*' son metadatos internos


def _prepare(kind: str, payload: dict) -> dict:
    """Normaliza el payload del agente: rechaza claves reservadas; en un nodo, las claves descriptivas que el
    modelo no tiene (p.ej. 'description') pasan a `notes`; en una arista, se descartan. `feeds` (solo nodos) son
    aristas salientes: permiten agregar un nodo RAÍZ (sin dependencias) sin quedar aislado."""
    bad = sorted(k for k in payload if k in RESERVED_KEYS or str(k).startswith("_"))
    if bad:
        raise ValueError(f"add_{kind}: claves reservadas {bad} (la capa sale del segmento)")
    allowed = (NODE_KEYS | {"feeds"}) if kind == "node" else EDGE_KEYS
    entry = {k: v for k, v in payload.items() if k in allowed}
    extra = {k: v for k, v in payload.items() if k not in allowed}
    if extra and kind == "node":
        txt = "; ".join(f"{k}: {v}" for k, v in extra.items())
        entry["notes"] = f"{entry['notes']}; {txt}" if isinstance(entry.get("notes"), str) and entry["notes"] else txt
    return entry


def _overlay_items(kind: str, entry: dict) -> list[tuple[str, dict]]:
    """(sección del overlay, ítem) que escribe un cambio: un nodo con `feeds` escribe también sus aristas."""
    if kind == "company":
        return [("companies", entry)]
    if kind == "edge":
        return [("edges", entry)]
    node = {k: v for k, v in entry.items() if k != "feeds"}
    return [("nodes", node)] + [("edges", {"source": entry["id"], "target": t}) for t in entry.get("feeds") or []]


def _simulate(kind: str, entry: dict):  # noqa: ANN202
    """Carga mapa y universo CON el cambio, en una copia temporal y con los loaders reales.
    Devuelve (mapa, universo) resultantes; cualquier error de carga se reporta como ValueError."""
    from qa.map.model import load_map  # noqa: PLC0415
    from qa.universe.model import load_universe  # noqa: PLC0415

    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        shutil.copy(MAP_PATH, t / "industry_map.yaml")
        shutil.copy(UNIVERSE_PATH, t / "companies.yaml")
        for src, name in ((MAP_OVERLAY, "map_overlay.yaml"), (DISCOVERED, "discovered.yaml")):
            if src.exists():
                shutil.copy(src, t / name)
        target = t / ("discovered.yaml" if kind == "company" else "map_overlay.yaml")
        data = (yaml.safe_load(target.read_text(encoding="utf-8")) if target.exists() else None) or {}
        for key, item in _overlay_items(kind, entry):
            data.setdefault(key, []).append(item)
        target.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        try:
            return load_map(t / "industry_map.yaml"), load_universe(t / "companies.yaml", RELATIONSHIPS_PATH)
        except Exception as e:        # noqa: BLE001 — TypeError/ValidationError/KeyError del loader
            raise ValueError(f"el cambio rompe la carga del mapa/universo: {type(e).__name__}: {e}") from e


def _validate_overlay(kind: str, entry: dict) -> None:
    """Simula el cambio y falla ANTES de escribir si el RESULTADO rompe algo. Los errores que el estado
    actual ya tenía no bloquean (si no, un cambio malo impediría aplicar la corrección)."""
    from qa.map.model import load_map, validate_map  # noqa: PLC0415
    from qa.universe.model import load_universe, validate_universe  # noqa: PLC0415
    import networkx as nx  # noqa: PLC0415

    m, u = load_map(), load_universe()
    if kind == "company":
        t = str(entry["ticker"]).upper()
        if t in {k.upper() for k in u.companies}:
            raise ValueError(f"{entry['ticker']} ya está en el universo")
        bad = [n for n in entry.get("nodes", []) if n not in m.nodes]
        if not entry.get("nodes") or bad:
            raise ValueError(f"nodos inexistentes en el mapa: {bad or 'ninguno indicado'}")
    elif kind == "node":
        for k in ("id", "name", "type", "segment"):
            if not entry.get(k):
                raise ValueError(f"add_node requiere '{k}'")
        if entry["id"] in m.nodes:
            raise ValueError(f"nodo {entry['id']} ya existe")
        if entry["segment"] not in m.segments:
            raise ValueError(f"segmento inexistente: {entry['segment']}")
        bad = [n for n in list(entry.get("depends_on") or []) + list(entry.get("feeds") or []) if n not in m.nodes]
        if bad:
            raise ValueError(f"depends_on/feeds con nodos inexistentes: {bad}")
    elif kind == "edge":
        for k in ("source", "target"):
            if entry.get(k) not in m.nodes:
                raise ValueError(f"add_edge: {k} inexistente ({entry.get(k)})")
        if any(e.source == entry["source"] and e.target == entry["target"] for e in m.edges):
            raise ValueError("add_edge: la arista ya existe")
    m2, u2 = _simulate(kind, entry)
    if kind == "node" and entry["id"] not in m2.nodes:
        raise ValueError(f"add_node: el nodo {entry['id']} no quedó incorporado")
    if kind in ("node", "edge") and not nx.is_directed_acyclic_graph(m2.graph()):
        raise ValueError(f"add_{kind} crearía un ciclo en el mapa")
    if kind == "edge":
        if not any(e.source == entry["source"] and e.target == entry["target"] for e in m2.edges):
            raise ValueError("add_edge: la arista no quedó incorporada")
        if not nx.is_directed_acyclic_graph(m2.graph()):
            raise ValueError("add_edge crearía un ciclo en el mapa")
    if kind == "company" and str(entry["ticker"]) not in u2.companies:
        raise ValueError(f"add_company: {entry['ticker']} no quedó incorporada")
    before = set(validate_map(m) + validate_universe(u, m))
    new = [e for e in validate_map(m2) + validate_universe(u2, m2) if e not in before]
    if new:
        raise ValueError(f"el cambio introduce errores de validación: {new[:5]}")


# bolsas fuera de EE.UU. con líneas habituales en USD (GDRs en LSE, líneas .U en TSX, SIX/HKEX/SGX)
USD_LINES_OK = {"LSE", "TSX", "TSXV", "SIX", "HKEX", "SGX"}
CURRENCY_ALIASES = {"GBX": "GBP", "GBP": "GBP"}        # precios en peniques: el proveedor los escala (GBp)


def _company_currency(c: dict) -> str:
    """Moneda de cotización: la de la bolsa por defecto; la del agente se acepta si coincide con la bolsa o si es USD
    en una bolsa con líneas en dólares. En EE.UU. siempre USD. Nunca USD por defecto fuera de EE.UU."""
    from qa.universe.model import EXCHANGE_CURRENCY, US_EXCHANGES  # noqa: PLC0415
    exch = str(c.get("exchange") or "").upper()
    by_exch = EXCHANGE_CURRENCY.get(exch)
    raw = (c.get("currency") or "").strip()
    given = CURRENCY_ALIASES.get(raw.upper(), raw.upper()) or None
    if given and by_exch and given != by_exch and not (given == "USD" and exch in USD_LINES_OK and exch not in US_EXCHANGES):
        raise ValueError(f"moneda {given} contradice la bolsa {exch} ({by_exch}): verificar y editar antes de aprobar")
    ccy = given or by_exch
    if not ccy:
        raise ValueError(f"moneda desconocida para la bolsa {exch!r}: indicar 'currency' antes de aprobar")
    return ccy


def apply_proposal(p: MapChangeProposal) -> None:
    """Escribe el cambio aprobado al overlay. Valida primero: si falla, no escribe nada (la propuesta sigue pending)."""
    if p.change_type == "add_company":
        c = p.payload
        if not c.get("ticker") or not c.get("exchange"):
            raise ValueError("add_company requiere ticker y bolsa verificados antes de aplicar")
        entry = {"ticker": c["ticker"], "name": c["name"], "exchange": c["exchange"],
                 "country": c.get("country") or "??", "currency": _company_currency(c),
                 "nodes": c.get("map_nodes") or [],
                 "exposure": {"ai": c.get("estimated_exposure") if c.get("estimated_exposure") in ("H", "M", "L") else "M",
                              "energy": "N", "commodities": "N"},
                 "status": "candidate", "verification": "search_summary", "notes": f"discovery proposal #{p.id}"}
        _validate_overlay("company", entry)
        data = (yaml.safe_load(DISCOVERED.read_text(encoding="utf-8")) if DISCOVERED.exists() else None) or {}
        data.setdefault("companies", []).append(entry)
        DISCOVERED.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    elif p.change_type in ("add_node", "add_edge"):
        if not isinstance(p.payload, dict) or "raw" in p.payload:
            raise ValueError("payload no estructurado (JSON inválido del agente): editar antes de aprobar")
        kind = "node" if p.change_type == "add_node" else "edge"
        entry = _prepare(kind, p.payload)
        _validate_overlay(kind, entry)
        data = (yaml.safe_load(MAP_OVERLAY.read_text(encoding="utf-8")) if MAP_OVERLAY.exists() else None) or {}
        data.setdefault("nodes", [])
        data.setdefault("edges", [])
        for key, item in _overlay_items(kind, entry):
            data[key].append(item | {"_proposal": p.id})
        MAP_OVERLAY.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    else:
        raise ValueError(f"{p.change_type}: aplicar manualmente en data/bottlenecks (requiere revisión de evidencia)")
    from qa.map.model import default_map  # noqa: PLC0415
    from qa.universe.model import default_universe  # noqa: PLC0415
    default_map.cache_clear()
    default_universe.cache_clear()


def pending(s: Session) -> list[MapChangeProposal]:
    return list(s.scalars(select(MapChangeProposal).where(MapChangeProposal.status == "pending")))
