"""Mapa industrial IA + Energía + Commodities."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import networkx as nx  # noqa: E402
import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

import data as D  # noqa: E402

st.title("🗺️ Mapa industrial")
D.banner()

m = D.industry_map()
reg = D.bottleneck_registry()
counts = D.node_company_counts()
crit = m.structural_criticality()

node_bn: dict[str, list] = {}
for b in reg.values():
    for n in b.map_nodes:
        node_bn.setdefault(n, []).append(b)
sev = {n: max(b.severity for b in bs) for n, bs in node_bn.items()}

# Rampa ordinal (azul, pasos >= 250 sobre fondo claro). Sin bottleneck = gris neutro.
BINS = [(8.5, "#184f95", "≥ 8.5"), (7.5, "#2a78d6", "7.5 – 8.5"), (6.0, "#5598e7", "6 – 7.5"), (0.0, "#86b6ef", "< 6")]
NONE_COLOR = "#e1e0d9"


def sev_color(n: str) -> str:
    if n not in sev:
        return NONE_COLOR
    return next(c for lo, c, _ in BINS if sev[n] >= lo)


st.caption(f"Mapa v{m.version} · {len(m.layers)} capas · {len(m.segments)} segmentos · {len(m.nodes)} nodos · "
           f"{len(m.edges)} dependencias. Nodos = funciones económicas (componentes, materiales, capacidades), "
           "no sectores bursátiles. Concentración y sustituibilidad son priors del analista.")

tab_tree, tab_net, tab_node = st.tabs(["Jerarquía", "Red de dependencias", "Detalle de nodo"])

# ----------------------------------------------------------------------------- jerarquía
with tab_tree:
    c1, c2 = st.columns([1, 3])
    with c1:
        kind = st.radio("Vista", ["Treemap", "Sunburst"], horizontal=True)
        st.markdown("**Severidad del cuello de botella**")
        for _, col, lab in BINS:
            st.markdown(f"<span style='display:inline-block;width:12px;height:12px;background:{col};"
                        f"border-radius:3px;margin-right:6px'></span>{lab}", unsafe_allow_html=True)
        st.markdown(f"<span style='display:inline-block;width:12px;height:12px;background:{NONE_COLOR};"
                    f"border-radius:3px;margin-right:6px'></span>sin bottleneck registrado", unsafe_allow_html=True)
        st.caption("Clic en una capa o segmento para profundizar. "
                   "Tamaño = nº de empresas del universo en el nodo (nodos sin empresas: tamaño mínimo). "
                   "La severidad mide la evidencia de escasez, no el atractivo de invertir.")
    ids, labels, parents, values, colors, hover = [], [], [], [], [], []
    seg_tot: dict[str, float] = {}
    for n in m.nodes.values():
        v = max(counts.get(n.id, 0), 0.4)
        seg_tot[n.segment] = seg_tot.get(n.segment, 0) + v
        ids.append(n.id); labels.append(n.name.split(" (")[0]); parents.append(n.segment); values.append(v)
        colors.append(sev_color(n.id))
        bn = ", ".join(f"{b.id} ({b.severity})" for b in node_bn.get(n.id, [])) or "—"
        hover.append(f"<b>{n.name}</b><br>empresas: {counts.get(n.id, 0)}<br>concentración: {n.concentration or '—'}"
                     f"<br>sustituibilidad: {n.substitutability or '—'}<br>criticidad estructural: {crit[n.id]}"
                     f"<br>bottlenecks: {bn}")
    lay_tot: dict[str, float] = {}
    for sid, (lid, sname) in m.segments.items():
        v = seg_tot.get(sid, 0)
        lay_tot[lid] = lay_tot.get(lid, 0) + v
        ids.append(sid); labels.append(sname); parents.append(lid); values.append(v); colors.append("#f0efec")
        hover.append(f"<b>{sname}</b>")
    for lid, lname in m.layers.items():
        ids.append(lid); labels.append(lname); parents.append(""); values.append(lay_tot.get(lid, 0))
        colors.append("#f9f9f7"); hover.append(f"<b>{lname}</b>")
    common = dict(ids=ids, labels=labels, parents=parents, values=values, branchvalues="total",
                  marker=dict(colors=colors, line=dict(color="#fcfcfb", width=2)), hovertext=hover, hoverinfo="text")
    if kind == "Sunburst":
        fig = go.Figure(go.Sunburst(**common, insidetextorientation="radial", maxdepth=2))
    else:
        fig = go.Figure(go.Treemap(**common, maxdepth=3, tiling=dict(pad=3), pathbar=dict(visible=True),
                                   textfont=dict(size=12)))
    with c2:
        st.plotly_chart(D.plotly_layout(fig, height=720, legend=False), width="stretch")

# ----------------------------------------------------------------------------- red
with tab_net:
    # orden de flujo para la vista: insumos físicos a la izquierda, demanda final a la derecha
    FLOW = ["critical_materials", "nuclear_fuel", "semicap", "industrial_services", "compute", "memory",
            "packaging_test", "networking_optical", "generation", "grid", "datacenter", "demand"]
    layer_order = [x for x in FLOW if x in m.layers] + [x for x in m.layers if x not in FLOW]
    f1, f2 = st.columns([3, 1])
    with f1:
        sel_layers = st.multiselect("Capas", layer_order, default=layer_order, format_func=lambda x: m.layers[x])
    with f2:
        only_bn = st.toggle("Solo nodos en bottlenecks y vecinos", value=False)
    g = m.graph()
    keep = {n for n in g.nodes if m.nodes[n].layer in sel_layers}
    if only_bn:
        core = {n for n in keep if n in sev}
        keep = core | {v for n in core for v in list(g.predecessors(n)) + list(g.successors(n)) if v in keep}
    sg = g.subgraph(keep).copy()
    if not sg.nodes:
        st.info("Ninguna capa seleccionada.")
    else:
        for n in sg.nodes:
            sg.nodes[n]["li"] = layer_order.index(m.nodes[n].layer)
        pos = nx.multipartite_layout(sg, subset_key="li", align="vertical", scale=1)
        ex, ey = [], []
        for a, b in sg.edges:
            ex += [pos[a][0], pos[b][0], None]; ey += [pos[a][1], pos[b][1], None]
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=ex, y=ey, mode="lines", line=dict(color="#c3c2b7", width=0.7),
                                 hoverinfo="skip", showlegend=False))
        for in_bn, name, color in ((False, "sin bottleneck", "#c3c2b7"), (True, "en bottleneck registrado", "#2a78d6")):
            ns = [n for n in sg.nodes if (n in sev) == in_bn]
            if not ns:
                continue
            fig.add_trace(go.Scatter(
                x=[pos[n][0] for n in ns], y=[pos[n][1] for n in ns], mode="markers", name=name,
                marker=dict(size=[9 + 2.2 * min(counts.get(n, 0), 12) for n in ns], color=color,
                            line=dict(color="#fcfcfb", width=2)),
                text=[f"<b>{m.nodes[n].name}</b><br>capa: {m.layers[m.nodes[n].layer]}<br>empresas: {counts.get(n, 0)}"
                      f"<br>severidad: {sev.get(n, '—')}<br>insumos: {sg.in_degree(n)} · dependientes: {sg.out_degree(n)}"
                      for n in ns], hoverinfo="text"))
        # etiquetado selectivo: los ~12 nodos más severos (desempate por nº de empresas)
        hi = sorted([n for n in sg.nodes if n in sev], key=lambda n: (-sev[n], -counts.get(n, 0)))[:12]
        fig.add_trace(go.Scatter(x=[pos[n][0] for n in hi], y=[pos[n][1] for n in hi], mode="text", cliponaxis=False,
                                 text=[m.nodes[n].name.split(" (")[0][:28] for n in hi], textposition="middle right",
                                 textfont=dict(size=10, color=D.INK["secondary"]), hoverinfo="skip", showlegend=False))
        xs = sorted({round(pos[n][0], 3) for n in sg.nodes})
        lay_x = {}
        for n in sg.nodes:
            lay_x.setdefault(m.nodes[n].layer, pos[n][0])
        def wrap(t: str, w: int = 13) -> str:
            lines, cur = [], ""
            for word in t.split():
                if len(cur) + len(word) + 1 > w and cur:
                    lines.append(cur); cur = word
                else:
                    cur = f"{cur} {word}".strip()
            return "<br>".join(lines + [cur])
        fig.update_xaxes(tickvals=list(lay_x.values()), ticktext=[wrap(m.layers[k]) for k in lay_x], showgrid=False,
                         tickangle=0, tickfont=dict(size=10), automargin=True)
        fig.update_yaxes(visible=False)
        st.plotly_chart(D.plotly_layout(fig, height=760), width="stretch")
        st.caption("Columnas = capas, de insumos físicos (izquierda) a demanda final (derecha); aristas = dependencias "
                   "insumo→producto. Tamaño = empresas del universo. Etiquetas solo en los 12 nodos más severos; "
                   "el resto en el hover.")

# ----------------------------------------------------------------------------- detalle de nodo
with tab_node:
    opts = sorted(m.nodes, key=lambda n: (-sev.get(n, 0), n))
    nid = st.selectbox("Nodo", opts, format_func=lambda n: f"{m.nodes[n].name}  [{n}]")
    n = m.nodes[nid]
    a, b, c, d = st.columns(4)
    a.metric("Empresas en el universo", counts.get(nid, 0))
    b.metric("Criticidad estructural (prior)", crit[nid])
    c.metric("Severidad bottleneck", sev.get(nid, "—"))
    d.metric("Concentración / sustituibilidad", f"{n.concentration or '—'} / {n.substitutability or '—'}")
    st.markdown(f"**Capa:** {m.layers[n.layer]} › {m.segments[n.segment][1]} · **tipo:** {n.type}"
                + (f" · _{n.notes}_" if n.notes else ""))
    path = m.path_to_demand(nid)
    if path:
        st.markdown("**Camino a la demanda final:** " + " → ".join(m.nodes[p].name.split(" (")[0] for p in path))
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Insumos (upstream)**")
        ups = sorted(m.upstream(nid))
        st.write(", ".join(m.nodes[u].name.split(" (")[0] for u in ups) or "—")
    with col2:
        st.markdown("**Dependientes (downstream)**")
        dns = sorted(m.downstream(nid))
        st.write(", ".join(m.nodes[u].name.split(" (")[0] for u in dns) or "—")
    st.markdown("**Bottlenecks que incluyen este nodo**")
    if node_bn.get(nid):
        st.dataframe(pd.DataFrame([{"id": bb.id, "nombre": bb.name, "estado": D.BN_STATUS_LABEL.get(bb.status, bb.status),
                                    "severidad": bb.severity, "confianza": bb.confidence.split()[0]}
                                   for bb in node_bn[nid]]), hide_index=True, width="stretch")
    else:
        st.write("Ninguno con evidencia registrada.")
    st.markdown("**Empresas del universo en el nodo** (base seleccionada)")
    comp = D.companies()
    if not comp.empty:
        sub = comp[comp["nodes"].map(lambda ns: nid in ns)]
        if sub.empty:
            st.write("Ninguna — candidato para el agente Discovery (`qa discover --nodes " + nid + "`).")
        else:
            st.dataframe(sub[["id", "name", "exchange", "country", "status", "verification"]], hide_index=True,
                         width="stretch")
