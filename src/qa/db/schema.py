"""Esquema de base de datos persistente (SQLite por defecto; portable a Postgres).

Separación explícita de capas (regla 13):
  1. Datos originales      -> `datapoints` (kind=original), `prices`, `option_quotes`, `documents`
  2. Datos derivados       -> `datapoints` (kind=derived, con `inputs`)
  3. Estimaciones          -> `scenarios`, `datapoints` (kind=estimate)
  4. Interpretaciones      -> `agent_runs`, `disagreements`, `scores.evidence`
La cartera simulada (`recommendations`, `paper_trades`, `portfolio_snapshots`) es append-only:
nunca se reescribe una decisión pasada, solo se agregan revisiones.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text,
                        UniqueConstraint)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(dt: datetime | None) -> datetime | None:
    """SQLite no conserva la zona horaria: todo timestamp se interpreta como UTC."""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


class Base(DeclarativeBase):
    pass


# --------------------------------------------------------------------------- mapa industrial
class MapNode(Base):
    """Nodo del mapa: capa, segmento, componente, material, capacidad o servicio."""
    __tablename__ = "map_nodes"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    level: Mapped[str] = mapped_column(String(20))          # layer | segment | node
    node_type: Mapped[str | None] = mapped_column(String(30))  # component|material|capacity|service|technology|equipment
    parent_id: Mapped[str | None] = mapped_column(String(80), ForeignKey("map_nodes.id"))
    description: Mapped[str | None] = mapped_column(Text)
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    map_version: Mapped[str | None] = mapped_column(String(40))


class MapEdge(Base):
    """Dependencia entre nodos: `source` es insumo de `target` (source -> target)."""
    __tablename__ = "map_edges"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(String(80), ForeignKey("map_nodes.id"))
    target_id: Mapped[str] = mapped_column(String(80), ForeignKey("map_nodes.id"))
    relation: Mapped[str] = mapped_column(String(40), default="input_to")
    note: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (UniqueConstraint("source_id", "target_id", "relation"),)


class MapChangeProposal(Base):
    """Propuesta del agente Map Curator / Discovery. No se aplica sin aprobación (humana o auditor)."""
    __tablename__ = "map_change_proposals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    agent_run_id: Mapped[int | None] = mapped_column(ForeignKey("agent_runs.id"))
    change_type: Mapped[str] = mapped_column(String(40))   # add_node|add_edge|add_company|link_company|add_bottleneck|update_bottleneck
    payload: Mapped[dict] = mapped_column(JSON)
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    rationale: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|approved|rejected|applied
    reviewed_by: Mapped[str | None] = mapped_column(String(80))
    review_note: Mapped[str | None] = mapped_column(Text)


# --------------------------------------------------------------------------- universo
class Company(Base):
    __tablename__ = "companies"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)    # ticker canónico, p.ej. "HPS.A-TSX"
    ticker: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(200))
    exchange: Mapped[str | None] = mapped_column(String(20))
    country: Mapped[str | None] = mapped_column(String(40))
    currency: Mapped[str | None] = mapped_column(String(5))
    yahoo_symbol: Mapped[str | None] = mapped_column(String(30))
    cik: Mapped[str | None] = mapped_column(String(12))
    gics_sector: Mapped[str | None] = mapped_column(String(60))
    primary_layer: Mapped[str | None] = mapped_column(String(80))
    functions: Mapped[list] = mapped_column(JSON, default=list)       # nodos del mapa con rol
    products: Mapped[list] = mapped_column(JSON, default=list)
    exposure: Mapped[dict] = mapped_column(JSON, default=dict)        # ai/energy/commodities: nivel + base
    bottlenecks: Mapped[list] = mapped_column(JSON, default=list)
    ibkr_accessible: Mapped[bool | None] = mapped_column(Boolean)
    verification: Mapped[str] = mapped_column(String(40), default="analyst_prior")
    status: Mapped[str] = mapped_column(String(20), default="active")  # active|candidate|delisted|excluded
    added_at: Mapped[date | None] = mapped_column(Date)
    added_by: Mapped[str | None] = mapped_column(String(80))
    notes: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list] = mapped_column(JSON, default=list)


class CompanyRelationship(Base):
    __tablename__ = "company_relationships"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_company: Mapped[str] = mapped_column(String(80))   # puede ser una empresa fuera del universo
    target_company: Mapped[str] = mapped_column(String(80))
    relation: Mapped[str] = mapped_column(String(30))         # supplier_of|customer_of|partner|offtake|lease|competitor
    detail: Mapped[str | None] = mapped_column(Text)
    verification: Mapped[str] = mapped_column(String(40))     # Verification enum
    source_url: Mapped[str | None] = mapped_column(Text)
    source_date: Mapped[date | None] = mapped_column(Date)


# --------------------------------------------------------------------------- cuellos de botella
class Bottleneck(Base):
    __tablename__ = "bottlenecks"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    layer: Mapped[str | None] = mapped_column(String(60))
    map_nodes: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(30))           # current_shortage|projected_shortage|speculative_hypothesis|easing
    constrained_item: Mapped[str | None] = mapped_column(Text)
    demand_driver: Mapped[str | None] = mapped_column(Text)
    supply_limit: Mapped[str | None] = mapped_column(Text)
    estimated_duration: Mapped[str | None] = mapped_column(Text)
    severity: Mapped[float | None] = mapped_column(Float)     # 0-10, rúbrica documentada
    confidence: Mapped[str | None] = mapped_column(String(10))
    suppliers: Mapped[list] = mapped_column(JSON, default=list)
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    counter_evidence: Mapped[list] = mapped_column(JSON, default=list)
    priced_in_assessment: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# --------------------------------------------------------------------------- datos
class Document(Base):
    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[str | None] = mapped_column(String(40))
    doc_type: Mapped[str] = mapped_column(String(30))
    title: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[date | None] = mapped_column(Date)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    summary: Mapped[str | None] = mapped_column(Text)


class DataPointRow(Base):
    __tablename__ = "datapoints"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity: Mapped[str] = mapped_column(String(80), index=True)
    metric: Mapped[str] = mapped_column(String(80), index=True)
    value: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(30))
    currency: Mapped[str | None] = mapped_column(String(5))
    period_end: Mapped[date | None] = mapped_column(Date)
    period_type: Mapped[str | None] = mapped_column(String(10))
    fiscal_label: Mapped[str | None] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(20))
    verification: Mapped[str] = mapped_column(String(40))
    source_name: Mapped[str] = mapped_column(String(120))
    source_url: Mapped[str | None] = mapped_column(Text)
    document_type: Mapped[str | None] = mapped_column(String(30))
    known_at: Mapped[date | None] = mapped_column(Date)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    inputs: Mapped[list] = mapped_column(JSON, default=list)
    author: Mapped[str | None] = mapped_column(String(80))
    note: Mapped[str | None] = mapped_column(Text)
    # Una fila por VINTAGE (known_at): los valores re-expresados no pisan lo que se sabía antes.
    __table_args__ = (UniqueConstraint("entity", "metric", "period_type", "period_end", "kind",
                                       "source_name", "known_at", name="uq_datapoint"),)


class Price(Base):
    __tablename__ = "prices"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(30), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    open: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    adj_close: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str | None] = mapped_column(String(5))
    source: Mapped[str] = mapped_column(String(40))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("symbol", "date", "source"),)


class MarketSnapshot(Base):
    """Foto de mercado de una empresa a un instante (market cap, shares, liquidez)."""
    __tablename__ = "market_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[str] = mapped_column(String(40), index=True)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    price: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str | None] = mapped_column(String(5))
    shares_outstanding: Mapped[float | None] = mapped_column(Float)
    market_cap: Mapped[float | None] = mapped_column(Float)
    market_cap_usd: Mapped[float | None] = mapped_column(Float)
    adv_usd_20d: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(40))


class OptionQuote(Base):
    __tablename__ = "option_quotes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    underlying: Mapped[str] = mapped_column(String(30), index=True)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expiry: Mapped[date] = mapped_column(Date)
    strike: Mapped[float] = mapped_column(Float)
    right: Mapped[str] = mapped_column(String(1))   # C | P
    bid: Mapped[float | None] = mapped_column(Float)
    ask: Mapped[float | None] = mapped_column(Float)
    last: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)
    open_interest: Mapped[float | None] = mapped_column(Float)
    implied_vol: Mapped[float | None] = mapped_column(Float)
    underlying_price: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(40))


# --------------------------------------------------------------------------- agentes y análisis
class AgentRun(Base):
    """Cada llamada de agente queda registrada: modelo, prompt version, input hash, salida, costo."""
    __tablename__ = "agent_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    pipeline_run_id: Mapped[str | None] = mapped_column(String(40), index=True)
    agent: Mapped[str] = mapped_column(String(40), index=True)
    tier: Mapped[str] = mapped_column(String(20))
    subject: Mapped[str | None] = mapped_column(String(80), index=True)  # company_id / bottleneck_id / "portfolio"
    model: Mapped[str] = mapped_column(String(40))
    prompt_version: Mapped[str] = mapped_column(String(20))
    input_hash: Mapped[str] = mapped_column(String(32), index=True)
    config_hash: Mapped[str | None] = mapped_column(String(16))
    decision_time: Mapped[date | None] = mapped_column(Date)
    output: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20))   # ok|error|refusal|skipped_budget|reused
    reused_from: Mapped[int | None] = mapped_column(Integer)   # id del AgentRun reutilizado
    error: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    web_sources: Mapped[list] = mapped_column(JSON, default=list)


class Disagreement(Base):
    __tablename__ = "disagreements"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    pipeline_run_id: Mapped[str | None] = mapped_column(String(40))
    company_id: Mapped[str] = mapped_column(String(40), index=True)
    topic: Mapped[str] = mapped_column(Text)
    positions: Mapped[dict] = mapped_column(JSON)          # {agent: posición}
    resolving_evidence: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open|resolved


class FunnelState(Base):
    """Estado de cada empresa en el embudo de análisis."""
    __tablename__ = "funnel_state"
    company_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tier_reached: Mapped[str] = mapped_column(String(10))     # L0|L1|L2|L3
    l0_pass: Mapped[bool | None] = mapped_column(Boolean)
    l0_reasons: Mapped[list] = mapped_column(JSON, default=list)
    priority: Mapped[float | None] = mapped_column(Float)
    last_score: Mapped[float | None] = mapped_column(Float)
    last_updated: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_input_hash: Mapped[dict] = mapped_column(JSON, default=dict)   # {tier: hash}


class ScoreRow(Base):
    __tablename__ = "scores"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    pipeline_run_id: Mapped[str | None] = mapped_column(String(40))
    company_id: Mapped[str] = mapped_column(String(40), index=True)
    tier: Mapped[str] = mapped_column(String(10))
    total: Mapped[float] = mapped_column(Float)
    total_uncapped: Mapped[float] = mapped_column(Float)
    factors: Mapped[dict] = mapped_column(JSON)          # {factor: {score, rationale, evidence, source: rule|agent}}
    caps_applied: Mapped[list] = mapped_column(JSON, default=list)
    kill_switches: Mapped[list] = mapped_column(JSON, default=list)
    alerts: Mapped[list] = mapped_column(JSON, default=list)
    missing_data: Mapped[list] = mapped_column(JSON, default=list)
    confidence: Mapped[str] = mapped_column(String(10))
    eligible: Mapped[bool] = mapped_column(Boolean)
    # escenarios exactos usados para este score (None = sin escenarios: ER/P(pérdida) no aplican)
    scenario_id: Mapped[int | None] = mapped_column(ForeignKey("scenarios.id"))


class ScenarioSet(Base):
    __tablename__ = "scenarios"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    company_id: Mapped[str] = mapped_column(String(40), index=True)
    author: Mapped[str] = mapped_column(String(60))
    price_at_creation: Mapped[float | None] = mapped_column(Float)
    price_source: Mapped[str | None] = mapped_column(String(80))
    assumptions: Mapped[dict] = mapped_column(JSON)       # supuestos por escenario (ESTIMATE)
    results: Mapped[dict] = mapped_column(JSON)           # valores por horizonte (DERIVED de supuestos)
    expected_return: Mapped[dict] = mapped_column(JSON)   # {12: x, 24: y, 36: z}
    prob_loss: Mapped[float | None] = mapped_column(Float)
    reverse_dcf: Mapped[dict | None] = mapped_column(JSON)
    probability_basis: Mapped[str] = mapped_column(Text, default="subjective")


# --------------------------------------------------------------------------- cartera simulada
class Recommendation(Base):
    __tablename__ = "recommendations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    pipeline_run_id: Mapped[str | None] = mapped_column(String(40))
    company_id: Mapped[str] = mapped_column(String(40), index=True)
    action: Mapped[str] = mapped_column(String(10))       # acción FINAL ejecutada: BUY|ADD|HOLD|TRIM|SELL|AVOID
    pm_action: Mapped[str | None] = mapped_column(String(10))       # lo que propuso el Portfolio Manager
    pm_target_weight: Mapped[float | None] = mapped_column(Float)
    constraint_notes: Mapped[list] = mapped_column(JSON, default=list)  # por qué el código cambió la propuesta
    target_weight: Mapped[float] = mapped_column(Float)
    conviction: Mapped[str] = mapped_column(String(10))   # low|medium|high
    price_at_decision: Mapped[float | None] = mapped_column(Float)
    price_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    price_source: Mapped[str | None] = mapped_column(String(80))
    thesis: Mapped[str] = mapped_column(Text)
    catalysts: Mapped[list] = mapped_column(JSON, default=list)
    risks: Mapped[list] = mapped_column(JSON, default=list)
    scenario_id: Mapped[int | None] = mapped_column(ForeignKey("scenarios.id"))
    score_id: Mapped[int | None] = mapped_column(ForeignKey("scores.id"))
    sources: Mapped[list] = mapped_column(JSON, default=list)
    data_cutoff: Mapped[date | None] = mapped_column(Date)    # info disponible al decidir
    invalidation_triggers: Mapped[list] = mapped_column(JSON, default=list)
    supersedes_id: Mapped[int | None] = mapped_column(ForeignKey("recommendations.id"))
    mode: Mapped[str] = mapped_column(String(12), default="simulated")   # simulated | operated


class PaperTrade(Base):
    __tablename__ = "paper_trades"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    portfolio: Mapped[str] = mapped_column(String(60))
    recommendation_id: Mapped[int | None] = mapped_column(ForeignKey("recommendations.id"))
    symbol: Mapped[str] = mapped_column(String(30))
    side: Mapped[str] = mapped_column(String(4))          # BUY|SELL
    quantity: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    price_source: Mapped[str] = mapped_column(String(80))
    price_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    fees: Mapped[float] = mapped_column(Float, default=0.0)
    slippage: Mapped[float] = mapped_column(Float, default=0.0)
    mode: Mapped[str] = mapped_column(String(12), default="simulated")


class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    portfolio: Mapped[str] = mapped_column(String(60), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    nav: Mapped[float] = mapped_column(Float)
    cash: Mapped[float] = mapped_column(Float)
    positions: Mapped[dict] = mapped_column(JSON)         # {symbol: {qty, price, value, weight}}
    __table_args__ = (UniqueConstraint("portfolio", "date"),)


# --------------------------------------------------------------------------- research feed
class FeedEvent(Base):
    __tablename__ = "feed_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    event_date: Mapped[date | None] = mapped_column(Date)
    company_id: Mapped[str | None] = mapped_column(String(40), index=True)
    bottleneck_id: Mapped[str | None] = mapped_column(String(80))
    event_type: Mapped[str] = mapped_column(String(40))   # filing|earnings|contract|capacity|price_move|regulation|news|map_change
    title: Mapped[str] = mapped_column(Text)
    detail: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    verification: Mapped[str] = mapped_column(String(40))
    impact: Mapped[str | None] = mapped_column(String(20))  # thesis_positive|thesis_negative|neutral|unknown
    triggers_review: Mapped[bool] = mapped_column(Boolean, default=False)
    processed: Mapped[bool] = mapped_column(Boolean, default=False)
