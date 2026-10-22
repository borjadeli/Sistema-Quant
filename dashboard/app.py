"""Quant Advisory — dashboard (solo lectura).

Ejecutar desde la raíz del proyecto:
    streamlit run dashboard/app.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

import data  # noqa: E402

st.set_page_config(page_title="Quant Advisory", page_icon="🧭", layout="wide")

PAGES = Path(__file__).resolve().parent / "pages"
nav = st.navigation({
    "Mapa": [
        st.Page(PAGES / "1_mapa.py", title="Mapa industrial", icon="🗺️", default=True),
        st.Page(PAGES / "2_bottlenecks.py", title="Radar de cuellos de botella", icon="⛔"),
    ],
    "Investigación": [
        st.Page(PAGES / "3_oportunidades.py", title="Radar de oportunidades", icon="🎯"),
        st.Page(PAGES / "4_ficha.py", title="Ficha de empresa", icon="📄"),
        st.Page(PAGES / "5_comparador.py", title="Comparador", icon="⚖️"),
    ],
    "Seguimiento": [
        st.Page(PAGES / "6_portfolio.py", title="Portfolio (simulado)", icon="💼"),
        st.Page(PAGES / "7_feed.py", title="Research Feed", icon="📰"),
        st.Page(PAGES / "8_costos.py", title="Embudo y costos", icon="💲"),
    ],
})

with st.sidebar:
    st.markdown("### 🧭 Quant Advisory")
    st.caption("IA · Energía · Commodities — research sistemático. Solo lectura.")
    if "db" not in st.session_state:
        st.session_state["db"] = "real"
    st.radio("Base de datos", list(data.DB_OPTIONS), key="db", format_func=data.DB_OPTIONS.get)
    p = data.db_path(st.session_state["db"])
    if p is None:   # URL no-SQLite (p.ej. Postgres): el dashboard solo lee SQLite en archivo
        st.error(data.db_error(st.session_state["db"]))
    else:           # ruta absoluta: QA_DATABASE_URL puede apuntar fuera del repo
        st.caption(f"`{p}` · {'✅ existe' if p.exists() else '❌ no existe'}")
    st.divider()
    st.caption("Cartera **simulada**: el sistema nunca envía órdenes reales. Probabilidades de escenarios = "
               "juicios subjetivos, no estadísticas calibradas.")

nav.run()
