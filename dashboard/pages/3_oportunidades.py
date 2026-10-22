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
comp["label"] = [D.company_label(i, n) for i, n in zip(comp["id"], comp["name"])]
scores = D.latest_scores()          # ya trae ER/P(pérdida) de los escenarios exactos del score (scenario_id)
snaps = D.latest_snapshots()
funnel = D.funnel_state()

f1, f2, f3, f4 = st.columns(4)
with f1:
    layers = st.multiselect("Capa (nodo principal)", sorted(comp["layer"].dropna().unique()),
                            format_func=lambda x: m.layers.get(x, x), placeholder="Todas")
with f2:
    bns = st.multiselect("Cuello de botella", sorted(reg), format_func=lambda x: reg[x].name[:40], placeholder="Todos")
with f3:
    tiers = st.multiselect("Capa del embudo", ["L0", "L1", "L2", "L3"], placeholder="Todas")
with f4:
    elig = st.selectbox("Elegibilidad", ["todas", "elegibles", "bloqueadas por kill switch"])

base = comp.copy()
if layers:
    base = base[base["layer"].isin(layers)]
if bns:
    base = base[base["bottleneck_ids"].map(lambda ids: bool(set(ids) & set(bns)))]

if not scores.empty:
    df = scores.merge(base[["id", "name", "label", "layer", "bottleneck_ids", "status"]], left_on="company_id",
                      right_on="id")
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
    df = df.reset_index(drop=True)

    st.subheader(f"Empresas con score ({len(df)})")
    if df.empty:
        st.info("Ninguna empresa con score para los filtros elegidos.")
    else:
        ts = pd.to_datetime(df["created_at"], utc=True, errors="coerce")
        st.caption(f"Scores calculados entre {D.fmt_dt(ts.min())} y {D.fmt_dt(ts.max())} "
                   "(último score de cada empresa).")
        no_er = df["er36"].isna()
        plot = df[~no_er]
        if plot.empty:
            st.info("Ninguna de estas empresas tiene escenarios asociados a su score: sin retorno esperado no hay "
                    "gráfico. Ver la tabla.")
        else:
            fig = go.Figure()
            mc = plot["market_cap_usd"]
            has_mc = mc.notna().any()
            size = 10 + 30 * np.sqrt(mc.fillna(mc.median()) / mc.max()) if has_mc else pd.Series(16, index=plot.index)
            for ok, name, color in ((True, "elegible", D.SERIES[0]), (False, "bloqueada por kill switch", D.INK["muted"])):
                sub = plot[(plot["eligible"] == 1) == ok]
                if sub.empty:
                    continue
                fig.add_trace(go.Scatter(
                    x=sub["total"], y=sub["er36"], mode="markers+text", name=name, text=sub["company_id"],
                    textposition="top center", textfont=dict(size=11, color=D.INK["secondary"]),
                    marker=dict(size=size[sub.index], color=color, opacity=0.85, line=dict(color="#fcfcfb", width=2)),
                    customdata=np.stack([sub["name"], sub["tier"], sub["confidence"].map(lambda v: D.es(D.LEVEL_ES, v)),
                                         sub["kill_switches"].map(lambda k: "; ".join(k or []) or "—"),
                                         sub["market_cap_usd"].map(lambda v: D.fmt_money(v, "USD")),
                                         sub["prob_loss"].map(lambda v: D.fmt_pct(v).lstrip("+"))], axis=-1),
                    hovertemplate="<b>%{text}</b> %{customdata[0]}<br>score %{x:.2f} · capa %{customdata[1]} · "
                                  "confianza %{customdata[2]}<br>retorno esperado 36m %{y:+.0%} · "
                                  "P(pérdida perm.) %{customdata[5]}<br>market cap %{customdata[4]}"
                                  "<br>kill switches: %{customdata[3]}<extra></extra>"))
            fig.add_hline(y=0, line=dict(color=D.INK["axis"], width=1))
            fig.update_xaxes(title="score total (0-10)", range=[0, 10])
            fig.update_yaxes(title="ER 36m (ponderado)", tickformat="+.0%")
            st.plotly_chart(D.plotly_layout(fig, height=480), width="stretch")
            st.caption(("Tamaño de la burbuja = market cap en USD (ver hover). " if has_mc else
                        "Sin datos de mercado: todas las burbujas del mismo tamaño. ")
                       + "ER 36m = retorno esperado a 36 meses ponderado por probabilidades subjetivas de los "
                         "escenarios; score y ER son salidas del modelo."
                       + (f" {int(no_er.sum())} empresa(s) sin escenarios no se grafican." if no_er.any() else ""))
        show = df.sort_values("total", ascending=False).assign(
            kill_switches=df["kill_switches"].map(lambda k: "; ".join(k or [])),
            alerts=df["alerts"].map(lambda k: "; ".join(k or [])),
            confidence=df["confidence"].map(lambda v: D.es(D.LEVEL_ES, v)),
            eligible=df["eligible"].astype(bool),
            # NumberColumn "%+.0f%%" necesita puntos porcentuales
            **{c: df[c] * 100 for c in ("er12", "er24", "er36", "prob_loss")},
            created_at=pd.to_datetime(df["created_at"], utc=True, errors="coerce"))
        # riesgo primero (P(pérdida), kill switches, alertas) junto al score; nombre largo y total sin topes al final
        order = ["label", "total", "er36", "prob_loss", "kill_switches", "alerts", "eligible", "tier", "confidence",
                 "er12", "er24", "total_uncapped", "created_at"]
        st.dataframe(show[order], hide_index=True, width="stretch", placeholder="—", column_config={
            "label": st.column_config.TextColumn("empresa", pinned=True),
            "total": st.column_config.ProgressColumn("score", min_value=0, max_value=10, format="%.2f"),
            "er36": st.column_config.NumberColumn("ER 36m", format="%+.0f%%", help="'—' = el score no usó escenarios"),
            "prob_loss": st.column_config.NumberColumn("P(pérdida perm.)", format="%.0f%%"),
            "kill_switches": st.column_config.TextColumn("kill switches", width="medium"),
            "alerts": st.column_config.TextColumn("alertas", width="medium"),
            "eligible": st.column_config.CheckboxColumn("elegible"),
            "tier": "capa", "confidence": "confianza",
            "er12": st.column_config.NumberColumn("ER 12m", format="%+.0f%%"),
            "er24": st.column_config.NumberColumn("ER 24m", format="%+.0f%%"),
            "total_uncapped": st.column_config.NumberColumn("score sin topes", format="%.2f"),
            "created_at": st.column_config.DatetimeColumn("fecha del score", format="YYYY-MM-DD HH:mm")})
else:
    st.info("Todavía no hay scores (se generan en L2/L3 del embudo: `qa funnel`). Abajo, el estado del screen L0.")

st.subheader("Estado del embudo (L0: screen cuantitativo sin LLM)")
if funnel.empty:
    st.info("Sin estado de embudo: ejecutá `qa funnel --tiers L0`.")
else:
    fv = funnel.merge(base[["id", "label", "layer"]], left_on="company_id", right_on="id")
    if tiers:
        fv = fv[fv["tier_reached"].isin(tiers)]
    fv = fv.sort_values("priority", ascending=False).assign(
        layer=lambda x: x["layer"].map(D.layer_name),
        l0_pass=lambda x: x["l0_pass"].astype("boolean"),
        l0_reasons=lambda x: x["l0_reasons"].map(lambda r: "; ".join(r or [])),
        last_score=lambda x: pd.to_numeric(x["last_score"], errors="coerce"),
        last_updated=lambda x: pd.to_datetime(x["last_updated"], utc=True, errors="coerce"))
    st.dataframe(fv[["label", "layer", "tier_reached", "l0_pass", "priority", "last_score", "l0_reasons",
                     "last_updated"]], hide_index=True, width="stretch", placeholder="—",
                 column_config={
                     "label": st.column_config.TextColumn("empresa", pinned=True),
                     "layer": st.column_config.TextColumn("capa del mapa", width="large"),
                     "tier_reached": "capa alcanzada",
                     "l0_pass": st.column_config.CheckboxColumn("pasa L0"),
                     "priority": st.column_config.NumberColumn("pre-score / prioridad", format="%.2f"),
                     "last_score": st.column_config.NumberColumn("último score", format="%.2f",
                                                                 help="vacío = todavía sin score (L2/L3)"),
                     "l0_reasons": st.column_config.TextColumn("motivos L0", width="large"),
                     "last_updated": st.column_config.DatetimeColumn("actualizado", format="YYYY-MM-DD HH:mm")})
    st.caption("pre-score L0 = 0.40·bottleneck + 0.20·exposición (prior) + 0.20·calidad financiera + 0.20·valuación "
               "relativa. Sin datos de mercado solo pasan semillas y posiciones en cartera.")
