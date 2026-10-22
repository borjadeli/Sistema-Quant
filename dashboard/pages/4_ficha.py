"""Ficha individual de empresa."""
from __future__ import annotations

import json
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

st.title("📄 Ficha de empresa")
D.banner()

m = D.industry_map()
reg = D.bottleneck_registry()
cid = D.company_select(default=st.query_params.get("c"))
if cid is None:
    st.stop()
comp = D.companies()
c = comp[comp["id"] == cid].iloc[0]


@st.cache_data(ttl=60, show_spinner=False)
def fundamentals(url: str, cid: str):  # noqa: ANN201
    with Session(D._engine(url)) as s:
        pts = repo.load_datapoints(s, cid)
    return pts, (compute_fundamentals(cid, pts) if pts else None)


pts, f = fundamentals(D.db_url(D.current_db()), cid)

# ----------------------------------------------------------------------------- identidad
st.markdown(f"## {c['name']}  `{cid}`")
st.caption(f"{c['exchange']} · {c['country']} · {c['currency']} · estado: {D.es(D.COMPANY_STATUS_ES, c['status'])} · "
           f"identidad: {D.es(D.VERIFICATION_ES, c['verification'])}"
           + (f" · {c['notes']}" if c.get("notes") else ""))
snap = D.latest_snapshots()
snap = snap[snap["company_id"] == cid] if not snap.empty else snap
a, b, cc, d = st.columns(4)
if not snap.empty:
    s0 = snap.iloc[-1]
    a.metric("Precio", f"{s0['price']:,.2f} {s0['currency'] or ''}",
             help=f"fuente {s0['source']} · al {D.fmt_dt(s0['as_of'])}")
    b.metric("Market cap", D.fmt_money(s0["market_cap_usd"], "USD"))
    cc.metric("Liquidez diaria 20d", D.fmt_money(s0["adv_usd_20d"], "USD"))
else:
    a.metric("Precio", "—", help="sin datos de mercado: `qa ingest`")
    b.metric("Market cap (USD)", "—")
    cc.metric("Liquidez diaria 20d (USD)", "—")
expo = c["exposure"] or {}
d.metric("Exposición (prior) IA/Energía/Comm.", f"{expo.get('ai', '—')}/{expo.get('energy', '—')}/{expo.get('commodities', '—')}",
         help="prior del analista, no dato; se reemplaza por % de ingresos reportado")

tabs = st.tabs(["Mapa y bottlenecks", "Fundamentales", "Escenarios", "Score", "Agentes y discrepancias",
                "Investigación web"])

# ----------------------------------------------------------------------------- mapa
with tabs[0]:
    nodes = [n for n in c["nodes"] if n in m.nodes]
    st.dataframe(pd.DataFrame([{"nodo": n, "nombre": m.nodes[n].name, "capa": m.layers[m.nodes[n].layer],
                                "concentración": m.nodes[n].concentration, "sustituibilidad": m.nodes[n].substitutability}
                               for n in nodes]), hide_index=True, width="stretch", placeholder="—")
    if nodes:
        path = m.path_to_demand(nodes[0])
        if path:
            st.markdown("**Camino a la demanda final (nodo principal):** "
                        + " → ".join(m.nodes[p].name.split(" (")[0] for p in path))
    bl = [x for x in reg.values() if set(nodes) & set(x.map_nodes)]
    st.markdown("**Cuellos de botella vinculados**")
    if bl:
        st.dataframe(pd.DataFrame([{"id": x.id, "nombre": x.name, "estado": D.BN_STATUS_LABEL.get(x.status, x.status),
                                    "severidad": x.severity, "confianza": D.es(D.LEVEL_ES, x.confidence.split()[0])}
                                   for x in bl])
                     .sort_values("severidad", ascending=False), hide_index=True, width="stretch", placeholder="—")
    else:
        st.write("Ninguno registrado.")

# ----------------------------------------------------------------------------- fundamentales
with tabs[1]:
    if f is None:
        st.info("Sin datapoints para esta empresa en la base seleccionada (`qa ingest`).")
    else:
        q = {"filing": "✅ documentos regulatorios", "search_summary": "🔎 resúmenes de buscador (no verificado)",
             "synthetic": "🧪 sintético", "mixed": "mixto", "none": "sin datos"}
        st.markdown(f"**Calidad de datos:** {q.get(f.data_quality, f.data_quality)} · moneda {f.currency or '—'} · "
                    f"as of {f.as_of}")
        if f.missing:
            st.caption("Faltantes clave (no se rellenan): " + ", ".join(map(D.metric_label, f.missing)))
        rows = []
        pct = {"gross_margin", "ebit_margin", "ebitda_margin", "net_margin", "fcf_margin", "capex_intensity", "roic",
               "revenue_growth_yoy", "revenue_growth_q_yoy", "dilution_yoy", "sbc_pct_revenue", "incremental_margin"}
        for k, mt in f.metrics.items():
            if mt.value is None:
                continue
            ccy = None if k.startswith(("shares", "diluted_shares")) else f.currency
            val = (f"{mt.value:.1%}" if k in pct else
                   f"{mt.value:.2f}" if abs(mt.value) < 100 else D.fmt_money(mt.value, ccy))
            rows.append({"métrica": D.metric_label(k), "valor": val, "período": mt.period, "nota": mt.note,
                         "insumos": "; ".join(mt.inputs[:2]) + (" …" if len(mt.inputs) > 2 else "")})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", placeholder="—")
        rev = f.quarterly.get("revenue", {})
        if rev:
            ser = pd.Series(rev).sort_index().tail(12)
            fig = go.Figure(go.Bar(x=[str(x) for x in ser.index], y=ser.values, marker=dict(color=D.SERIES[0],
                                   cornerradius=4), hovertemplate="%{x}<br>%{y:,.0f}<extra></extra>"))
            fig.update_layout(title="Ingresos trimestrales", bargap=0.35)
            fig.update_yaxes(title=f.currency or "")
            st.plotly_chart(D.plotly_layout(fig, height=320, legend=False), width="stretch")
        with st.expander(f"Datapoints originales con fuente ({len(pts)})"):
            dp = pd.DataFrame([{"métrica": D.metric_label(p.metric), "valor": p.value, "unidad": p.unit, "período": p.period_type,
                                "fin": p.period_end, "conocido": p.known_at,
                                "verificación": D.es(D.VERIFICATION_ES, p.verification.value),
                                "fuente": p.source.name, "doc": p.source.document_type, "url": p.source.url,
                                "nota": p.note} for p in pts]).sort_values(["métrica", "fin"])
            st.dataframe(dp, hide_index=True, width="stretch", placeholder="—",
                         column_config={"url": st.column_config.LinkColumn("url")})

# ----------------------------------------------------------------------------- escenarios
with tabs[2]:
    sc = D.latest_scenarios()
    sc = sc[sc["company_id"] == cid] if not sc.empty else sc
    if sc.empty:
        st.info("Sin escenarios (los propone el Valuation Analyst en L2/L3).")
    else:
        row = sc.iloc[-1]
        res = row["results"] or {}
        st.warning("Probabilidades **subjetivas** asignadas por agentes; no son estadísticas calibradas. "
                   f"Base: {row['probability_basis']}", icon="⚠️")
        st.caption(f"Autor: {row['author']} · precio al crear: {row['price_at_creation']} ({row['price_source']}) · "
                   f"{row['created_at']}")
        p0 = res.get("price0")
        fig = go.Figure()
        # bear / base / bull = polaridad: polos rojo/azul y base en gris neutro (paleta divergente)
        color = {"bear": "#e34948", "base": "#898781", "bull": "#2a78d6"}
        tab = []
        for r in res.get("results", []):
            pt = r["price_target"]
            xs = [0, 12, 24, 36]
            ys = [p0] + [pt.get(str(h)) for h in (12, 24, 36)]
            fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines+markers", name=f"{r['name']} (p={r['probability']:.0%})",
                                     line=dict(color=color.get(r["name"]), width=2), marker=dict(size=8),
                                     hovertemplate=f"{r['name']}<br>mes %{{x}}: %{{y:,.2f}}<extra></extra>"))
            tab.append({"escenario": r["name"], "prob.": r["probability"],
                        **{f"PO {h}m": pt.get(str(h)) for h in (12, 24, 36)},
                        **{f"ret {h}m": r["returns"].get(str(h)) for h in (12, 24, 36)},
                        "financiamiento req.": r.get("financing_need"),
                        "dilución por financ.": r.get("dilution_from_financing")})
        fig.add_hline(y=p0, line=dict(color=D.INK["axis"], width=1))
        fig.update_xaxes(title="meses", tickvals=[0, 12, 24, 36])
        fig.update_yaxes(title="precio objetivo")
        st.plotly_chart(D.plotly_layout(fig, height=360), width="stretch")
        st.dataframe(pd.DataFrame(tab), hide_index=True, width="stretch", placeholder="—", column_config={
            "prob.": st.column_config.NumberColumn(format="percent"),
            **{f"ret {h}m": st.column_config.NumberColumn(format="percent") for h in (12, 24, 36)},
            **{f"PO {h}m": st.column_config.NumberColumn(format="%.2f") for h in (12, 24, 36)},
            "financiamiento req.": st.column_config.NumberColumn(format="%.0f"),
            "dilución por financ.": st.column_config.NumberColumn(format="percent")})
        er = res.get("expected_return", {})
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Retorno esperado 12m", D.fmt_pct(er.get("12")))
        k2.metric("Retorno esperado 36m", D.fmt_pct(er.get("36")))
        k3.metric("P(pérdida permanente >30%)", f"{res.get('prob_permanent_loss', 0):.0%}")
        ud = res.get("upside_downside_ratio")
        k4.metric("Asimetría bull/|bear| 36m", f"{ud:.1f}x" if ud else "—")
        for w in res.get("warnings", []):
            st.caption(f"⚠️ {w}")
        with st.expander("Supuestos (estimaciones de agentes)"):
            st.json(row["assumptions"])
        if row.get("reverse_dcf"):
            st.markdown("**Reverse DCF**")
            st.json(row["reverse_dcf"])

# ----------------------------------------------------------------------------- score
with tabs[3]:
    scs = D.latest_scores()
    scs = scs[scs["company_id"] == cid] if not scs.empty else scs
    if scs.empty:
        st.info("Sin score (se calcula en L2/L3).")
    else:
        s = scs.iloc[-1]
        w = D.cfg()["scoring"]["weights"]
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Score total", f"{s['total']:.2f}", help=f"sin topes: {s['total_uncapped']:.2f}")
        k2.metric("Capa", s["tier"])
        k3.metric("Confianza", D.es(D.LEVEL_ES, s["confidence"]))
        k4.metric("Elegible para cartera", "sí" if s["eligible"] else "no")
        if pd.notna(s["scenario_id"]):
            st.caption(f"Calculado el {D.fmt_dt(s['created_at'])} con los escenarios #{int(s['scenario_id'])} "
                       f"({D.fmt_dt(s['scenario_created_at'])}): ER 36m {D.fmt_pct(s['er36'])} · "
                       f"P(pérdida perm.) {D.fmt_pct(s['prob_loss']).lstrip('+')}.")
        else:
            st.caption(f"Calculado el {D.fmt_dt(s['created_at'])} **sin escenarios**: retorno esperado y "
                       "P(pérdida) no aplican a este score.")
        fac = s["factors"] or {}
        names = list(w)
        flabels = [D.FACTOR_ES.get(n, n) for n in names]
        fig = go.Figure(go.Bar(y=flabels, x=[(fac.get(n) or {}).get("score", 0) for n in names], orientation="h",
                               marker=dict(color=D.SERIES[0], cornerradius=4),
                               text=[f"{(fac.get(n) or {}).get('score', 0):.1f} · peso {w[n]:.0%}" for n in names],
                               textposition="outside", hovertemplate="%{y}: %{x:.2f}<extra></extra>"))
        fig.update_xaxes(range=[0, 12.5], tickvals=[0, 2, 4, 6, 8, 10], title="puntaje 0-10")
        fig.update_layout(bargap=0.45)
        fig.update_yaxes(categoryorder="array", categoryarray=flabels[::-1])
        st.plotly_chart(D.plotly_layout(fig, height=300, legend=False), width="stretch")
        st.dataframe(pd.DataFrame([{"factor": D.FACTOR_ES.get(n, n), "puntaje": (fac.get(n) or {}).get("score"), "peso": w[n],
                                    "método": (fac.get(n) or {}).get("method"),
                                    "fundamento": (fac.get(n) or {}).get("rationale"),
                                    "evidencia": "; ".join(map(str, (fac.get(n) or {}).get("evidence", [])[:4])),
                                    "faltante": "; ".join((fac.get(n) or {}).get("missing", []))} for n in names]),
                     hide_index=True, width="stretch", placeholder="—",
                     column_config={"peso": st.column_config.NumberColumn(format="percent")})
        for k in s["kill_switches"] or []:
            st.error(f"⛔ {k}")
        for k in s["caps_applied"] or []:
            st.warning(f"🔒 tope aplicado: {k}")
        for k in s["alerts"] or []:
            st.warning(f"⚠️ {k}")
        if s["missing_data"]:
            st.caption("Información faltante: " + "; ".join(s["missing_data"]))

# ----------------------------------------------------------------------------- agentes
with tabs[4]:
    runs = D.query("SELECT * FROM agent_runs WHERE subject = :cid ORDER BY created_at", cid=cid)
    if runs.empty:
        st.info("Ningún agente analizó esta empresa todavía.")
    else:
        rv = runs.assign(status=runs["status"].map(lambda v: D.es(D.RUN_STATUS_ES, v)),
                         created_at=pd.to_datetime(runs["created_at"], utc=True, errors="coerce"))
        cols = ["created_at", "agent", "tier", "model", "status", "cost_usd", "input_tokens", "output_tokens",
                "prompt_version", "input_hash", "error"] + (["reused_from"] if "reused_from" in rv else [])
        st.dataframe(rv[cols], hide_index=True, width="stretch", placeholder="—", column_config=D.colcfg(
            rv, tier="capa", cost_usd=st.column_config.NumberColumn("costo (USD)", format="$%.4f"),
            created_at=st.column_config.DatetimeColumn("fecha", format="YYYY-MM-DD HH:mm"),
            reused_from=st.column_config.NumberColumn("reutiliza corrida #", format="%d")))
        for _, r in runs.iterrows():
            out = D.js(r["output"])
            if out:
                with st.expander(f"{r['agent']} · {r['model']} · {D.es(D.RUN_STATUS_ES, r['status'])} · "
                                 f"{D.fmt_dt(r['created_at'])}"):
                    st.json(out, expanded=False)
                    src = D.js(r["web_sources"]) or []
                    if src:
                        st.caption("Fuentes web: " + " · ".join(src[:15]))
    dis = D.complete_only(D.query("SELECT * FROM disagreements WHERE company_id = :cid ORDER BY created_at", cid=cid))
    st.markdown("**Discrepancias registradas entre agentes**")
    if dis.empty:
        st.write("Ninguna.")
    else:
        dis["positions"] = dis["positions"].map(lambda x: json.dumps(D.js(x), ensure_ascii=False))
        dv = dis[["created_at", "topic", "positions", "resolving_evidence", "status"]]
        dv = dv.assign(status=dv["status"].map({"open": "abierta", "resolved": "resuelta"}).fillna(dv["status"]))
        st.dataframe(dv, hide_index=True, width="stretch", placeholder="—", column_config=D.colcfg(dv))

# ----------------------------------------------------------------------------- research semilla
with tabs[5]:
    sr = D.seed_research().get(cid)
    if not sr:
        st.info("Sin investigación web semilla para esta empresa.")
    else:
        st.info("🔎 **search_summary**: leído en resúmenes de buscador, no en el documento primario. "
                f"{sr.get('verification_method', '')}")
        st.markdown(sr.get("business_summary") or "—")
        st.markdown(f"**Vínculo IA/energía/commodities:** {sr.get('ai_energy_commodity_link') or '—'}")
        rel = pd.DataFrame(sr.get("confirmed_relationships") or [])
        if not rel.empty:
            st.markdown("**Relaciones (según fuente)**")
            st.dataframe(rel, hide_index=True, width="stretch", placeholder="—",
                         column_config=D.colcfg(rel, source_url=st.column_config.LinkColumn("fuente")))
        ev = pd.DataFrame(sr.get("key_recent_events") or [])
        if not ev.empty:
            st.markdown("**Eventos recientes**")
            st.dataframe(ev, hide_index=True, width="stretch", placeholder="—",
                         column_config=D.colcfg(ev, source_url=st.column_config.LinkColumn("fuente")))
        rf = sr.get("red_flags") or []
        if rf:
            st.markdown("**Red flags**")
            rfd = pd.DataFrame(rf) if isinstance(rf[0], dict) else pd.DataFrame({"red flag": rf})
            st.dataframe(rfd, hide_index=True, width="stretch", placeholder="—",
                         column_config=D.colcfg(rfd, source_url=st.column_config.LinkColumn("fuente")))
        if sr.get("notes_unverified"):
            st.caption("No verificado: " + (json.dumps(sr["notes_unverified"], ensure_ascii=False)
                                            if not isinstance(sr["notes_unverified"], str) else sr["notes_unverified"]))
