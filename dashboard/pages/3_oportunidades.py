"""Radar de oportunidades: score vs retorno esperado, filtros por capa / bottleneck / capa del embudo."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

import data as D  # noqa: E402

st.title("🎯 Radar de oportunidades")
D.banner()

m = D.industry_map()
reg = D.bottleneck_registry()
comp = D.companies()
if comp.empty:
    st.info("Sin empresas en la base. Ejecutá `qa init`.")
    st.stop()

comp["layer"] = comp["primary_layer"]
comp["bottleneck_ids"] = comp["nodes"].map(lambda ns: [b.id for b in reg.values() if set(ns) & set(b.map_nodes)])
scores = D.latest_scores()
scen = D.latest_scenarios()
snaps = D.latest_snapshots()
funnel = D.query("SELECT * FROM funnel_state")

f1, f2, f3, f4 = st.columns(4)
with f1:
    layers = st.multiselect("Capa (nodo principal)", sorted(comp["layer"].dropna().unique()),
                            format_func=lambda x: m.layers.get(x, x))
with f2:
    bns = st.multiselect("Cuello de botella", sorted(reg), format_func=lambda x: reg[x].name[:40])
with f3:
    tiers = st.multiselect("Capa del embudo", ["L0", "L1", "L2", "L3"])
with f4:
    elig = st.selectbox("Elegibilidad", ["todas", "elegibles", "bloqueadas por kill switch"])

base = comp.copy()
if layers:
    base = base[base["layer"].isin(layers)]
if bns:
    base = base[base["bottleneck_ids"].map(lambda ids: bool(set(ids) & set(bns)))]

if not scores.empty:
    df = scores.merge(base[["id", "name", "layer", "bottleneck_ids", "status"]], left_on="company_id", right_on="id")
    if not scen.empty:
        df = df.merge(scen[["company_id", "er12", "er24", "er36", "prob_loss", "price_at_creation"]], on="company_id",
                      how="left")
    else:
        df = df.assign(er12=np.nan, er24=np.nan, er36=np.nan, prob_loss=np.nan, price_at_creation=np.nan)
    if not snaps.empty:
        df = df.merge(snaps[["company_id", "market_cap_usd", "adv_usd_20d"]], on="company_id", how="left")
    else:
        df = df.assign(market_cap_usd=np.nan, adv_usd_20d=np.nan)
    if tiers:
        df = df[df["tier"].isin(tiers)]
    if elig == "elegibles":
        df = df[df["eligible"] == 1]
    elif elig != "todas":
        df = df[df["eligible"] == 0]

    st.subheader(f"Empresas con score ({len(df)})")
    if df.empty:
        st.info("Ninguna empresa con score para los filtros elegidos.")
    else:
        fig = go.Figure()
        mc = df["market_cap_usd"].fillna(df["market_cap_usd"].median() if df["market_cap_usd"].notna().any() else 1e9)
        size = 10 + 30 * np.sqrt(mc / mc.max())
        for ok, name, color in ((True, "elegible", D.SERIES[0]), (False, "bloqueada por kill switch", D.INK["muted"])):
            sub = df[(df["eligible"] == 1) == ok]
            if sub.empty:
                continue
            fig.add_trace(go.Scatter(
                x=sub["total"], y=sub["er36"], mode="markers+text", name=name, text=sub["company_id"],
                textposition="top center", textfont=dict(size=11, color=D.INK["secondary"]),
                marker=dict(size=size[sub.index], color=color, opacity=0.85, line=dict(color="#fcfcfb", width=2)),
                customdata=np.stack([sub["name"], sub["tier"], sub["confidence"],
                                     sub["kill_switches"].map(lambda k: "; ".join(k or []) or "—")], axis=-1),
                hovertemplate="<b>%{text}</b> %{customdata[0]}<br>score %{x:.2f} · capa %{customdata[1]} · "
                              "confianza %{customdata[2]}<br>retorno esperado 36m %{y:+.0%}<br>kill: %{customdata[3]}"
                              "<extra></extra>"))
        fig.add_hline(y=0, line=dict(color=D.INK["axis"], width=1))
        fig.update_xaxes(title="score total (0-10)", range=[0, 10])
        fig.update_yaxes(title="retorno esperado 36m (ponderado, probabilidades subjetivas)", tickformat="+.0%")
        st.plotly_chart(D.plotly_layout(fig, height=480), width="stretch")
        st.caption("Tamaño = market cap (si hay datos de mercado). Score y retorno esperado son salidas del modelo; "
                   "las probabilidades de los escenarios son juicios subjetivos.")
        show = df[["company_id", "name", "tier", "total", "total_uncapped", "eligible", "confidence", "er12", "er24",
                   "er36", "prob_loss", "kill_switches", "alerts", "created_at"]].sort_values("total", ascending=False)
        show["kill_switches"] = show["kill_switches"].map(lambda k: "; ".join(k or []))
        show["alerts"] = show["alerts"].map(lambda k: "; ".join(k or []))
        st.dataframe(show, hide_index=True, width="stretch", column_config={
            "total": st.column_config.ProgressColumn("score", min_value=0, max_value=10, format="%.2f"),
            "er12": st.column_config.NumberColumn("ER 12m", format="percent"),
            "er24": st.column_config.NumberColumn("ER 24m", format="percent"),
            "er36": st.column_config.NumberColumn("ER 36m", format="percent"),
            "prob_loss": st.column_config.NumberColumn("P(pérdida perm.)", format="percent"),
            "eligible": st.column_config.CheckboxColumn("elegible")})
else:
    st.info("Todavía no hay scores (se generan en L2/L3 del embudo: `qa funnel`). Abajo, el estado del screen L0.")

st.subheader("Estado del embudo (L0: screen cuantitativo sin LLM)")
if funnel.empty:
    st.caption("Sin estado de embudo: ejecutá `qa funnel --tiers L0`.")
else:
    funnel["l0_reasons"] = funnel["l0_reasons"].map(lambda r: "; ".join(D.js(r) or []))
    fv = funnel.merge(base[["id", "name", "layer"]], left_on="company_id", right_on="id")
    if tiers:
        fv = fv[fv["tier_reached"].isin(tiers)]
    fv = fv.sort_values("priority", ascending=False)
    st.dataframe(fv[["company_id", "name", "layer", "tier_reached", "l0_pass", "priority", "last_score", "l0_reasons",
                     "last_updated"]], hide_index=True, width="stretch",
                 column_config={"priority": st.column_config.NumberColumn("pre-score / prioridad", format="%.2f"),
                                "l0_pass": st.column_config.CheckboxColumn("pasa L0"),
                                "layer": st.column_config.TextColumn("capa")})
    st.caption("pre-score L0 = 0.40·bottleneck + 0.20·exposición (prior) + 0.20·calidad financiera + 0.20·valuación "
               "relativa. Sin datos de mercado solo pasan semillas y posiciones en cartera.")
