"""Embudo de análisis y costos de LLM."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

import data as D  # noqa: E402
from qa.funnel.costs import estimate_cycle  # noqa: E402

st.title("💲 Embudo y costos")
D.banner()

cfg = D.cfg()
fun = D.query("SELECT * FROM funnel_state")
runs = D.query("SELECT * FROM agent_runs")

st.subheader("Embudo")
if fun.empty:
    st.info("Sin estado del embudo: `qa funnel --tiers L0`.")
else:
    order = ["L0", "L1", "L2", "L3"]
    idx = fun["tier_reached"].map(lambda t: order.index(t) if t in order else 0)
    reached = {t: int((idx >= i).sum()) for i, t in enumerate(order)}
    k = st.columns(5)
    k[0].metric("Universo (L0)", len(fun))
    k[1].metric("Pasan filtros L0", int(fun["l0_pass"].fillna(0).astype(bool).sum()))
    for i, t in enumerate(order[1:], start=2):
        k[i].metric(f"Llegaron a {t}", reached.get(t, 0))
    # rampa ordinal (azul, pasos >= 250 sobre superficie clara)
    ramp = ["#86b6ef", "#3987e5", "#256abf", "#184f95"]
    vals = [len(fun)] + [reached.get(t, 0) for t in order[1:]]
    fig = go.Figure(go.Bar(x=vals, y=["L0 screen", "L1 triage", "L2 estándar", "L3 deep dive"], orientation="h",
                           marker=dict(color=ramp, cornerradius=4), text=vals, textposition="outside",
                           hovertemplate="%{y}: %{x} empresas<extra></extra>"))
    fig.update_yaxes(autorange="reversed")
    fig.update_xaxes(range=[0, max(vals) * 1.15])
    fig.update_layout(bargap=0.5)
    st.plotly_chart(D.plotly_layout(fig, height=260, legend=False), width="stretch")
    reasons = fun["l0_reasons"].map(lambda r: D.js(r) or []).explode().dropna()
    if not reasons.empty:
        top = reasons.str.replace(r"\$[\d.,]+M?", "$X", regex=True).value_counts().head(8)
        st.markdown("**Motivos más frecuentes en L0**")
        st.dataframe(top.rename("empresas").to_frame(), width="stretch")
    st.caption("Promoción por valor de la información: prioridad + incertidumbre (L1), score (L2) y siempre las "
               "posiciones en cartera. Análisis reutilizado si el data pack no cambió materialmente.")

st.subheader("Gasto en LLM")
budget = cfg["funnel"]
if runs.empty:
    st.info("Sin llamadas a agentes registradas.")
else:
    runs["created_at"] = pd.to_datetime(runs["created_at"])
    runs["mes"] = runs["created_at"].dt.strftime("%Y-%m")
    this_month = runs[runs["mes"] == pd.Timestamp.now("UTC").strftime("%Y-%m")]["cost_usd"].sum()
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Gasto total", f"${runs['cost_usd'].sum():,.2f}")
    k2.metric("Gasto este mes", f"${this_month:,.2f}", help=f"presupuesto mensual ${budget['monthly_budget_usd']:.0f}")
    k3.metric("Llamadas", len(runs))
    k4.metric("Reutilizadas / salteadas por presupuesto",
              f"{int((runs['status'] == 'reused').sum())} / {int((runs['status'] == 'skipped_budget').sum())}")
    st.progress(min(this_month / budget["monthly_budget_usd"], 1.0),
                text=f"{this_month / budget['monthly_budget_usd']:.0%} del presupuesto mensual "
                     f"(${budget['monthly_budget_usd']:.0f}; por corrida ${budget['per_run_budget_usd']:.0f})")
    by_agent = runs.groupby(["agent", "model"], as_index=False).agg(llamadas=("id", "count"), usd=("cost_usd", "sum"),
                                                                     tokens_in=("input_tokens", "sum"),
                                                                     tokens_out=("output_tokens", "sum"))
    by_agent = by_agent.sort_values("usd")
    fig = go.Figure(go.Bar(y=by_agent["agent"] + " · " + by_agent["model"].str.replace("claude-", ""), x=by_agent["usd"],
                           orientation="h", marker=dict(color=D.SERIES[0], cornerradius=4),
                           text=by_agent["usd"].map(lambda x: f"${x:.3f}"), textposition="outside",
                           hovertemplate="%{y}: $%{x:.4f}<extra></extra>"))
    fig.update_layout(title="Costo por agente", bargap=0.45)
    fig.update_xaxes(range=[0, by_agent["usd"].max() * 1.3 or 1])
    st.plotly_chart(D.plotly_layout(fig, height=max(240, 32 * len(by_agent) + 100), legend=False),
                    width="stretch")
    st.dataframe(runs.groupby(["mes", "status"], as_index=False).agg(llamadas=("id", "count"), usd=("cost_usd", "sum")),
                 hide_index=True, width="stretch", column_config={"usd": st.column_config.NumberColumn(format="$%.4f")})

st.subheader("Estimación de un ciclo completo (cota superior)")
est = estimate_cycle()
st.dataframe(pd.DataFrame(est["rows"]), hide_index=True, width="stretch",
             column_config={"usd_per_call": st.column_config.NumberColumn("USD/llamada", format="$%.4f"),
                            "usd_total": st.column_config.NumberColumn("USD total", format="$%.2f")})
c1, c2 = st.columns(2)
c1.metric("Embudo por capas", f"${est['total_usd_upper_bound']:,.2f} / ciclo")
c2.metric("Sin embudo (todo el universo en profundidad)", f"${est['naive_all_deep_usd']:,.0f} / ciclo")
st.caption(est["note"] + ". Modelos y topes configurables en `config/settings.yaml`.")
