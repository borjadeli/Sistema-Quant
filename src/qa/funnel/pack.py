"""Construcción de data packs: lo ÚNICO que ven los agentes. Todo con fuente, fecha y calidad de datos."""
from __future__ import annotations

import json
import math
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from qa.bottlenecks import BottleneckRecord, company_bottlenecks
from qa.config import RESEARCH_DIR
from qa.db import repo
from qa.db.schema import FeedEvent, as_utc
from qa.fundamentals.metrics import Fundamentals, compute_fundamentals
from qa.map.model import IndustryMap
from qa.universe.model import Company
from qa.valuation.core import MarketInputs, compute_multiples, reverse_dcf

_SEED_CACHE: dict | None = None


def seed_research(cid: str) -> dict | None:
    global _SEED_CACHE
    if _SEED_CACHE is None:
        p = RESEARCH_DIR / "seed_companies_verified.json"
        _SEED_CACHE = {r["ticker"]: r for r in json.loads(p.read_text(encoding="utf-8"))} if p.exists() else {}
    r = _SEED_CACHE.get(cid)
    # Los "skeletons" (data_quality none) contienen solo conocimiento previo sin fuente: no se pasan a los agentes.
    if not r or not r.get("business_summary") or str(r.get("data_quality", "")).startswith("none"):
        return None
    keep = ["business_summary", "main_products", "segments", "ai_energy_commodity_link", "confirmed_relationships",
            "key_recent_events", "red_flags", "notes_unverified", "verification_method"]
    return {k: r.get(k) for k in keep} | {"_verification": "search_summary — leído en resúmenes de buscador"}


def market_inputs(s: Session, c: Company, f: Fundamentals | None, as_of: datetime | None = None) -> MarketInputs | None:
    snap = repo.latest_snapshot(s, c.id, as_of)
    if snap is None or snap.price is None:
        return None
    shares = (f.v("diluted_shares") if f else None) or snap.shares_outstanding
    if not shares:
        return None
    fx = 1.0
    if f is not None and f.currency and snap.currency and f.currency != snap.currency:
        return None     # moneda de reporte != cotización: requiere FX explícito (no se asume)
    return MarketInputs(price=snap.price, shares=shares, price_currency=snap.currency or c.currency,
                        price_time=as_utc(snap.as_of).isoformat(), price_source=snap.source, fx_report_to_price=fx)


def _clean(x):  # noqa: ANN001, ANN202
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
        return None
    return x


def company_pack(s: Session, c: Company, m: IndustryMap, registry: dict[str, BottleneckRecord],
                 as_of: date, compact: bool = False) -> dict:
    pts = repo.load_datapoints(s, c.id, as_of=as_of)
    f = compute_fundamentals(c.id, pts, as_of) if pts else None
    mk = market_inputs(s, c, f)
    snap = repo.latest_snapshot(s, c.id)
    crit = m.structural_criticality()
    pack: dict = {
        "company": {"id": c.id, "name": c.name, "exchange": c.exchange, "country": c.country, "currency": c.currency,
                    "status": c.status, "tags": c.tags, "notes": c.notes, "identity_verification": c.verification.value},
        "map_position": {
            "nodes": [{"id": n, "name": m.nodes[n].name, "layer": m.nodes[n].layer,
                       "concentration": m.nodes[n].concentration, "substitutability": m.nodes[n].substitutability,
                       "structural_criticality": crit[n]} for n in c.nodes],
            "path_to_final_demand": m.path_to_demand(c.primary_node),
            "exposure_prior": c.exposure.model_dump() | {"_note": "prior del analista, no dato"}},
        "bottlenecks": [
            {"id": b["id"], "severity": b["severity"], "status": b["status"], "confidence": b["confidence"],
             "name": registry[b["id"]].name,
             **({} if compact else {
                 "supply_limit": registry[b["id"]].raw.get("supply_limit"),
                 "estimated_duration": registry[b["id"]].raw.get("estimated_duration"),
                 "evidence": registry[b["id"]].raw.get("evidence", [])[:3],
                 "counter_evidence": registry[b["id"]].raw.get("counter_evidence", [])[:3]})}
            for b in company_bottlenecks(c.nodes, registry)],
        "market": None if snap is None else {
            "price": snap.price, "currency": snap.currency, "as_of": as_utc(snap.as_of).isoformat(), "source": snap.source,
            "market_cap_usd": snap.market_cap_usd, "adv_usd_20d": snap.adv_usd_20d},
        "fundamentals": None, "valuation": None,
    }
    if f is not None:
        keys = ["revenue_ttm", "revenue_last_q", "revenue_growth_yoy", "revenue_growth_q_yoy", "gross_margin",
                "ebit_margin", "ebitda_margin", "net_margin", "fcf_ttm", "fcf_margin", "capex_intensity", "roic",
                "cash_total", "debt", "net_debt", "net_debt_to_ebitda", "interest_coverage", "cash_runway_quarters",
                "dilution_yoy", "sbc_pct_revenue", "incremental_margin", "rpo", "diluted_shares", "net_income_last_q"]
        pack["fundamentals"] = {
            "currency": f.currency, "data_quality": f.data_quality, "as_of": str(f.as_of), "missing": f.missing,
            "metrics": {k: {"value": _clean(f.metrics[k].value), "period": f.metrics[k].period}
                        for k in keys if k in f.metrics and f.metrics[k].value is not None}}
        if not compact:
            rev_q = f.quarterly.get("revenue", {})
            pack["fundamentals"]["revenue_quarterly_last8"] = {str(d): v for d, v in list(rev_q.items())[-8:]}
            oi_q = f.quarterly.get("operating_income", {})
            pack["fundamentals"]["operating_income_quarterly_last8"] = {str(d): v for d, v in list(oi_q.items())[-8:]}
    if f is not None and mk is not None:
        mult = compute_multiples(f, mk)
        rd = reverse_dcf(f, mk)
        pack["valuation"] = {"price": mk.price, "price_time": mk.price_time, "price_source": mk.price_source,
                             "market_cap": mult.market_cap, "ev": mult.ev,
                             "multiples": {k: _clean(v) for k, v in mult.values.items()},
                             "not_meaningful": mult.not_meaningful,
                             "reverse_dcf": rd.model_dump() if rd else None}
    if not compact:
        sr = seed_research(c.id)
        if sr:
            pack["web_research"] = sr
        ev = s.scalars(select(FeedEvent).where(FeedEvent.company_id == c.id)
                       .order_by(FeedEvent.created_at.desc()).limit(10)).all()
        pack["recent_events"] = [{"date": str(e.event_date), "type": e.event_type, "title": e.title, "url": e.url,
                                  "verification": e.verification} for e in ev]
    pack["_meta"] = {"decision_date": str(as_of)}
    return pack


def hash_view(pack: dict, move_pct: float = 15.0) -> dict:
    """Vista estable del pack para decidir reutilización: el precio entra en 'buckets' logarítmicos de
    `move_pct`%, así un análisis se reutiliza salvo cambio material (nuevo filing, evento o movimiento > move_pct)."""
    v = json.loads(json.dumps(pack, default=str))
    price = (v.get("market") or {}).get("price")
    bucket = round(math.log(price) / math.log(1 + move_pct / 100)) if price and price > 0 else None
    for k in ("market", "valuation"):
        v.pop(k, None)
    v["price_bucket"] = bucket
    return v
