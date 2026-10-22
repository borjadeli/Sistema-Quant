"""Acceso a datos: sincronización YAML -> DB y lecturas/escrituras con trazabilidad."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from qa.db.schema import (Bottleneck, Company as CompanyRow, CompanyRelationship, DataPointRow, MapEdge,
                          MapNode, MarketSnapshot, Price)
from qa.map.model import IndustryMap
from qa.provenance import DataKind, DataPoint, Source, Verification
from qa.providers.base import MarketSnapshotData
from qa.universe.model import Universe


# --------------------------------------------------------------------------- sync curado -> DB
def sync_map(s: Session, m: IndustryMap) -> None:
    """Reemplaza el mapa en DB por la versión curada en YAML (el YAML es la fuente de verdad)."""
    s.execute(delete(MapEdge))
    s.execute(delete(MapNode))
    s.flush()
    for lid, lname in m.layers.items():
        s.add(MapNode(id=lid, name=lname, level="layer", map_version=m.version))
    for sid, (lid, sname) in m.segments.items():
        s.add(MapNode(id=sid, name=sname, level="segment", parent_id=lid, map_version=m.version))
    s.flush()
    for n in m.nodes.values():
        s.add(MapNode(id=n.id, name=n.name, level="node", node_type=n.type, parent_id=n.segment,
                      description=n.notes, map_version=m.version,
                      attributes={"concentration": n.concentration, "substitutability": n.substitutability,
                                  "layer": n.layer}))
    s.flush()
    for e in m.edges:
        s.add(MapEdge(source_id=e.source, target_id=e.target, relation=e.relation))


def sync_universe(s: Session, u: Universe, m: IndustryMap) -> None:
    existing = {c.id: c for c in s.scalars(select(CompanyRow))}
    for c in u.companies.values():
        row = existing.get(c.id) or CompanyRow(id=c.id, added_at=date.today(), added_by="universe_yaml")
        row.ticker, row.name, row.exchange, row.country, row.currency = c.ticker, c.name, c.exchange, c.country, c.currency
        row.yahoo_symbol = c.yahoo_symbol
        row.primary_layer = m.nodes[c.primary_node].layer
        row.functions = [{"node": n, "layer": m.nodes[n].layer} for n in c.nodes]
        row.exposure = c.exposure.model_dump()
        row.ibkr_accessible = c.ibkr_accessible == "yes"
        row.verification = c.verification.value
        row.status = c.status
        row.notes = "; ".join(x for x in [c.notes, c.verify and f"VERIFICAR: {c.verify}"] if x) or None
        row.tags = list(c.tags) + (["seed"] if c.seed else [])
        s.merge(row)
    # Empresas que salieron del YAML no se borran (evita survivorship bias): se marcan excluidas.
    for cid, row in existing.items():
        if cid not in u.companies and row.status == "active" and row.added_by == "universe_yaml":
            row.status = "excluded"
    s.execute(delete(CompanyRelationship))
    for r in u.relationships:
        s.add(CompanyRelationship(source_company=r.source, target_company=r.target, relation=r.relation,
                                  detail=r.detail, verification=r.verification.value, source_url=r.source_url,
                                  source_date=_d(r.source_date)))


def sync_bottlenecks(s: Session, items: list[dict]) -> None:
    for b in items:
        s.merge(Bottleneck(
            id=b["id"], name=b["name"], layer=b.get("layer"), map_nodes=b.get("map_nodes", []),
            status=b["status"], constrained_item=b.get("constrained_item"), demand_driver=b.get("demand_driver"),
            supply_limit=b.get("supply_limit"), estimated_duration=b.get("estimated_duration"),
            severity=b.get("severity"), confidence=b.get("confidence"), suppliers=b.get("known_suppliers", []),
            evidence=b.get("evidence", []), counter_evidence=b.get("counter_evidence", []),
            priced_in_assessment=b.get("priced_in_assessment"), updated_at=datetime.now(timezone.utc)))


def _d(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        try:
            return date.fromisoformat(s[:7] + "-01")
        except ValueError:
            return None


# --------------------------------------------------------------------------- datapoints
def upsert_datapoints(s: Session, points: list[DataPoint]) -> int:
    n = 0
    for p in points:
        q = select(DataPointRow).where(
            DataPointRow.entity == p.entity, DataPointRow.metric == p.metric,
            DataPointRow.period_type == p.period_type, DataPointRow.period_end == p.period_end,
            DataPointRow.kind == p.kind.value, DataPointRow.source_name == p.source.name,
            DataPointRow.known_at == p.known_at)          # cada vintage es una fila: nunca se pisa el pasado
        row = s.scalars(q).first() or DataPointRow()
        row.entity, row.metric, row.value, row.unit, row.currency = p.entity, p.metric, p.value, p.unit, p.currency
        row.period_end, row.period_type, row.fiscal_label = p.period_end, p.period_type, p.fiscal_label
        row.kind, row.verification = p.kind.value, p.verification.value
        row.source_name, row.source_url, row.document_type = p.source.name, p.source.url, p.source.document_type
        row.known_at, row.retrieved_at = p.known_at, p.source.retrieved_at
        row.inputs, row.author, row.note = p.inputs, p.author, p.note
        s.add(row)
        n += 1
    return n


def load_datapoints(s: Session, entity: str, kinds: tuple[str, ...] = ("original",),
                    as_of: date | None = None) -> list[DataPoint]:
    q = select(DataPointRow).where(DataPointRow.entity == entity, DataPointRow.kind.in_(kinds))
    if as_of is not None:
        q = q.where(DataPointRow.known_at <= as_of)
    out = []
    for r in s.scalars(q):
        out.append(DataPoint(
            entity=r.entity, metric=r.metric, value=r.value, unit=r.unit, currency=r.currency,
            period_end=r.period_end, period_type=r.period_type, fiscal_label=r.fiscal_label,
            kind=DataKind(r.kind), verification=Verification(r.verification),
            source=Source(name=r.source_name, url=r.source_url, document_type=r.document_type,
                          retrieved_at=r.retrieved_at or datetime.now(timezone.utc)),
            known_at=r.known_at, inputs=r.inputs or [], author=r.author, note=r.note))
    return out


# --------------------------------------------------------------------------- mercado
def save_snapshot(s: Session, snap: MarketSnapshotData) -> None:
    s.add(MarketSnapshot(company_id=snap.company_id, as_of=snap.as_of, price=snap.price, currency=snap.currency,
                         shares_outstanding=snap.shares_outstanding, market_cap=snap.market_cap,
                         market_cap_usd=snap.market_cap_usd, adv_usd_20d=snap.adv_usd_20d, source=snap.source))


def latest_snapshot(s: Session, company_id: str, as_of: datetime | None = None) -> MarketSnapshot | None:
    q = select(MarketSnapshot).where(MarketSnapshot.company_id == company_id)
    if as_of is not None:
        q = q.where(MarketSnapshot.as_of <= as_of)
    return s.scalars(q.order_by(MarketSnapshot.as_of.desc())).first()


def save_prices(s: Session, symbol: str, df: pd.DataFrame, source: str, currency: str | None = None) -> int:
    existing = set(s.scalars(select(Price.date).where(Price.symbol == symbol, Price.source == source)))
    n = 0
    for d, r in df.iterrows():
        if d in existing:
            continue
        s.add(Price(symbol=symbol, date=d, open=_nan(r.get("open")), high=_nan(r.get("high")),
                    low=_nan(r.get("low")), close=float(r["close"]), adj_close=_nan(r.get("adj_close")),
                    volume=_nan(r.get("volume")), currency=currency, source=source))
        n += 1
    return n


def price_series(s: Session, symbol: str, start: date | None = None, end: date | None = None) -> pd.Series:
    q = select(Price.date, Price.adj_close, Price.close).where(Price.symbol == symbol)
    if start:
        q = q.where(Price.date >= start)
    if end:
        q = q.where(Price.date <= end)
    rows = s.execute(q.order_by(Price.date)).all()
    return pd.Series({d: (a if a is not None else c) for d, a, c in rows}, name=symbol, dtype=float)


def _nan(x) -> float | None:  # noqa: ANN001
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if v != v else v
