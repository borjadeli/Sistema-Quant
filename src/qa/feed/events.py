"""Research Feed: acontecimientos que pueden cambiar tesis. Se generan automáticamente a partir de datos
nuevos (filings, movimientos de precio) y de los agentes; también se pueden cargar manualmente."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from qa.config import settings, utc_today
from qa.db.schema import DataPointRow, FeedEvent, MarketSnapshot, as_utc


def detect_new_filings(s: Session, since: date) -> int:
    rows = s.execute(select(DataPointRow.entity, DataPointRow.document_type, DataPointRow.known_at,
                            DataPointRow.source_url)
                     .where(DataPointRow.known_at >= since, DataPointRow.kind == "original",
                            DataPointRow.source_name == "sec_edgar").distinct()).all()
    n = 0
    for entity, doc, known, url in {(r[0], r[1], r[2], r[3]) for r in rows}:
        exists = s.scalars(select(FeedEvent).where(FeedEvent.company_id == entity, FeedEvent.event_type == "filing",
                                                   FeedEvent.event_date == known, FeedEvent.title.contains(doc or ""))).first()
        if exists:
            continue
        s.add(FeedEvent(event_date=known, company_id=entity, event_type="filing", title=f"Nuevo {doc}",
                        url=url, verification="confirmed_by_filing", impact="unknown", triggers_review=True))
        n += 1
    return n


def detect_restatements(s: Session, since: date) -> int:
    """Vintages nuevos (known_at >= since) que cambian de forma MATERIAL un valor ya publicado (cambio >= 2% o
    enmienda /A), en el mismo concepto o re-etiquetado al concepto preferido: es una red flag a revisar. Otra medida
    (concepto de menor prioridad) y los ajustes chicos no generan evento."""
    rows = s.scalars(select(DataPointRow).where(DataPointRow.known_at >= since, DataPointRow.kind == "original",
                                                DataPointRow.note.contains("restates="),
                                                DataPointRow.note.contains(";material"))).all()
    n = 0
    for r in rows:
        kind = "Re-etiquetado XBRL con otro valor (posible re-expresión)" if "retag=" in r.note else "Re-expresión"
        title = f"{kind}: {r.metric} {r.period_type} {r.period_end}"
        exists = s.scalars(select(FeedEvent).where(FeedEvent.company_id == r.entity,
                                                   FeedEvent.event_type == "restatement",
                                                   FeedEvent.title == title, FeedEvent.event_date == r.known_at)).first()
        if exists:
            continue
        prev = r.note.split("restates=")[1].split(";")[0]
        s.add(FeedEvent(event_date=r.known_at, company_id=r.entity, event_type="restatement", title=title,
                        detail=f"nuevo valor {r.value} (antes {prev}); {r.document_type}", url=r.source_url,
                        verification="confirmed_by_filing", impact="unknown", triggers_review=True))
        n += 1
    return n


def end_of_day_utc(d: date) -> datetime:
    return datetime.combine(d, datetime.max.time(), tzinfo=timezone.utc)


def detect_price_moves(s: Session, threshold_pct: float | None = None, lookback_days: int = 30) -> int:
    """Un evento por EPISODIO de movimiento: si ya hay un price_move de la empresa dentro de la ventana, solo se
    registra otro cuando el precio se movió de nuevo >= umbral desde el precio de ese último evento. Así una
    suba sostenida no genera un evento por día (ni cambia el hash de reutilización a diario)."""
    if threshold_pct is None:
        threshold_pct = settings()["funnel"]["reuse"]["price_move_trigger_pct"]
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=lookback_days)
    n = 0
    for cid in s.scalars(select(MarketSnapshot.company_id).distinct()):
        snaps = s.scalars(select(MarketSnapshot).where(MarketSnapshot.company_id == cid)
                          .order_by(MarketSnapshot.as_of.desc())).all()
        if len(snaps) < 2 or not snaps[0].price:
            continue
        old = next((x for x in snaps[1:] if as_utc(x.as_of) <= cutoff), snaps[-1])
        if not old.price:
            continue
        move = snaps[0].price / old.price - 1
        if abs(move) * 100 < threshold_pct:
            continue
        last = s.scalars(select(FeedEvent).where(FeedEvent.company_id == cid, FeedEvent.event_type == "price_move",
                                                 FeedEvent.event_date >= cutoff.date())
                         .order_by(FeedEvent.event_date.desc(), FeedEvent.id.desc())).first()
        if last is not None:
            seen = as_utc(last.created_at) if last.created_at else end_of_day_utc(last.event_date)
            ref = next((x for x in snaps if as_utc(x.as_of) <= seen and x.price), None)   # precio al último evento
            if ref is None or abs(snaps[0].price / ref.price - 1) * 100 < threshold_pct:
                continue          # mismo episodio: sin nuevo tramo >= umbral desde el último evento
        s.add(FeedEvent(event_date=as_utc(snaps[0].as_of).date(), company_id=cid, event_type="price_move",
                        title=f"Movimiento de precio {move:+.0%} desde {as_utc(old.as_of).date()}",
                        verification="market_data",
                        detail=f"{old.price:.2f} -> {snaps[0].price:.2f} ({snaps[0].source})",
                        impact="unknown", triggers_review=True))
        s.flush()
        n += 1
    return n


def add_manual(s: Session, title: str, company_id: str | None = None, url: str | None = None,
               event_type: str = "news", verification: str = "media_report", detail: str | None = None,
               bottleneck_id: str | None = None, event_date: date | None = None, impact: str = "unknown") -> FeedEvent:
    e = FeedEvent(event_date=event_date or utc_today(), company_id=company_id, bottleneck_id=bottleneck_id,
                  event_type=event_type, title=title, detail=detail, url=url, verification=verification,
                  impact=impact, triggers_review=True)
    s.add(e)
    return e
