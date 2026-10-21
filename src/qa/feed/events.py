"""Research Feed: acontecimientos que pueden cambiar tesis. Se generan automáticamente a partir de datos
nuevos (filings, movimientos de precio) y de los agentes; también se pueden cargar manualmente."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from qa.db.schema import DataPointRow, FeedEvent, MarketSnapshot


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


def detect_price_moves(s: Session, threshold_pct: float = 15.0, lookback_days: int = 30) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    n = 0
    for cid in s.scalars(select(MarketSnapshot.company_id).distinct()):
        snaps = s.scalars(select(MarketSnapshot).where(MarketSnapshot.company_id == cid)
                          .order_by(MarketSnapshot.as_of.desc())).all()
        if len(snaps) < 2 or not snaps[0].price:
            continue
        old = next((x for x in snaps[1:] if x.as_of.replace(tzinfo=x.as_of.tzinfo or timezone.utc) <= cutoff), snaps[-1])
        if not old.price:
            continue
        move = snaps[0].price / old.price - 1
        if abs(move) * 100 >= threshold_pct:
            s.add(FeedEvent(event_date=snaps[0].as_of.date(), company_id=cid, event_type="price_move",
                            title=f"Movimiento de precio {move:+.0%} desde {old.as_of.date()}",
                            verification="market_data",
                            detail=f"{old.price:.2f} -> {snaps[0].price:.2f} ({snaps[0].source})",
                            impact="unknown", triggers_review=True))
            n += 1
    return n


def add_manual(s: Session, title: str, company_id: str | None = None, url: str | None = None,
               event_type: str = "news", verification: str = "media_report", detail: str | None = None,
               bottleneck_id: str | None = None, event_date: date | None = None, impact: str = "unknown") -> FeedEvent:
    e = FeedEvent(event_date=event_date or date.today(), company_id=company_id, bottleneck_id=bottleneck_id,
                  event_type=event_type, title=title, detail=detail, url=url, verification=verification,
                  impact=impact, triggers_review=True)
    s.add(e)
    return e
