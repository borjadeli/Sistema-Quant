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

comp = D.companies()
names = dict(zip(comp["id"], comp["name"])) if not comp.empty else {}
snaps = D.query("SELECT * FROM portfolio_snapshots ORDER BY date")
trades = D.query("SELECT * FROM paper_trades ORDER BY created_at")
recs = D.query("SELECT * FROM recommendations ORDER BY created_at DESC, id DESC")
pf_names = (set(snaps["portfolio"]) if not snaps.empty else set()) | (set(trades["portfolio"]) if not trades.empty else set())
default = D.cfg()["portfolio"]["name"]

PERF_ROWS = [("start", "desde", "txt"), ("end", "hasta", "txt"), ("n_obs", "observaciones", "int"),
             ("total_return", "retorno total", "pct"), ("cagr", "CAGR", "pct"), ("volatility", "volatilidad anual", "pct"),
             ("sharpe", "Sharpe", "num"), ("max_drawdown", "máximo drawdown", "pct"),
             ("bench_total_return", "retorno del benchmark", "pct"), ("excess_return", "exceso de retorno", "pct"),
             ("beta", "beta", "num"), ("alpha_annual", "alpha anual (CAPM)", "pct"),
             ("tracking_error", "tracking error", "pct"), ("information_ratio", "information ratio", "num"),
             ("note", "nota", "txt")]


def fmt_perf(kind: str, v) -> str:  # noqa: ANN001
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    if kind == "pct":
        return f"{v:+.2%}"
    if kind == "num":
        return f"{v:.2f}"
    if kind == "int":
        return f"{int(v)}"
    return str(v)[:10] if kind == "txt" and str(v)[:4].isdigit() else str(v)


if not pf_names:
    st.info("La cartera no tiene operaciones todavía: el comité (Portfolio Manager) las genera al final de `qa funnel`.")
else:
    opts = sorted(pf_names)
    pf = st.selectbox("Cartera", opts, index=opts.index(default) if default in opts else 0)
    ps = snaps[snaps["portfolio"] == pf].copy() if not snaps.empty else snaps
    tr = trades[trades["portfolio"] == pf].copy() if not trades.empty else trades
    if not ps.empty:
        last = ps.iloc[-1]
        pos = D.js(last["positions"]) or {}
        init = D.cfg()["portfolio"]["initial_capital_usd"]
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("NAV", f"${last['nav']:,.0f}", delta=f"{last['nav'] / init - 1:+.2%}",
                  help=f"Variación vs capital inicial (${init:,.0f}). Valuación al {last['date']}.")
        k2.metric("Caja", f"${last['cash']:,.0f}", delta=f"{last['cash'] / last['nav']:.1%} del NAV",
                  delta_color="off", delta_arrow="off", help="Caja no invertida (incluida en el NAV).")
        k3.metric("Posiciones", len(pos))
        conc = concentration({k: v["weight"] for k, v in pos.items()})
        k4.metric("N efectivo (1/HHI)", f"{conc['effective_n']:.1f}" if conc["effective_n"] else "—",
                  help="Número efectivo de posiciones: 1 / Σ peso². Excluye la caja.")
        st.caption(f"Última valuación: {last['date']}")
        cash_w = last["cash"] / last["nav"] if last["nav"] else 0.0
        pdf = pd.DataFrame([{"símbolo": k, **v} for k, v in pos.items()])
        if not pdf.empty:
            pdf = pdf.sort_values("weight")
        # pesos: posiciones (azul) + caja (gris neutro) para que el gráfico sume el 100% del NAV
        bars = pd.DataFrame({"y": ["Caja"] + (pdf["símbolo"].tolist() if not pdf.empty else []),
                             "x": [cash_w] + (pdf["weight"].tolist() if not pdf.empty else []),
                             "c": [D.INK["neutral"]] + [D.SERIES[0]] * len(pdf)})
        fig = go.Figure(go.Bar(y=bars["y"], x=bars["x"], orientation="h",
                               marker=dict(color=bars["c"], cornerradius=4),
                               text=bars["x"].map(lambda x: f"{x:.1%}"), textposition="outside",
                               textfont=dict(color=D.INK["secondary"]),
                               hovertemplate="%{y}: %{x:.2%} del NAV<extra></extra>"))
        fig.update_layout(title="Pesos sobre el NAV (posiciones y caja)", bargap=0.5)
        fig.update_xaxes(tickformat=".0%", range=[0, max(bars["x"].max() * 1.18, 0.05)])
        fig.update_yaxes(categoryorder="array", categoryarray=bars["y"].tolist())
        st.plotly_chart(D.plotly_layout(fig, height=max(200, 40 * len(bars) + 100), legend=False), width="stretch")
        if not pdf.empty:
            pt = pdf.sort_values("weight", ascending=False)
            pt = pt.assign(name=pt["símbolo"].map(names.get),
                           price_time=pd.to_datetime(pt["price_time"], utc=True, errors="coerce")
                           if "price_time" in pt else pd.to_datetime(last["date"], utc=True))
            cols = [c for c in ("símbolo", "name", "qty", "price", "value", "weight", "price_time", "price_source")
                    if c in pt]
            st.dataframe(pt[cols], hide_index=True, width="stretch", placeholder="—", column_config=D.colcfg(
                pt, qty=st.column_config.NumberColumn("cantidad", format="%.2f"),
                price=st.column_config.NumberColumn("precio (USD)", format="dollar"),
                value=st.column_config.NumberColumn("valor (USD)", format="dollar"),
                weight=st.column_config.NumberColumn("peso", format="percent"),
                price_time=st.column_config.DatetimeColumn("precio al", format="YYYY-MM-DD HH:mm")))
            st.caption("Precio de valuación del snapshot; 'precio al' = hora del precio (o fecha del snapshot si el "
                       "precio no trae hora).")
        nav = pd.Series(ps["nav"].values, index=pd.to_datetime(ps["date"]))
        st.subheader("NAV vs benchmarks (base 100)")
        metrics = {}
        if len(nav) < 2:
            st.info(f"Solo hay un snapshot de NAV ({last['date']}): la curva se dibuja a partir de la segunda "
                    "valuación (cada corrida de `qa funnel` en un día distinto agrega una).")
        else:
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=nav.index, y=100 * nav / nav.iloc[0], mode="lines+markers", name=pf,
                                     line=dict(color=D.SERIES[0], width=2), marker=dict(size=8),
                                     hovertemplate="%{x|%Y-%m-%d}: %{y:.1f}<extra>" + pf + "</extra>"))
            for i, bsym in enumerate(("SPY", "QQQ")):
                px = D.query("SELECT date, adj_close, close FROM prices WHERE symbol = :s ORDER BY date", s=bsym)
                if px.empty:
                    continue
                ser = pd.Series(px["adj_close"].fillna(px["close"]).values, index=pd.to_datetime(px["date"]))
                ser = ser[ser.index >= nav.index[0]]
                if len(ser) > 1:
                    fig.add_trace(go.Scatter(x=ser.index, y=100 * ser / ser.iloc[0], mode="lines", name=bsym,
                                             line=dict(color=D.SERIES[1 + i], width=2),
                                             hovertemplate="%{x|%Y-%m-%d}: %{y:.1f}<extra>" + bsym + "</extra>"))
                    metrics[bsym] = performance(nav, ser, rf=D.cfg()["risk_free_rate"])
            fig.update_xaxes(type="date", tickformat="%d %b %Y")
            fig.update_yaxes(title="base 100")
            st.plotly_chart(D.plotly_layout(fig, height=340), width="stretch")
        if not metrics:
            metrics["(sin benchmark)"] = performance(nav, None, rf=D.cfg()["risk_free_rate"])
            if len(nav) >= 2:
                st.caption("Sin precios de SPY/QQQ en la base (`qa ingest --benchmarks`).")
        perf = pd.DataFrame({b: [fmt_perf(kind, mt.get(k)) for k, _, kind in PERF_ROWS] for b, mt in metrics.items()},
                            index=[lab for _, lab, _ in PERF_ROWS])
        perf = perf[~(perf == "—").all(axis=1)]
        st.dataframe(perf, width="stretch", placeholder="—", height=D.table_height(len(perf)))
        st.caption("Métricas sin anualizar con menos de 1 año de historia; Sharpe requiere > 20 observaciones. "
                   "Resultados simulados, no operados.")
    st.subheader("Operaciones simuladas")
    if tr.empty:
        st.write("Ninguna.")
    else:
        tv = tr.assign(side=tr["side"].map(lambda v: D.es(D.SIDE_ES, v)), mode=tr["mode"].map(lambda v: D.es(D.MODE_ES, v)),
                       notional=tr["quantity"] * tr["price"],
                       created_at=pd.to_datetime(tr["created_at"], utc=True, errors="coerce"),
                       price_time=pd.to_datetime(tr["price_time"], utc=True, errors="coerce"),
                       name=tr["symbol"].map(names.get))
        tv = tv.sort_values("created_at", ascending=False)
        st.dataframe(tv[["symbol", "name", "side", "quantity", "price", "notional", "fees", "slippage", "created_at",
                         "price_time", "price_source", "recommendation_id", "mode"]], hide_index=True, width="stretch", placeholder="—",
                     column_config=D.colcfg(
                         tv, symbol=st.column_config.TextColumn("símbolo", pinned=True),
                         quantity=st.column_config.NumberColumn("cantidad", format="%.2f"),
                         price=st.column_config.NumberColumn("precio (USD)", format="dollar"),
                         notional=st.column_config.NumberColumn("monto (USD)", format="dollar"),
                         fees=st.column_config.NumberColumn("comisiones (USD)", format="dollar"),
                         slippage=st.column_config.NumberColumn("slippage (USD)", format="dollar",
                                                                help="costo estimado de slippage: |precio ejecutado − "
                                                                     "precio de referencia| × cantidad"),
                         created_at=st.column_config.DatetimeColumn("fecha", format="YYYY-MM-DD HH:mm"),
                         price_time=st.column_config.DatetimeColumn("hora del precio", format="YYYY-MM-DD HH:mm"),
                         recommendation_id=st.column_config.NumberColumn("recomendación #", format="%d")))

st.subheader("Registro de recomendaciones")
if recs.empty:
    st.write("Sin recomendaciones.")
else:
    for col in ("catalysts", "risks", "invalidation_triggers", "sources", "constraint_notes"):
        if col in recs:
            recs[col] = recs[col].map(lambda x: D.js(x) or [])
        else:
            recs[col] = [[] for _ in range(len(recs))]
    for col in ("pm_action", "pm_target_weight"):
        if col not in recs:
            recs[col] = None
    # acción final (ejecutada, tras reglas de cartera) vs lo que propuso el Portfolio Manager
    changed = recs.apply(lambda r: (pd.notna(r["pm_action"]) and r["pm_action"] != r["action"])
                         or (pd.notna(r["pm_target_weight"]) and abs(r["pm_target_weight"] - r["target_weight"]) > 1e-6)
                         or bool(r["constraint_notes"]), axis=1)
    rv = recs.assign(
        label=[D.company_label(c, names.get(c)) for c in recs["company_id"]],
        action=recs["action"].map(lambda v: D.es(D.ACTION_ES, v)),
        pm_action=recs["pm_action"].map(lambda v: D.es(D.ACTION_ES, v)),
        conviction=recs["conviction"].map(lambda v: D.es(D.LEVEL_ES, v)),
        changed=changed,
        constraint_txt=recs["constraint_notes"].map(lambda x: "; ".join(map(str, x))),
        created_at=pd.to_datetime(recs["created_at"], utc=True, errors="coerce"),
        price_time=pd.to_datetime(recs["price_time"], utc=True, errors="coerce"),
        mode=recs["mode"].map(lambda v: D.es(D.MODE_ES, v)))
    has_pm = recs["pm_action"].notna().any() or recs["pm_target_weight"].notna().any()
    if changed.any():
        st.warning(f"{int(changed.sum())} recomendación(es) con la acción o el peso **ajustados por las reglas de "
                   "cartera** respecto de la propuesta del Portfolio Manager (ver columna «ajuste por reglas» y el "
                   "detalle).", icon="🔧")
    cols = ["label", "action", "target_weight", "conviction", "thesis"]
    cols += (["pm_action", "pm_target_weight"] if has_pm else []) + (["constraint_txt"] if changed.any() else [])
    cols += ["created_at", "price_at_decision", "price_time", "price_source", "data_cutoff", "mode", "pipeline_run_id"]
    st.dataframe(rv[cols], hide_index=True, width="stretch", placeholder="—", column_config=D.colcfg(
        rv, label=st.column_config.TextColumn("empresa", pinned=True),
        action=st.column_config.TextColumn("acción final", help="acción ejecutada tras aplicar las reglas de cartera"),
        target_weight=st.column_config.NumberColumn("peso final", format="percent"),
        conviction="convicción",
        thesis=st.column_config.TextColumn("tesis", width="large"),
        pm_action=st.column_config.TextColumn("propuesta PM", help="acción propuesta por el agente Portfolio Manager"),
        pm_target_weight=st.column_config.NumberColumn("peso propuesto PM", format="percent"),
        constraint_txt=st.column_config.TextColumn("ajuste por reglas", width="medium"),
        created_at=st.column_config.DatetimeColumn("fecha", format="YYYY-MM-DD HH:mm"),
        price_at_decision=st.column_config.NumberColumn("precio al decidir", format="%.2f"),
        price_time=st.column_config.DatetimeColumn("hora del precio", format="YYYY-MM-DD HH:mm"),
        data_cutoff="corte de información"))
    st.caption("Cada recomendación guarda precio, hora y fuente disponibles al decidir, y la fecha de corte de la "
               "información (`data_cutoff`) para evitar look-ahead bias.")
    st.markdown("**Detalle: tesis, catalizadores, riesgos y gatillos de invalidación**")
    for _, r in rv.head(30).iterrows():
        title = (f"{r['label']} · {r['action']} · peso {r['target_weight']:.1%} · {D.fmt_dt(r['created_at'], False)}"
                 + (" · 🔧 ajustada" if r["changed"] else ""))
        with st.expander(title):
            st.markdown(f"**Tesis:** {r['thesis'] or '—'}")
            if has_pm:
                pmw = r["pm_target_weight"]
                st.markdown(f"**Propuesta del PM:** {r['pm_action'] or '—'}"
                            + (f" · peso {pmw:.1%}" if pd.notna(pmw) else "")
                            + f" → **final:** {r['action']} · peso {r['target_weight']:.1%}")
            for k, lab in (("constraint_notes", "Ajustes por reglas de cartera"), ("catalysts", "Catalizadores"),
                           ("risks", "Riesgos"), ("invalidation_triggers", "Gatillos de invalidación")):
                if r[k]:
                    st.markdown(f"**{lab}**\n" + "\n".join(f"- {x}" for x in r[k]))
            if r["sources"]:
                st.caption("Fuentes: " + " · ".join(map(str, r["sources"][:12])))
