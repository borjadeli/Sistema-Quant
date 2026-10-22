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
pools = {b: comp.loc[comp["bn"].map(lambda x, b=b: b in x), "id"].tolist() for b in reg}

FREE = "(libre)"
bn_opts = [FREE] + sorted(reg, key=lambda x: -reg[x].severity)
# por defecto: el bottleneck más severo que tenga empresas en la base; si ninguno, comparación libre
first = next((i for i, b in enumerate(bn_opts) if b != FREE and pools[b]), 0)
c1, c2 = st.columns([2, 3])
with c1:
    bsel = st.selectbox("Cuello de botella", bn_opts, index=first, key=f"cmp_bn_{D.current_db()}",
                        format_func=lambda x: "(libre) — cualquier empresa" if x == FREE else
                        f"{reg[x].name[:45]} · sev. {reg[x].severity} · {len(pools[x])} emp.")
pool = ids if bsel == FREE else pools[bsel]
if not pool:
    st.info(f"Ninguna empresa de la base seleccionada está expuesta a **{reg[bsel].name}**. Elegí otro cuello "
            "de botella o **(libre)** para comparar cualquier empresa.")
    st.stop()
scores = D.latest_scores()
scored = set(scores["company_id"]) if not scores.empty else set()
default = [x for x in pool if x in scored][:5] or pool[:5]
with c2:
    sel = st.multiselect("Empresas", pool, default=default, placeholder="Elegí empresas para comparar",
                         format_func=lambda x: D.company_label(x, names.get(x)))
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
    rev = f.metrics.get("revenue_ttm") or f.metrics.get("revenue_last_q")
    period = "; ".join(x for x in ((rev.period if rev else None),
                                   f"balance {f.balance_date}" if f.balance_date else None) if x)
    return {k: f.v(k) for k in keys} | {"data_quality": f.data_quality, "currency": f.currency,
                                        "fund_period": period or None}


url = D.db_url(D.current_db())
snaps = D.latest_snapshots()
funnel = D.funnel_state()
fun_by = {r["company_id"]: r for r in funnel.to_dict("records")} if not funnel.empty else {}
w = D.cfg()["scoring"]["weights"]
QUALITY = {"filing": "✅ documentos regulatorios", "search_summary": "🔎 resumen de buscador (no verificado)",
           "synthetic": "🧪 sintético", "mixed": "mixto", "none": "sin datos"}
rows = []
for cid in sel:
    r = {"empresa": cid}
    r |= fund(url, cid)
    if not snaps.empty and cid in set(snaps["company_id"]):
        s0 = snaps[snaps["company_id"] == cid].iloc[-1]
        r |= {"price": s0["price"], "price_ccy": s0["currency"], "market_cap_usd": s0["market_cap_usd"],
              "adv_usd_20d": s0["adv_usd_20d"], "price_asof": f"{D.fmt_dt(s0['as_of'], False)} · {s0['source']}"}
    r["funnel"] = D.funnel_explain(fun_by.get(cid))
    if cid in scored:
        s0 = scores[scores["company_id"] == cid].iloc[-1]
        r |= {"score": s0["total"], "tier": s0["tier"], "eligible": bool(s0["eligible"]),
              "confidence": D.es(D.LEVEL_ES, s0["confidence"]), "score_date": D.fmt_dt(s0["created_at"], False),
              "er12": s0["er12"], "er24": s0["er24"], "er36": s0["er36"], "prob_loss": s0["prob_loss"],
              "scenario": (f"#{int(s0['scenario_id'])} · {D.fmt_dt(s0['scenario_created_at'], False)}"
                           if pd.notna(s0["scenario_id"]) else "sin escenarios (ER no aplica)")}
        r |= {f"f:{k}": ((s0["factors"] or {}).get(k) or {}).get("score") for k in w}
    rows.append(r)
df = pd.DataFrame(rows).set_index("empresa")

# (clave, etiqueta, formato) por sección. Formatos: pct, pct+ (con signo), money (moneda de reporte),
# usd, num, bool, txt.
SECTIONS = {
    "Fundamentales": [
        ("fund_period", "período de los datos", "txt"), ("data_quality", "calidad de datos", "quality"),
        ("currency", "moneda de reporte", "txt"), ("revenue_ttm", "ingresos TTM", "money"),
        ("revenue_last_q", "ingresos último trimestre", "money"), ("revenue_growth_yoy", "crecimiento ingresos a/a", "pct"),
        ("gross_margin", "margen bruto", "pct"), ("ebit_margin", "margen EBIT", "pct"),
        ("fcf_margin", "margen FCF", "pct"), ("roic", "ROIC", "pct"), ("net_debt", "deuda neta", "money"),
        ("net_debt_to_ebitda", "deuda neta / EBITDA", "x"), ("dilution_yoy", "dilución a/a", "pct"),
        ("cash_runway_quarters", "runway de caja (trimestres)", "num")],
    "Mercado": [
        ("price_asof", "precio: fecha · fuente", "txt"), ("price", "precio", "price"),
        ("market_cap_usd", "market cap", "usd"), ("adv_usd_20d", "liquidez diaria 20d", "usd")],
    "Score y escenarios": [
        ("funnel", "estado en el embudo", "txt"), ("score", "score total (0-10)", "num"),
        ("tier", "capa del embudo", "txt"), ("eligible", "elegible para cartera", "bool"),
        ("confidence", "confianza", "txt"), ("score_date", "fecha del score", "txt"),
        ("scenario", "escenarios usados", "txt"), ("er12", "retorno esperado 12m", "pct+"),
        ("er24", "retorno esperado 24m", "pct+"), ("er36", "retorno esperado 36m", "pct+"),
        ("prob_loss", "P(pérdida permanente)", "pct")],
    "Factores del score (0-10)": [(f"f:{k}", D.FACTOR_ES.get(k, k), "num") for k in w],
}


def fmt(kind: str, v, row: pd.Series):  # noqa: ANN001, ANN202
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    if kind == "pct":
        return f"{v:.1%}"
    if kind == "pct+":
        return f"{v:+.1%}"
    if kind == "money":
        return D.fmt_money(v, row.get("currency") if isinstance(row.get("currency"), str) else None)
    if kind == "usd":
        return D.fmt_money(v, "USD")
    if kind == "price":
        ccy = row.get("price_ccy")
        return f"{v:,.2f} {ccy if isinstance(ccy, str) else ''}".strip()
    if kind == "x":
        return f"{v:.2f}x"
    if kind == "bool":
        return "sí" if v else "no"
    if kind == "quality":
        return QUALITY.get(v, v)
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


st.subheader("Métricas lado a lado")
for title, spec in SECTIONS.items():
    st.markdown(f"**{title}**")
    tab = pd.DataFrame({D.company_label(cid, names.get(cid)): [fmt(kind, df.loc[cid].get(k), df.loc[cid])
                                                               for k, _, kind in spec] for cid in df.index},
                       index=[lab for _, lab, _ in spec])
    st.dataframe(tab, width="stretch", placeholder="—", height=D.table_height(len(tab)))
st.caption("Fundamentales calculados desde datapoints con trazabilidad; '—' = dato faltante (no se rellena). "
           "Montos en la moneda de reporte de cada empresa; market cap y liquidez en USD. Revisar la calidad de "
           "datos: resumen de buscador / sintético no son datos verificados. Retorno esperado y P(pérdida) salen "
           "de los escenarios exactos usados por el score.")

fcols = [c for c in df.columns if c.startswith("f:")]
st.subheader("Puntaje por factor (0-10)")
z = df[fcols].astype(float).dropna(how="all") if fcols else pd.DataFrame()
missing = [cid for cid in df.index if cid not in z.index]
if not z.empty:
    xlab = [D.FACTOR_ES.get(c[2:], c[2:]).replace(" ", "<br>", 1) for c in fcols]   # 2 líneas: sin rotar
    fig = go.Figure(go.Heatmap(z=z.values, x=xlab, showscale=False,
                               y=[D.company_label(c, names.get(c)) for c in z.index], zmin=0, zmax=10,
                               colorscale=[[0, "#cde2fb"], [0.5, "#3987e5"], [1, "#0d366b"]],
                               text=z.round(1).values, texttemplate="%{text}", xgap=2, ygap=2,
                               hovertemplate="%{y} · %{x}: %{z:.1f}<extra></extra>"))
    fig.update_yaxes(autorange="reversed")
    fig.update_xaxes(side="top", tickangle=0)
    st.plotly_chart(D.plotly_layout(fig, height=120 + 48 * len(z), legend=False), width="stretch")
    st.caption("Valor de cada factor en la celda; color más oscuro = puntaje más alto (escala 0-10).")
else:
    st.info("Ninguna de las empresas elegidas tiene score (se calcula en L2/L3 del embudo: `qa funnel`).")
if missing:
    st.caption("Sin score: " + " · ".join(f"**{D.company_label(c, names.get(c))}** — {D.funnel_explain(fun_by.get(c))}"
                                          for c in missing))
