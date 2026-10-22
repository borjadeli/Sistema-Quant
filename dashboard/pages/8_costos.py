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
fun = D.funnel_state()
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
    fig.update_xaxes(range=[0, max(vals) * 1.15], dtick=1 if max(vals) <= 12 else None, title="empresas")
    fig.update_layout(bargap=0.5)
    st.plotly_chart(D.plotly_layout(fig, height=260, legend=False), width="stretch")
    reasons = fun["l0_reasons"].explode().dropna()
    if not reasons.empty:
        top = reasons.str.replace(r"\$[\d.,]+M?", "$X", regex=True).value_counts().head(8)
        st.markdown("**Motivos más frecuentes en L0**")
        st.dataframe(top.rename_axis("motivo").rename("empresas").reset_index(), hide_index=True, width="stretch", placeholder="—",
                     column_config={"motivo": st.column_config.TextColumn("motivo", width="large")})
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
    # status 'reused' = análisis reutilizado (el data pack no cambió): no hubo llamada al LLM ni costo.
    reused = runs[runs["status"] == "reused"]
    llm_calls = runs[~runs["status"].isin(["reused", "skipped_budget", "dry_run"])]
    saved = 0.0
    if not reused.empty and "reused_from" in runs:
        cost_by_id = runs.set_index("id")["cost_usd"]
        saved = float(pd.to_numeric(reused["reused_from"], errors="coerce").map(cost_by_id).fillna(0).sum())
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Gasto total", f"${runs['cost_usd'].sum():,.2f}")
    k2.metric("Gasto este mes", f"${this_month:,.2f}", help=f"presupuesto mensual ${budget['monthly_budget_usd']:.0f}")
    k3.metric("Llamadas al LLM", len(llm_calls), help="corridas de agentes que llamaron al modelo (ok, error o "
                                                      "rechazo); excluye reutilizadas y salteadas")
    k4.metric("Reutilizadas", len(reused),
              help="análisis reutilizados porque el data pack no cambió materialmente (sin llamada al LLM)."
                   + (f" Costo evitado ≈ ${saved:,.2f} (costo de las corridas originales)." if len(reused) else ""))
    k5.metric("Salteadas por presupuesto", int((runs["status"] == "skipped_budget").sum()))
    st.progress(min(this_month / budget["monthly_budget_usd"], 1.0),
                text=f"{this_month / budget['monthly_budget_usd']:.0%} del presupuesto mensual "   # \$: no es LaTeX
                     f"(\\${budget['monthly_budget_usd']:.0f}; por corrida \\${budget['per_run_budget_usd']:.0f})")
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
    by_status = runs.groupby(["mes", "status"], as_index=False).agg(corridas=("id", "count"), usd=("cost_usd", "sum"))
    by_status["status"] = by_status["status"].map(lambda v: D.es(D.RUN_STATUS_ES, v))
    st.dataframe(by_status, hide_index=True, width="stretch", placeholder="—",
                 column_config={"status": "estado", "usd": st.column_config.NumberColumn("costo (USD)", format="$%.4f")})

st.subheader("Estimación de un ciclo completo (capas llenas, sin reutilización)")
est = estimate_cycle()
est_df = pd.DataFrame(est["rows"])
st.dataframe(est_df, hide_index=True, width="stretch", placeholder="—",
             column_config=D.colcfg(est_df, usd_per_call=st.column_config.NumberColumn("USD/llamada", format="$%.4f"),
                                    usd_total=st.column_config.NumberColumn("USD total", format="$%.2f")))
c1, c2, c3 = st.columns(3)
c1.metric("Embudo por capas", f"${est['total_usd_upper_bound']:,.2f} / ciclo")
c2.metric("Presupuesto por corrida necesario", f"${est['budget_needed_usd']:,.2f}",
          help="El control previo a cada llamada exige la cota superior de esa solicitud (todo max_tokens y todas "
               "las búsquedas), no su costo esperado.")
c3.metric("Sin embudo (todo el universo en profundidad)", f"${est['naive_all_deep_usd']:,.0f} / ciclo")
st.caption(est["note"] + ". Modelos y topes configurables en `config/settings.yaml`.")
