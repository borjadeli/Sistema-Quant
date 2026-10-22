"""Capa de datos del dashboard: SOLO LECTURA. Nunca llama a LLMs ni escribe en la base.

- Mapa, bottlenecks e investigación semilla: archivos curados (YAML/JSON) vía los loaders de `qa`.
- Empresas, scores, escenarios, cartera, feed y costos: tablas de la base seleccionada (real o demo).
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, inspect, text

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO / "src") not in sys.path:
    sys.path.insert(0, str(_REPO / "src"))

from qa.bottlenecks import load_bottlenecks  # noqa: E402
from qa.config import RESEARCH_DIR, ROOT, database_url, settings  # noqa: E402,F401  (ROOT = raíz del proyecto qa)
from qa.map.model import load_map  # noqa: E402
from qa.universe.model import load_universe  # noqa: E402

# ----------------------------------------------------------------------------- paleta (dataviz reference)
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
SEQ_BLUE = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"]
STATUS = {"good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a", "critical": "#d03b3b"}
INK = {"primary": "#0b0b0b", "secondary": "#52514e", "muted": "#898781", "grid": "#e1e0d9", "axis": "#c3c2b7",
       "surface": "#fcfcfb", "neutral": "#c3c2b7"}
BN_STATUS_COLOR = {"current_shortage": STATUS["critical"], "projected_shortage": STATUS["serious"],
                   "speculative_hypothesis": STATUS["warning"], "easing": STATUS["good"]}
BN_STATUS_LABEL = {"current_shortage": "⛔ escasez actual", "projected_shortage": "⚠️ escasez proyectada",
                   "speculative_hypothesis": "❔ hipótesis especulativa", "easing": "✅ aliviándose"}
# Rampa ordinal de severidad (azul, pasos >= 250 sobre fondo claro). Sin bottleneck = gris neutro.
# La comparten el treemap/sunburst y la red del mapa industrial.
SEV_BINS = [(8.5, "#184f95", "≥ 8.5"), (7.5, "#2a78d6", "7.5 – 8.5"), (6.0, "#5598e7", "6 – 7.5"),
            (0.0, "#86b6ef", "< 6")]
SEV_NONE_COLOR = "#e1e0d9"


def sev_color(severity: float | None) -> str:
    if severity is None:
        return SEV_NONE_COLOR
    return next(c for lo, c, _ in SEV_BINS if severity >= lo)


DB_OPTIONS = {"real": "Base real", "demo": "Demo sintética"}

# ----------------------------------------------------------------------------- etiquetas en español
# Cabeceras de tablas: nombre crudo de columna -> etiqueta visible (st.column_config acepta str como label).
LABELS = {
    "company_id": "empresa", "name": "nombre", "exchange": "bolsa", "country": "país", "currency": "moneda",
    "status": "estado", "verification": "verificación", "created_at": "fecha", "tier": "capa del embudo",
    "tier_reached": "capa alcanzada", "qty": "cantidad", "quantity": "cantidad", "price": "precio",
    "value": "valor", "weight": "peso", "price_source": "fuente del precio", "price_time": "hora del precio",
    "symbol": "símbolo", "side": "operación", "fees": "comisiones", "slippage": "slippage", "mode": "modo",
    "recommendation_id": "recomendación #", "agent": "agente", "model": "modelo", "cost_usd": "costo",
    "input_tokens": "tokens entrada", "output_tokens": "tokens salida", "prompt_version": "versión prompt",
    "input_hash": "hash de entrada", "error": "error", "topic": "tema", "positions": "posiciones",
    "resolving_evidence": "evidencia que lo resolvería", "claim": "afirmación", "source_name": "fuente",
    "source_url": "enlace", "secondary_url": "enlace secundario", "source_date": "fecha de la fuente",
    "date": "fecha", "evidence_type": "tipo de evidencia", "company": "empresa", "ticker_if_listed": "ticker",
    "note": "nota", "event_date": "fecha del evento", "bottleneck_id": "cuello de botella", "event_type": "tipo",
    "title": "título", "detail": "detalle", "impact": "impacto", "triggers_review": "dispara revisión",
    "processed": "procesado", "url": "enlace", "change_type": "tipo de cambio", "rationale": "fundamento",
    "reviewed_by": "revisado por", "calls": "llamadas", "usd_per_call": "USD/llamada", "usd_total": "USD total",
    "event": "evento", "flag": "red flag", "counterparty": "contraparte", "relationship": "relación",
    "reused_from": "reutiliza corrida #", "pipeline_run_id": "corrida", "id": "id",
}
LEVEL_ES = {"low": "baja", "medium": "media", "high": "alta"}
ACTION_ES = {"BUY": "Comprar", "ADD": "Aumentar", "HOLD": "Mantener", "TRIM": "Reducir", "SELL": "Vender",
             "AVOID": "Evitar"}
SIDE_ES = {"BUY": "compra", "SELL": "venta"}
MODE_ES = {"simulated": "simulado", "operated": "operado"}
COMPANY_STATUS_ES = {"active": "activa", "candidate": "candidata", "delisted": "deslistada", "excluded": "excluida"}
VERIFICATION_ES = {"confirmed_by_filing": "✅ documento regulatorio", "company_statement": "declaración corporativa",
                   "media_report": "prensa / terceros", "search_summary": "🔎 resumen de buscador",
                   "analyst_prior": "prior del analista", "hypothesis": "hipótesis",
                   "synthetic_test_data": "🧪 sintético"}
RUN_STATUS_ES = {"ok": "ok", "reused": "reutilizada", "skipped_budget": "salteada (presupuesto)", "error": "error",
                 "refusal": "rechazada por el modelo", "dry_run": "simulación (dry run)"}
IMPACT_ES = {"thesis_positive": "▲ a favor de la tesis", "thesis_negative": "▼ contra la tesis",
             "neutral": "neutral", "unknown": "sin evaluar"}
EVENT_TYPE_ES = {"filing": "filing regulatorio", "earnings": "resultados", "contract": "contrato",
                 "capacity": "capacidad", "price_move": "movimiento de precio", "regulation": "regulación",
                 "news": "noticia", "map_change": "cambio de mapa"}
PROPOSAL_STATUS_ES = {"pending": "pendiente", "approved": "aprobada", "rejected": "rechazada", "applied": "aplicada"}
FACTOR_ES = {"bottleneck_importance": "importancia del bottleneck", "economic_capture": "captura económica",
             "valuation_asymmetry": "asimetría de valuación", "catalysts": "catalizadores",
             "financial_quality": "calidad financiera", "informational_edge": "ventaja informativa"}
METRIC_ES = {
    "revenue": "ingresos", "cost_of_revenue": "costo de ventas", "gross_profit": "utilidad bruta",
    "operating_income": "resultado operativo (EBIT)", "net_income": "resultado neto", "d_and_a": "D&A",
    "ocf": "flujo de caja operativo", "capex": "capex", "sbc": "compensación en acciones",
    "rd_expense": "gasto en I+D", "interest_expense": "intereses", "income_tax": "impuestos",
    "pretax_income": "resultado antes de impuestos", "cash": "caja", "short_term_investments": "inversiones CP",
    "debt_total": "deuda total", "debt_noncurrent": "deuda no corriente", "debt_current": "deuda corriente",
    "equity": "patrimonio", "total_assets": "activos totales", "current_assets": "activos corrientes",
    "current_liabilities": "pasivos corrientes", "inventory": "inventarios", "receivables": "cuentas por cobrar",
    "rpo": "backlog (RPO)", "shares_outstanding": "acciones en circulación", "diluted_shares": "acciones diluidas",
    "revenue_growth_yoy": "crecimiento ingresos a/a (TTM)", "revenue_growth_q_yoy": "crecimiento ingresos a/a (trim.)",
    "gross_margin": "margen bruto", "ebitda": "EBITDA", "ebitda_margin": "margen EBITDA", "ebit_margin": "margen EBIT",
    "net_margin": "margen neto", "fcf": "flujo de caja libre", "fcf_margin": "margen FCF",
    "capex_intensity": "capex / ingresos", "sbc_pct_revenue": "SBC / ingresos", "cash_total": "caja + inversiones",
    "debt": "deuda", "net_debt": "deuda neta", "net_debt_to_ebitda": "deuda neta / EBITDA",
    "interest_coverage": "cobertura de intereses", "current_ratio": "liquidez corriente", "roic": "ROIC",
    "cash_runway_quarters": "runway de caja (trimestres)", "incremental_margin": "margen incremental",
}


def metric_label(k: str) -> str:
    """Nombre legible de una métrica fundamental (sufijos _ttm / _last_q incluidos)."""
    for suf, lab in (("_ttm", " (TTM)"), ("_last_q", " (último trimestre)")):
        if k.endswith(suf) and k[: -len(suf)] in METRIC_ES:
            return METRIC_ES[k[: -len(suf)]] + lab
    return METRIC_ES.get(k, k.replace("_", " "))


CHANGE_TYPE_ES = {"add_node": "agregar nodo", "add_edge": "agregar dependencia", "add_company": "agregar empresa",
                  "link_company": "vincular empresa", "add_bottleneck": "agregar bottleneck",
                  "update_bottleneck": "actualizar bottleneck"}


def es(mapping: dict, v):  # noqa: ANN001, ANN201
    """Traduce un valor con el diccionario; deja el original si no está (nunca oculta un valor desconocido)."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    return mapping.get(v, v)


def colcfg(df: pd.DataFrame, **extra) -> dict:
    """column_config con etiquetas en español para las columnas crudas presentes + overrides explícitos."""
    return {c: LABELS[c] for c in df.columns if c in LABELS} | extra


def table_height(n_rows: int, max_rows: int = 40) -> int:
    """Altura de st.dataframe que muestra todas las filas (hasta max_rows) sin scroll interno."""
    return 38 + 35 * max(1, min(n_rows, max_rows))


def company_label(cid: str, name: str | None) -> str:
    """'TICKER — Nombre', sin repetir el ticker cuando el nombre ya lo contiene (empresas sintéticas)."""
    if not name or name.strip().upper() == cid.upper():
        return cid
    if name.upper().startswith(cid.upper()):
        return name
    return f"{cid} — {name}"


def plotly_layout(fig, height: int | None = None, legend: bool = True):  # noqa: ANN001, ANN201
    fig.update_layout(
        template="plotly_white", font=dict(family="system-ui, -apple-system, Segoe UI, sans-serif",
                                           color=INK["primary"], size=13),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor=INK["surface"], colorway=SERIES,
        margin=dict(l=10, r=10, t=40, b=10), showlegend=legend,
        hoverlabel=dict(bgcolor="white", font_size=12, font_color=INK["primary"]),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0))
    fig.update_xaxes(gridcolor=INK["grid"], linecolor=INK["axis"], zerolinecolor=INK["axis"],
                     tickfont=dict(color=INK["secondary"]))
    fig.update_yaxes(gridcolor=INK["grid"], linecolor=INK["axis"], zerolinecolor=INK["axis"],
                     tickfont=dict(color=INK["secondary"]))
    if height:
        fig.update_layout(height=height)
    return fig


# ----------------------------------------------------------------------------- selección de base
_SQLITE_URL = re.compile(r"^sqlite(?:\+\w+)?:///(?P<path>[^?]+)")


def db_url(key: str) -> str:
    if key == "demo":
        from qa.demo import DEMO_DB
        return DEMO_DB
    return database_url()   # resuelve rutas sqlite relativas contra qa.config.ROOT (y respeta QA_DATABASE_URL)


def db_path(key: str) -> Path | None:
    """Ruta absoluta del archivo SQLite, o None si la URL no es SQLite en archivo (el dashboard solo lee SQLite)."""
    mt = _SQLITE_URL.match(db_url(key))
    if not mt or mt["path"] in ("", ":memory:"):
        return None
    p = Path(mt["path"])
    return (p if p.is_absolute() else ROOT / p).resolve()


def db_error(key: str) -> str | None:
    """Mensaje para st.error cuando la base no es utilizable por el dashboard (None = OK)."""
    url = db_url(key)
    if db_path(key) is None:
        shown = re.sub(r"//[^/@]*@", "//***@", url)          # no mostrar credenciales
        return (f"La URL de base `{shown}` no es un archivo SQLite. El dashboard solo lee bases SQLite en archivo "
                "(`sqlite:///ruta/a/base.sqlite`). Revisá `QA_DATABASE_URL` o `database_url` en "
                "`config/settings.yaml`.")
    if not db_path(key).exists():
        return (f"La base `{db_path(key)}` no existe. Ejecutá `{'qa demo' if key == 'demo' else 'qa init'}` "
                f"en la raíz del proyecto (`{ROOT}`).")
    return None


def current_db() -> str:
    return st.session_state.get("db", "real")


@st.cache_resource
def _engine(url: str):  # noqa: ANN202
    mt = _SQLITE_URL.match(url)
    path = mt["path"] if mt else url.removeprefix("sqlite:///")
    # modo solo lectura de SQLite: el dashboard no puede escribir aunque quisiera
    return create_engine(f"sqlite:///file:{path}?mode=ro&uri=true", future=True)


def engine():  # noqa: ANN201
    key = current_db()
    if db_error(key):
        return None
    return _engine(db_url(key))


@st.cache_data(ttl=60, show_spinner=False)
def _query(url: str, sql: str, params: tuple = ()) -> pd.DataFrame:
    eng = _engine(url)
    with eng.connect() as c:
        if not inspect(c).get_table_names():
            return pd.DataFrame()
        return pd.read_sql(text(sql), c, params=dict(params))


def query(sql: str, **params) -> pd.DataFrame:
    if engine() is None:
        return pd.DataFrame()
    try:
        return _query(db_url(current_db()), sql, tuple(sorted(params.items())))
    except Exception as e:  # tabla inexistente en una base vieja, etc.
        st.caption(f"⚠️ consulta no disponible: {type(e).__name__}")
        return pd.DataFrame()


@st.cache_data(ttl=60, show_spinner=False)
def _incomplete_runs(url: str) -> frozenset:
    eng = _engine(url)
    with eng.connect() as c:
        if "pipeline_runs" not in inspect(c).get_table_names():
            return frozenset()          # base anterior a la tabla de corridas: no hay forma de distinguir
        rows = c.execute(text("SELECT id FROM pipeline_runs WHERE status != 'completed'")).all()
    return frozenset(r[0] for r in rows)


def complete_only(df: pd.DataFrame) -> pd.DataFrame:
    """Descarta filas de corridas que no terminaron (cortadas o en curso): sus scores/escenarios/recomendaciones
    son parciales y podrían ocultar el resultado de la última corrida completa."""
    if df.empty or "pipeline_run_id" not in df or engine() is None:
        return df
    bad = _incomplete_runs(db_url(current_db()))
    return df[~df["pipeline_run_id"].isin(bad)] if bad else df


def js(v):  # noqa: ANN001, ANN201
    """Columnas JSON de SQLite -> objetos Python."""
    if isinstance(v, (dict, list)) or v is None:
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return v


# ----------------------------------------------------------------------------- archivos curados
@st.cache_resource
def industry_map():  # noqa: ANN201
    return load_map()


@st.cache_resource
def universe_yaml():  # noqa: ANN201
    return load_universe()


@st.cache_resource
def bottleneck_registry():  # noqa: ANN201
    return load_bottlenecks(industry_map(), universe_yaml())


@st.cache_data(show_spinner=False)
def seed_research() -> dict:
    p = RESEARCH_DIR / "seed_companies_verified.json"
    if not p.exists():
        return {}
    return {r["ticker"]: r for r in json.loads(p.read_text(encoding="utf-8"))}


def cfg() -> dict:
    return settings()


def layer_name(lid: str | None) -> str | None:
    if lid is None or (isinstance(lid, float) and pd.isna(lid)):
        return None
    return industry_map().layers.get(lid, lid)


# ----------------------------------------------------------------------------- tablas de la base
def companies() -> pd.DataFrame:
    df = query("SELECT * FROM companies")
    if df.empty:
        return df
    for col in ("functions", "exposure", "tags", "products", "bottlenecks"):
        if col in df:
            df[col] = df[col].map(js)
    df["nodes"] = df["functions"].map(lambda f: [x["node"] for x in (f or [])])
    return df


def node_company_counts() -> Counter:
    df = companies()
    cnt: Counter = Counter()
    if not df.empty:
        for nodes in df["nodes"]:
            cnt.update(nodes)
    return cnt


def scenarios() -> pd.DataFrame:
    """Todos los sets de escenarios (JSON parseado + ER por horizonte)."""
    df = complete_only(query("SELECT * FROM scenarios ORDER BY created_at, id"))
    if df.empty:
        return df
    for col in ("assumptions", "results", "expected_return", "reverse_dcf"):
        df[col] = df[col].map(js)
    for h in (12, 24, 36):
        df[f"er{h}"] = df["expected_return"].map(lambda d, h=h: (d or {}).get(str(h)))
    return df


def latest_scenarios() -> pd.DataFrame:
    df = scenarios()
    if df.empty:
        return df
    return df.groupby("company_id", as_index=False).tail(1).reset_index(drop=True)


SCEN_COLS = ["er12", "er24", "er36", "prob_loss", "scenario_created_at"]


def latest_scores() -> pd.DataFrame:
    """Último score por empresa, unido a los escenarios EXACTOS que usó (scores.scenario_id).

    Un score sin escenarios (scenario_id NULL, o base vieja sin la columna) deja ER y P(pérdida) en NaN: la UI
    los muestra como '—' en lugar de tomar "el último escenario de la empresa", que podría ser posterior.
    """
    df = complete_only(query("SELECT * FROM scores ORDER BY created_at, id"))
    if df.empty:
        return df
    df = df.groupby("company_id", as_index=False).tail(1)
    for col in ("factors", "caps_applied", "kill_switches", "alerts", "missing_data"):
        df[col] = df[col].map(js)
    if "scenario_id" not in df:
        df["scenario_id"] = None
    df["scenario_id"] = pd.to_numeric(df["scenario_id"], errors="coerce")
    sc = scenarios()
    if sc.empty:
        df = df.assign(**{c: float("nan") for c in SCEN_COLS})
    else:
        sc = sc.rename(columns={"created_at": "scenario_created_at"}).assign(scenario_id=sc["id"].astype(float))
        df = df.merge(sc[["scenario_id", *SCEN_COLS]], on="scenario_id", how="left")
    return df.reset_index(drop=True)


def latest_snapshots() -> pd.DataFrame:
    df = query("SELECT * FROM market_snapshots ORDER BY as_of")
    if df.empty:
        return df
    return df.groupby("company_id", as_index=False).tail(1).reset_index(drop=True)


def funnel_state() -> pd.DataFrame:
    df = query("SELECT * FROM funnel_state")
    if not df.empty:
        df["l0_reasons"] = df["l0_reasons"].map(lambda r: js(r) or [])
    return df


def funnel_explain(row) -> str:  # noqa: ANN001
    """Texto corto del estado de una empresa en el embudo (por qué no tiene score, etc.)."""
    if row is None:
        return "sin estado de embudo (`qa funnel --tiers L0`)"
    tier = row.get("tier_reached") or "L0"
    if row.get("l0_pass") in (0, False):
        why = "; ".join(row.get("l0_reasons") or []) or "sin motivo registrado"
        return f"{tier} · no pasa L0: {why}"
    if pd.isna(row.get("last_score")):
        return f"{tier} · pasa L0, sin score todavía (L2/L3)"
    return f"{tier} · score {row['last_score']:.2f}"


def data_quality_flags() -> dict:
    """Qué tipos de verificación hay en la base (para el banner de advertencia)."""
    df = query("SELECT verification, COUNT(*) AS n FROM datapoints GROUP BY verification")
    return dict(zip(df["verification"], df["n"])) if not df.empty else {}


# ----------------------------------------------------------------------------- UI común
def banner() -> None:
    key = current_db()
    err = db_error(key)
    if err:
        st.error(err)
        return
    flags = data_quality_flags()
    if key == "demo" or "synthetic_test_data" in flags:
        st.warning("**DEMO · DATOS SINTÉTICOS.** Empresas `TEST_*` y agentes guionados. Nada de esta vista es "
                   "análisis real ni una recomendación.", icon="🧪")
    if flags.get("search_summary"):
        st.info(f"{flags['search_summary']} datapoints son **datos no verificados contra documento primario** "
                "(leídos en resúmenes de buscador). Contrastar con EDGAR/SEDAR antes de usarlos.", icon="🔎")
    if key == "real" and not flags.get("confirmed_by_filing") and query("SELECT 1 FROM market_snapshots LIMIT 1").empty:
        st.caption("Sin datos de SEC/mercado todavía: ejecutá `qa ingest` cuando la red permita acceder a "
                   "data.sec.gov y Yahoo Finance.")


def fmt_pct(x, digits: int = 0) -> str:  # noqa: ANN001
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "—"
    return f"{x:+.{digits}%}"


def fmt_money(x, currency: str | None = None) -> str:  # noqa: ANN001
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "—"
    a = abs(x)
    out = f"{x:,.0f}"
    for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if a >= div:
            out = f"{x / div:,.1f}{suf}"
            break
    return f"{out} {currency}" if currency else out


def fmt_dt(x, with_time: bool = True) -> str:  # noqa: ANN001
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "—"
    t = pd.to_datetime(x, errors="coerce", utc=True)
    if pd.isna(t):
        return str(x)
    return t.strftime("%Y-%m-%d %H:%M UTC" if with_time else "%Y-%m-%d")


def company_select(label: str = "Empresa", key: str = "company", default: str | None = None) -> str | None:
    df = companies()
    if df.empty:
        st.info("No hay empresas en la base seleccionada.")
        return None
    seeds = df["tags"].map(lambda t: "seed" in (t or []))
    df = df.assign(_seed=seeds).sort_values(["_seed", "id"], ascending=[False, True])
    opts = df["id"].tolist()
    names = dict(zip(df["id"], df["name"]))
    idx = opts.index(default) if default in opts else 0
    wkey = f"{key}_{current_db()}"          # una selección por base: cambiar de base no deja valores huérfanos
    if wkey in st.session_state and st.session_state[wkey] not in opts:
        del st.session_state[wkey]
    return st.selectbox(label, opts, index=idx, key=wkey, format_func=lambda x: company_label(x, names.get(x)))
