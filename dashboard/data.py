"""Capa de datos del dashboard: SOLO LECTURA. Nunca llama a LLMs ni escribe en la base.

- Mapa, bottlenecks e investigación semilla: archivos curados (YAML/JSON) vía los loaders de `qa`.
- Empresas, scores, escenarios, cartera, feed y costos: tablas de la base seleccionada (real o demo).
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, inspect, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from qa.bottlenecks import load_bottlenecks  # noqa: E402
from qa.config import RESEARCH_DIR, database_url, settings  # noqa: E402
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

DB_OPTIONS = {"real": "Base real (data/qa.sqlite)", "demo": "Demo sintética (data/demo.sqlite)"}


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
def db_url(key: str) -> str:
    if key == "demo":
        from qa.demo import DEMO_DB
        return DEMO_DB
    return database_url()


def db_path(key: str) -> Path:
    return Path(db_url(key).removeprefix("sqlite:///"))


def current_db() -> str:
    return st.session_state.get("db", "real")


@st.cache_resource
def _engine(url: str):  # noqa: ANN202
    path = url.removeprefix("sqlite:///")
    # modo solo lectura de SQLite: el dashboard no puede escribir aunque quisiera
    return create_engine(f"sqlite:///file:{path}?mode=ro&uri=true", future=True)


def engine():  # noqa: ANN201
    p = db_path(current_db())
    if not p.exists():
        return None
    return _engine(db_url(current_db()))


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


def latest_scores() -> pd.DataFrame:
    df = query("SELECT * FROM scores ORDER BY created_at")
    if df.empty:
        return df
    df = df.groupby("company_id", as_index=False).tail(1)
    for col in ("factors", "caps_applied", "kill_switches", "alerts", "missing_data"):
        df[col] = df[col].map(js)
    return df.reset_index(drop=True)


def latest_scenarios() -> pd.DataFrame:
    df = query("SELECT * FROM scenarios ORDER BY created_at")
    if df.empty:
        return df
    df = df.groupby("company_id", as_index=False).tail(1)
    for col in ("assumptions", "results", "expected_return", "reverse_dcf"):
        df[col] = df[col].map(js)
    df["er36"] = df["expected_return"].map(lambda d: (d or {}).get("36"))
    df["er24"] = df["expected_return"].map(lambda d: (d or {}).get("24"))
    df["er12"] = df["expected_return"].map(lambda d: (d or {}).get("12"))
    return df.reset_index(drop=True)


def latest_snapshots() -> pd.DataFrame:
    df = query("SELECT * FROM market_snapshots ORDER BY as_of")
    if df.empty:
        return df
    return df.groupby("company_id", as_index=False).tail(1).reset_index(drop=True)


def data_quality_flags() -> dict:
    """Qué tipos de verificación hay en la base (para el banner de advertencia)."""
    df = query("SELECT verification, COUNT(*) AS n FROM datapoints GROUP BY verification")
    return dict(zip(df["verification"], df["n"])) if not df.empty else {}


# ----------------------------------------------------------------------------- UI común
def banner() -> None:
    key = current_db()
    if not db_path(key).exists():
        st.error(f"La base `{db_path(key).relative_to(ROOT)}` no existe. Ejecutá "
                 f"`{'qa demo' if key == 'demo' else 'qa init'}` en la raíz del proyecto.")
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


def fmt_money(x) -> str:  # noqa: ANN001
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "—"
    a = abs(x)
    for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if a >= div:
            return f"{x / div:,.1f}{suf}"
    return f"{x:,.0f}"


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
    return st.selectbox(label, opts, index=idx, key=wkey, format_func=lambda x: f"{x} — {names.get(x, '')}")
