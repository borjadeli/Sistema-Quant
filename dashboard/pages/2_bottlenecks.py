"""Radar de cuellos de botella: severidad, estado, evidencia y contra-evidencia."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

import data as D  # noqa: E402

st.title("⛔ Radar de cuellos de botella")
D.banner()

m = D.industry_map()
reg = D.bottleneck_registry()
comp = D.companies()
in_db = {}
if not comp.empty:
    for b in reg.values():
        in_db[b.id] = sorted(comp.loc[comp["nodes"].map(lambda ns, b=b: bool(set(ns) & set(b.map_nodes))), "id"])

# capas del registro de bottlenecks (no siempre coinciden con los ids de capa del mapa)
BN_LAYER = {"grid_power": "Red eléctrica", "packaging": "Advanced packaging",
            "datacenter_power_thermal": "Datacenter: energía y térmica"}


def bn_layer(lid: str) -> str:
    return BN_LAYER.get(lid) or D.layer_name(lid)


rows = []
for b in sorted(reg.values(), key=lambda x: -x.severity):
    rows.append({"id": b.id, "cuello de botella": b.name, "capa": bn_layer(b.layer), "_layer": b.layer,
                 "estado": D.BN_STATUS_LABEL.get(b.status, b.status), "_status": b.status,
                 "confianza": D.es(D.LEVEL_ES, b.confidence.split()[0]), "severidad": b.severity,
                 "empresas": len(in_db.get(b.id, [])), "evidencia": len(b.raw.get("evidence", [])),
                 "contra-evidencia": len(b.raw.get("counter_evidence", [])),
                 "duración estimada": b.raw.get("estimated_duration")})
df = pd.DataFrame(rows)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Bottlenecks registrados", len(df))
c2.metric("Escasez actual", int((df["_status"] == "current_shortage").sum()))
c3.metric("Proyectada / especulativa", int(df["_status"].isin(["projected_shortage", "speculative_hypothesis"]).sum()))
c4.metric("Aliviándose", int((df["_status"] == "easing").sum()))

st.caption("Severidad 0-10 calculada por regla reproducible (estado, confianza, concentración del nodo, fuentes "
           "primarias, contra-evidencia; máx. 9 por regla). Mide la evidencia de restricción física, "
           "**no** si el mercado ya la incorporó al precio. La evidencia se obtuvo de extractos de buscador "
           "(ver `access_note` de cada registro).")

f1, f2 = st.columns([2, 2])
with f1:
    layers = st.multiselect("Capa", sorted(df["_layer"].unique(), key=bn_layer), default=[], format_func=bn_layer,
                            placeholder="Todas las capas")
with f2:
    stats = st.multiselect("Estado", list(D.BN_STATUS_LABEL), default=[], format_func=D.BN_STATUS_LABEL.get,
                           placeholder="Todos los estados")
layers = layers or sorted(df["_layer"].unique())     # vacío = todas
stats = stats or list(D.BN_STATUS_LABEL)
view = df[df["_layer"].isin(layers) & df["_status"].isin(stats)]

fig = go.Figure()
for st_key, label in D.BN_STATUS_LABEL.items():
    sub = view[view["_status"] == st_key].sort_values("severidad")
    if sub.empty:
        continue
    fig.add_trace(go.Bar(y=sub["cuello de botella"], x=sub["severidad"], orientation="h", name=label,
                         marker=dict(color=D.BN_STATUS_COLOR[st_key], line=dict(color="#fcfcfb", width=2),
                                     cornerradius=4),
                         customdata=sub[["confianza", "empresas", "evidencia", "contra-evidencia"]].values,
                         hovertemplate="<b>%{y}</b><br>severidad %{x}<br>confianza %{customdata[0]}"
                                       "<br>empresas en universo %{customdata[1]}<br>evidencia %{customdata[2]} · "
                                       "contra %{customdata[3]}<extra></extra>"))
order = view.sort_values("severidad")["cuello de botella"].tolist()
fig.update_yaxes(categoryorder="array", categoryarray=order, tickfont=dict(size=11))
fig.update_xaxes(range=[0, 10], title="severidad (0-10)")
fig.update_layout(barmode="overlay", bargap=0.55)
st.plotly_chart(D.plotly_layout(fig, height=max(320, 30 * len(view) + 120)), width="stretch")

st.dataframe(view.drop(columns=["_status", "_layer"]), hide_index=True, width="stretch", placeholder="—",
             column_config={"severidad": st.column_config.ProgressColumn("severidad", min_value=0, max_value=10,
                                                                         format="%.1f")})

st.subheader("Evidencia por cuello de botella")
for b in sorted(reg.values(), key=lambda x: -x.severity):
    if b.layer not in layers or b.status not in stats:
        continue
    with st.expander(f"{D.BN_STATUS_LABEL.get(b.status, b.status)} · **{b.name}** · severidad {b.severity}"):
        r = b.raw
        st.markdown(f"**Restringido:** {r.get('constrained_item', '—')}\n\n**Demanda:** {r.get('demand_driver', '—')}\n\n"
                    f"**Límite de oferta:** {r.get('supply_limit', '—')}\n\n**Duración estimada:** "
                    f"{r.get('estimated_duration', '—')}")
        st.caption(f"Nodos del mapa: {', '.join(b.map_nodes)} · Regla de severidad: {b.severity_explain}")
        if r.get("confidence_reason"):
            st.caption(f"Confianza ({D.es(D.LEVEL_ES, b.confidence)}): {r['confidence_reason']}")
        ev = pd.DataFrame(r.get("evidence", []))
        if not ev.empty:
            st.markdown("**Evidencia**")
            st.dataframe(ev, hide_index=True, width="stretch", placeholder="—",
                         column_config=D.colcfg(ev, source_url=st.column_config.LinkColumn("enlace"),
                                                secondary_url=st.column_config.LinkColumn("enlace secundario")))
        ce = pd.DataFrame(r.get("counter_evidence", []))
        if not ce.empty:
            st.markdown("**Contra-evidencia** (la mostramos siempre: no ocultar evidencia contradictoria)")
            st.dataframe(ce, hide_index=True, width="stretch", placeholder="—",
                         column_config=D.colcfg(ce, source_url=st.column_config.LinkColumn("enlace")))
        sup = pd.DataFrame(r.get("known_suppliers", []))
        if not sup.empty:
            st.markdown("**Proveedores conocidos (según evidencia)**")
            st.dataframe(sup, hide_index=True, width="stretch", placeholder="—", column_config=D.colcfg(sup))
        st.markdown("**Empresas del universo expuestas:** " + (", ".join(in_db.get(b.id, [])) or "—"))
        if r.get("access_note"):
            st.caption(f"Nota de acceso: {r['access_note']}")
