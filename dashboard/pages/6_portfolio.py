"""Cartera SIMULADA: posiciones, NAV, recomendaciones y operaciones."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

import data as D  # noqa: E402
from qa.portfolio.metrics import concentration, performance  # noqa: E402

st.title("💼 Portfolio (simulado)")
D.banner()
st.info("Cartera **SIMULADA** (paper trading). El sistema nunca envía órdenes reales; "
         "`live_trading_enabled` = " + str(D.cfg()["portfolio"].get("live_trading_enabled")) + ".", icon="🧾")

snaps = D.query("SELECT * FROM portfolio_snapshots ORDER BY date")
trades = D.query("SELECT * FROM paper_trades ORDER BY created_at")
recs = D.query("SELECT * FROM recommendations ORDER BY created_at DESC")
names = set(snaps["portfolio"]) | set(trades["portfolio"]) if not (snaps.empty and trades.empty) else set()
default = D.cfg()["portfolio"]["name"]
if not names:
    st.info("La cartera no tiene operaciones todavía: el comité (Portfolio Manager) las genera al final de `qa funnel`.")
else:
    opts = sorted(names)
    pf = st.selectbox("Cartera", opts, index=opts.index(default) if default in opts else 0)
    ps = snaps[snaps["portfolio"] == pf].copy()
    tr = trades[trades["portfolio"] == pf].copy()
    if not ps.empty:
        last = ps.iloc[-1]
        pos = D.js(last["positions"]) or {}
        init = D.cfg()["portfolio"]["initial_capital_usd"]
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("NAV", f"${last['nav']:,.0f}", delta=f"{last['nav'] / init - 1:+.2%} vs capital inicial")
        k2.metric("Caja", f"${last['cash']:,.0f}", help=f"{last['cash'] / last['nav']:.1%} del NAV")
        k3.metric("Posiciones", len(pos))
        conc = concentration({k: v["weight"] for k, v in pos.items()})
        k4.metric("N efectivo (1/HHI)", f"{conc['effective_n']:.1f}" if conc["effective_n"] else "—")
        st.caption(f"Última valuación: {last['date']}")
        if pos:
            pdf = pd.DataFrame([{"símbolo": k, **v} for k, v in pos.items()]).sort_values("weight")
            fig = go.Figure(go.Bar(y=pdf["símbolo"], x=pdf["weight"], orientation="h",
                                   marker=dict(color=D.SERIES[0], cornerradius=4),
                                   text=pdf["weight"].map(lambda x: f"{x:.1%}"), textposition="outside",
                                   hovertemplate="%{y}: %{x:.2%}<extra></extra>"))
            fig.update_layout(title="Pesos", bargap=0.5)
            fig.update_xaxes(tickformat=".0%", range=[0, max(pdf["weight"].max() * 1.3, 0.05)])
            st.plotly_chart(D.plotly_layout(fig, height=max(220, 40 * len(pdf) + 100), legend=False),
                            width="stretch")
            st.dataframe(pdf.sort_values("weight", ascending=False), hide_index=True, width="stretch",
                         column_config={"weight": st.column_config.NumberColumn("peso", format="percent")})
        nav = pd.Series(ps["nav"].values, index=pd.to_datetime(ps["date"]))
        st.subheader("NAV vs benchmarks (base 100)")
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=nav.index, y=100 * nav / nav.iloc[0], mode="lines+markers", name=pf,
                                 line=dict(color=D.SERIES[0], width=2), marker=dict(size=8)))
        metrics = {}
        for i, bsym in enumerate(("SPY", "QQQ")):
            px = D.query("SELECT date, adj_close, close FROM prices WHERE symbol = :s ORDER BY date", s=bsym)
            if px.empty:
                continue
            ser = pd.Series(px["adj_close"].fillna(px["close"]).values, index=pd.to_datetime(px["date"]))
            ser = ser[ser.index >= nav.index[0]]
            if len(ser) > 1:
                fig.add_trace(go.Scatter(x=ser.index, y=100 * ser / ser.iloc[0], mode="lines", name=bsym,
                                         line=dict(color=D.SERIES[1 + i], width=2)))
                metrics[bsym] = performance(nav, ser, rf=D.cfg()["risk_free_rate"])
        st.plotly_chart(D.plotly_layout(fig, height=340), width="stretch")
        if not metrics:
            metrics["(sin benchmark)"] = performance(nav, None, rf=D.cfg()["risk_free_rate"])
            st.caption("Sin precios de SPY/QQQ en la base (`qa ingest --benchmarks`).")
        st.dataframe(pd.DataFrame(metrics).T, width="stretch")
        st.caption("Métricas sin anualizar con menos de 1 año de historia; Sharpe requiere > 20 observaciones. "
                   "Resultados simulados, no operados.")
    st.subheader("Operaciones simuladas")
    if tr.empty:
        st.write("Ninguna.")
    else:
        st.dataframe(tr[["created_at", "symbol", "side", "quantity", "price", "price_source", "price_time", "fees",
                         "slippage", "recommendation_id", "mode"]], hide_index=True, width="stretch")

st.subheader("Registro de recomendaciones")
if recs.empty:
    st.write("Sin recomendaciones.")
else:
    for col in ("catalysts", "risks", "invalidation_triggers", "sources"):
        recs[col] = recs[col].map(lambda x: "; ".join(D.js(x) or []))
    st.dataframe(recs[["created_at", "company_id", "action", "target_weight", "conviction", "price_at_decision",
                       "price_time", "price_source", "data_cutoff", "thesis", "catalysts", "risks",
                       "invalidation_triggers", "mode", "pipeline_run_id"]], hide_index=True, width="stretch",
                 column_config={"target_weight": st.column_config.NumberColumn("peso final", format="percent")})
    st.caption("Cada recomendación guarda precio, hora y fuente disponibles al decidir, y la fecha de corte de la "
               "información (`data_cutoff`) para evitar look-ahead bias.")
