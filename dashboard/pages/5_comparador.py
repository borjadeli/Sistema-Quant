"""Comparador de empresas dentro de un mismo cuello de botella."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

import data as D  # noqa: E402
from qa.db import repo  # noqa: E402
from qa.fundamentals.metrics import compute_fundamentals  # noqa: E402

st.title("⚖️ Comparador")
D.banner()

reg = D.bottleneck_registry()
comp = D.companies()
if comp.empty:
    st.info("Sin empresas en la base.")
    st.stop()
ids = comp["id"].tolist()
names = dict(zip(comp["id"], comp["name"]))
comp["bn"] = comp["nodes"].map(lambda ns: [b.id for b in reg.values() if set(ns) & set(b.map_nodes)])

bn_opts = ["(libre)"] + sorted(reg, key=lambda x: -reg[x].severity)
c1, c2 = st.columns([1, 2])
with c1:
    bsel = st.selectbox("Cuello de botella", bn_opts, index=1 if len(bn_opts) > 1 else 0,
                        format_func=lambda x: x if x == "(libre)" else f"{reg[x].name[:45]} ({reg[x].severity})")
pool = ids if bsel == "(libre)" else comp.loc[comp["bn"].map(lambda b: bsel in b), "id"].tolist()
scored = set(D.latest_scores()["company_id"]) if not D.latest_scores().empty else set()
default = [x for x in pool if x in scored][:5] or pool[:5]
with c2:
    sel = st.multiselect("Empresas", ids if bsel == "(libre)" else pool, default=default,
                         format_func=lambda x: f"{x} — {names.get(x, '')}")
if not sel:
    st.info("Elegí al menos una empresa.")
    st.stop()


@st.cache_data(ttl=60, show_spinner=False)
def fund(url: str, cid: str) -> dict:
    with Session(D._engine(url)) as s:
        pts = repo.load_datapoints(s, cid)
    if not pts:
        return {}
    f = compute_fundamentals(cid, pts)
    keys = ["revenue_ttm", "revenue_last_q", "revenue_growth_yoy", "gross_margin", "ebit_margin", "fcf_margin",
            "roic", "net_debt", "net_debt_to_ebitda", "dilution_yoy", "cash_runway_quarters"]
    return {k: f.v(k) for k in keys} | {"data_quality": f.data_quality, "currency": f.currency}


url = D.db_url(D.current_db())
scores = D.latest_scores()
scen = D.latest_scenarios()
snaps = D.latest_snapshots()
w = D.cfg()["scoring"]["weights"]
rows = []
for cid in sel:
    r = {"empresa": cid, "nombre": names.get(cid)}
    r |= fund(url, cid)
    if not snaps.empty and cid in set(snaps["company_id"]):
        s0 = snaps[snaps["company_id"] == cid].iloc[-1]
        r |= {"market_cap_usd": s0["market_cap_usd"], "adv_usd_20d": s0["adv_usd_20d"]}
    if not scores.empty and cid in set(scores["company_id"]):
        s0 = scores[scores["company_id"] == cid].iloc[-1]
        r |= {"score": s0["total"], "tier": s0["tier"], "elegible": bool(s0["eligible"]), "confianza": s0["confidence"]}
        r |= {f"f:{k}": ((s0["factors"] or {}).get(k) or {}).get("score") for k in w}
    if not scen.empty and cid in set(scen["company_id"]):
        s0 = scen[scen["company_id"] == cid].iloc[-1]
        r |= {"ER 12m": s0["er12"], "ER 24m": s0["er24"], "ER 36m": s0["er36"], "P(pérdida perm.)": s0["prob_loss"]}
    rows.append(r)
df = pd.DataFrame(rows).set_index("empresa")

pct_rows = {"revenue_growth_yoy", "gross_margin", "ebit_margin", "fcf_margin", "roic", "dilution_yoy", "ER 12m",
            "ER 24m", "ER 36m", "P(pérdida perm.)"}
money_rows = {"revenue_ttm", "revenue_last_q", "net_debt", "market_cap_usd", "adv_usd_20d"}


def fmt(k: str, v):  # noqa: ANN001, ANN202
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    if k in pct_rows:
        return f"{v:+.1%}" if k.startswith("ER") else f"{v:.1%}"
    if k in money_rows:
        return D.fmt_money(v)
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


st.subheader("Métricas lado a lado")
t = df.T
st.dataframe(pd.DataFrame({c: [fmt(k, v) for k, v in t[c].items()] for c in t.columns}, index=t.index),
             width="stretch")
st.caption("Fundamentales calculados desde datapoints con trazabilidad; '—' = dato faltante (no se rellena). "
           "Revisar `data_quality`: search_summary / synthetic no son datos verificados.")

fcols = [c for c in df.columns if c.startswith("f:")]
if fcols:
    st.subheader("Puntaje por factor (0-10)")
    z = df[fcols].astype(float)
    fig = go.Figure(go.Heatmap(z=z.values, x=[c[2:] for c in fcols], y=z.index, zmin=0, zmax=10,
                               colorscale=[[0, "#cde2fb"], [0.5, "#3987e5"], [1, "#0d366b"]],
                               text=z.round(1).values, texttemplate="%{text}", xgap=2, ygap=2,
                               colorbar=dict(title="puntaje"),
                               hovertemplate="%{y} · %{x}: %{z:.1f}<extra></extra>"))
    fig.update_yaxes(autorange="reversed")
    st.plotly_chart(D.plotly_layout(fig, height=120 + 45 * len(z), legend=False), width="stretch")
else:
    st.caption("Sin scores para las empresas elegidas (se calculan en L2/L3 del embudo).")
