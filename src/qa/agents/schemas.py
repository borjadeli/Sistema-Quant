"""Esquemas de salida de cada agente. Todo output de agente es INTERPRETATION: queda separado de los datos.

Regla común: cada afirmación factual lleva `source_url` (o null si es inferencia) y un `verification`.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from qa.valuation.core import ScenarioAssumptions

VerificationLit = Literal["confirmed_by_filing", "company_statement", "media_report", "search_summary",
                          "analyst_prior", "hypothesis"]
Rubric = Literal["0-2", "3-4", "5-6", "7-8", "9-10"]


class Evidence(BaseModel):
    claim: str
    source_url: str | None
    source_date: str | None
    verification: VerificationLit


class RubricScore(BaseModel):
    rubric_band: Rubric = Field(description="banda de la rúbrica que mejor describe la evidencia")
    score: float = Field(ge=0, le=10, description="puntaje dentro de la banda elegida")
    rationale: str
    evidence: list[Evidence]
    missing_information: list[str]


# --------------------------------------------------------------------------- L1 triage
class TriageOutput(BaseModel):
    real_exposure: Literal["direct", "partial", "narrative_only", "none", "unclear"]
    exposure_explanation: str
    primary_bottleneck_id: str | None = Field(description="id de bottleneck del registro, o null")
    estimated_exposure_pct_revenue: float | None = Field(description="0-100; null si no se puede estimar")
    exposure_pct_basis: Literal["reported_segment", "management_statement", "estimate", "unknown"]
    quick_red_flags: list[str]
    priority: float = Field(ge=0, le=10, description="qué tan prometedora es para análisis profundo")
    uncertainty: float = Field(ge=0, le=10, description="cuánto podría cambiar la visión con más análisis")
    recommended_next: Literal["L2", "watch", "discard"]
    reason: str


# --------------------------------------------------------------------------- L2
class RedFlags(BaseModel):
    going_concern: bool
    customer_concentration_gt_40: bool
    export_control_exposure: bool
    restatement: bool
    recent_equity_raise: bool
    financing_committed: bool = Field(description="financiamiento comprometido que cubre la quema de caja")
    other: list[str]


class FundamentalOutput(BaseModel):
    business_quality_summary: str
    exposure_pct_revenue: float | None = Field(description="% de ingresos atribuible al/los nodo(s) restringidos")
    exposure_basis: Literal["reported_segment", "management_statement", "estimate", "unknown"]
    growth_drivers: list[str]
    pricing_power_evidence: list[Evidence]
    capacity_and_backlog: str
    margin_trajectory: str
    balance_sheet_assessment: str
    red_flags: RedFlags
    economic_capture: RubricScore
    data_issues: list[str] = Field(description="inconsistencias o faltantes detectados en el data pack")


class IndustrialOutput(BaseModel):
    company_role_in_chain: str
    bottleneck_links: list[str] = Field(description="ids de bottlenecks relevantes")
    bottleneck_adjustment: float = Field(ge=-2, le=2, description="ajuste +-2 al puntaje por regla, con evidencia")
    adjustment_rationale: str
    substitution_threats: list[str]
    new_dependencies_hypotheses: list[str] = Field(description="relaciones NO confirmadas (hipótesis)")
    evidence: list[Evidence]


class Catalyst(BaseModel):
    description: str
    expected_date: str | None = Field(description="YYYY-MM-DD o YYYY-MM o null")
    type: Literal["earnings", "contract", "capacity", "product", "regulatory", "financing", "index", "other"]
    direction: Literal["positive", "negative", "uncertain"]
    probability: float = Field(ge=0, le=1)
    impact: Literal["low", "medium", "high"]
    evidence: Evidence


class CatalystOutput(BaseModel):
    catalysts: list[Catalyst]
    catalysts_score: RubricScore


class ValuationOutput(BaseModel):
    scenarios: list[ScenarioAssumptions] = Field(description="exactamente bear, base y bull; probabilidades suman 1")
    comparables: list[str]
    method_notes: str = Field(description="por qué este múltiplo de salida es apropiado para el negocio")
    key_sensitivities: list[str]


class ExpectationsOutput(BaseModel):
    priced_in_summary: str
    implied_vs_base: Literal["market_more_optimistic", "roughly_aligned", "market_more_pessimistic", "unclear"]
    consensus_notes: str
    informational_edge: RubricScore


# --------------------------------------------------------------------------- L3
class BearPoint(BaseModel):
    argument: str
    evidence: list[Evidence]
    severity: Literal["low", "medium", "high", "thesis_killer"]
    what_would_resolve_it: str


class BearOutput(BaseModel):
    bear_points: list[BearPoint]
    is_bear_scenario_too_optimistic: bool
    proposed_bear_changes: str
    probability_challenge: str = Field(description="crítica a las probabilidades asignadas")
    overall_view: Literal["thesis_intact", "thesis_weakened", "thesis_broken"]


class AuditIssue(BaseModel):
    area: Literal["source", "calculation", "date", "consistency", "unsupported_claim", "data_quality"]
    description: str
    severity: Literal["minor", "major", "critical"]
    fix: str


class AuditOutput(BaseModel):
    verdict: Literal["pass", "pass_with_issues", "fail"]
    issues: list[AuditIssue]
    unverified_claims: list[str]
    disagreements_to_log: list[str] = Field(description="discrepancias entre agentes y qué evidencia las resolvería")


class Decision(BaseModel):
    company_id: str
    action: Literal["BUY", "ADD", "HOLD", "TRIM", "SELL", "AVOID"]
    target_weight: float = Field(ge=0, le=1)
    conviction: Literal["low", "medium", "high"]
    thesis: str
    catalysts: list[str]
    risks: list[str]
    invalidation_triggers: list[str]


class PortfolioOutput(BaseModel):
    decisions: list[Decision]
    cash_weight: float = Field(ge=0, le=1)
    portfolio_rationale: str
    correlation_and_concentration_notes: str
    rejected_with_reason: list[str]


# --------------------------------------------------------------------------- mapa y discovery
class MapProposal(BaseModel):
    change_type: Literal["add_node", "add_edge", "add_bottleneck", "update_bottleneck_status", "add_company",
                         "link_company"]
    payload_json: str = Field(description="JSON con los campos del cambio (id, name, type, source, target, ...)")
    rationale: str
    evidence: list[Evidence]


class MapCuratorOutput(BaseModel):
    proposals: list[MapProposal]
    stale_items: list[str] = Field(description="partes del mapa/registro que necesitan revisión")


class DiscoveredCompany(BaseModel):
    name: str
    ticker: str | None
    exchange: str | None
    country: str | None
    currency: str | None = Field(None, description="moneda de cotización ISO-4217 (p.ej. JPY en TSE)")
    map_nodes: list[str]
    role: str
    estimated_exposure: Literal["H", "M", "L", "unknown"]
    why_overlooked: str
    evidence: list[Evidence]
    confidence: Literal["low", "medium", "high"]


class DiscoveryOutput(BaseModel):
    candidates: list[DiscoveredCompany]
    searched_for: str
