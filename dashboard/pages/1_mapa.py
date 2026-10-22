"""Mapa industrial IA + Energía + Commodities."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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

# Rampa ordinal de severidad compartida por treemap, sunburst y red (data.SEV_BINS). Sin bottleneck = gris neutro.
BINS = D.SEV_BINS
NONE_COLOR = D.SEV_NONE_COLOR


def sev_color(n: str) -> str:
    return D.sev_color(sev.get(n))


def short(t: str, w: int = 18, lines: int = 1) -> str:
    """Corta por palabra a ~w caracteres por línea (hasta `lines` líneas), con '…' si se recortó.

    Nunca corta a mitad de palabra (salvo una palabra sola más larga que w)."""
    words = t.split(" (")[0].split()
    out: list[str] = []
    cur = ""
    while words:
        word = words[0]
        if not cur or len(cur) + 1 + len(word) <= w:
            cur = f"{cur} {word}".strip() if len(word) <= w or cur else word[:w - 1] + "…"
            words.pop(0)
            continue
        out.append(cur); cur = ""
        if len(out) == lines:
            break
    if cur and len(out) < lines:
        out.append(cur)
    text = "<br>".join(out)
    return text if not words or text.endswith("…") else text.rstrip(",;:/ ") + "…"


def wrap(t: str, w: int = 13) -> str:
    lines, cur = [], ""
    for word in t.split():
        if len(cur) + len(word) + 1 > w and cur:
            lines.append(cur); cur = word
        else:
            cur = f"{cur} {word}".strip()
    return "<br>".join(lines + [cur])


st.caption(f"Mapa v{m.version} · {len(m.layers)} capas · {len(m.segments)} segmentos · {len(m.nodes)} nodos · "
           f"{len(m.edges)} dependencias. Nodos = funciones económicas (componentes, materiales, capacidades), "
           "no sectores bursátiles. Concentración y sustituibilidad son priors del analista.")


def severity_legend() -> None:
    st.markdown("**Severidad del cuello de botella**")
    for _, col, lab in BINS:
        st.markdown(f"<span style='display:inline-block;width:12px;height:12px;background:{col};"
                    f"border-radius:3px;margin-right:6px'></span>{lab}", unsafe_allow_html=True)
    st.markdown(f"<span style='display:inline-block;width:12px;height:12px;background:{NONE_COLOR};"
                f"border-radius:3px;margin-right:6px'></span>sin bottleneck registrado", unsafe_allow_html=True)


tab_tree, tab_net, tab_node = st.tabs(["Jerarquía", "Red de dependencias", "Detalle de nodo"])

# ----------------------------------------------------------------------------- jerarquía
with tab_tree:
    c1, c2 = st.columns([1, 3])
    with c1:
        kind = st.radio("Vista", ["Treemap", "Sunburst"], horizontal=True)
        severity_legend()
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
# abreviaturas de una línea para el eje x con muchas columnas (nombre completo en el hover de cada nodo);
# con <= 6 columnas se usa el nombre completo en 2 líneas
LAYER_SHORT = {"critical_materials": "Materiales", "nuclear_fuel": "Nuclear", "semicap": "Equipos fab.",
               "industrial_services": "Serv. indust.", "compute": "Cómputo", "memory": "Memoria",
               "packaging_test": "Packaging", "networking_optical": "Redes/óptica", "generation": "Generación",
               "grid": "Red eléctrica", "datacenter": "Datacenters", "demand": "Demanda IA"}

with tab_net:
    # orden de flujo para la vista: insumos físicos a la izquierda, demanda final a la derecha
    FLOW = ["critical_materials", "nuclear_fuel", "semicap", "industrial_services", "compute", "memory",
            "packaging_test", "networking_optical", "generation", "grid", "datacenter", "demand"]
    layer_order = [x for x in FLOW if x in m.layers] + [x for x in m.layers if x not in FLOW]
    f1, f2 = st.columns([3, 2], vertical_alignment="bottom")
    with f1:
        sel_layers = st.multiselect("Capas", layer_order, default=[], format_func=lambda x: m.layers[x],
                                    placeholder="Todas las capas")
    with f2:
        only_bn = st.toggle("Solo bottlenecks + vecinos", value=False,
                            help="Muestra solo los nodos incluidos en algún cuello de botella registrado y sus "
                                 "insumos y dependientes directos (dentro de las capas elegidas).")
    shown_layers = sel_layers or layer_order          # vacío = todas
    g = m.graph()
    keep = {n for n in g.nodes if m.nodes[n].layer in shown_layers}
    if only_bn:
        core = {n for n in keep if n in sev}
        keep = core | {v for n in core for v in list(g.predecessors(n)) + list(g.successors(n)) if v in keep}
    sg = g.subgraph(keep).copy()
    if not sg.nodes:
        st.info("No hay nodos para las capas y el filtro elegidos.")
    else:
        cols = [lid for lid in layer_order if lid in shown_layers and any(m.nodes[n].layer == lid for n in sg)]
        seg_rank = {sid: i for i, sid in enumerate(m.segments)}
        col_nodes = {lid: sorted((n for n in sg if m.nodes[n].layer == lid),
                                 key=lambda n: (seg_rank.get(m.nodes[n].segment, 0), m.nodes[n].name))
                     for lid in cols}
        y: dict[str, float] = {}

        def place(lid: str) -> None:
            ns = col_nodes[lid]
            for i, n in enumerate(ns):
                y[n] = (len(ns) - 1) / 2 - i          # columna centrada, de arriba hacia abajo

        for lid in cols:
            place(lid)
        # heurística de baricentro (Sugiyama): ordenar cada columna por la posición media de sus vecinos
        # en otras columnas, alternando barridos izquierda→derecha y derecha→izquierda, reduce cruces.
        und = sg.to_undirected(as_view=True)
        for it in range(8):
            for lid in (cols if it % 2 == 0 else cols[::-1]):
                def bary(n: str, lid: str = lid) -> float:
                    nb = [y[v] for v in und.neighbors(n) if m.nodes[v].layer != lid]
                    return sum(nb) / len(nb) if nb else y[n]
                col_nodes[lid] = sorted(col_nodes[lid], key=lambda n: -bary(n))
                place(lid)
        xi = {lid: i for i, lid in enumerate(cols)}
        pos = {n: (xi[m.nodes[n].layer], y[n]) for n in sg}

        def edge_xy(a: str, b: str) -> tuple[list, list]:
            """Recta entre columnas; dentro de una misma capa, arco (Bézier cuadrática) hacia la derecha, al
            hueco entre columnas, para no atravesar los nodos de la columna (las etiquetas de la última columna
            van a la izquierda, así que tampoco las tapa)."""
            (x0, y0), (x1, y1) = pos[a], pos[b]
            if x0 != x1:
                return [x0, x1, None], [y0, y1, None]
            cx, cy = x0 + min(0.8, 0.12 + 0.05 * abs(y1 - y0)), (y0 + y1) / 2
            ts = [i / 12 for i in range(13)]
            xs = [(1 - t) ** 2 * x0 + 2 * (1 - t) * t * cx + t ** 2 * x1 for t in ts]
            ys = [(1 - t) ** 2 * y0 + 2 * (1 - t) * t * cy + t ** 2 * y1 for t in ts]
            return xs + [None], ys + [None]

        fig = go.Figure()
        # aristas: gris tenue; las que unen dos nodos en bottleneck (cadena de restricciones) resaltadas
        for hi_edge, name, color, width, opacity in (
                (False, "dependencia", D.INK["axis"], 0.8, 0.35),
                (True, "dependencia entre nodos en bottleneck", D.SERIES[0], 1.4, 0.55)):
            ex, ey = [], []
            for a, b in sg.edges:
                if (a in sev and b in sev) == hi_edge:
                    xs, ys = edge_xy(a, b)
                    ex += xs; ey += ys
            if ex:
                fig.add_trace(go.Scatter(x=ex, y=ey, mode="lines", name=name, opacity=opacity,
                                         line=dict(color=color, width=width), hoverinfo="skip",
                                         showlegend=hi_edge))
        # nodos: misma rampa de severidad que el treemap
        groups = [(lab, col, lambda n, lo=lo, prev=prev: n in sev and lo <= sev[n] < prev)
                  for (lo, col, lab), prev in zip(BINS, [float("inf")] + [b[0] for b in BINS[:-1]])]
        groups.append(("sin bottleneck", NONE_COLOR, lambda n: n not in sev))
        for lab, color, pred in groups:
            ns = [n for n in sg.nodes if pred(n)]
            if not ns:
                continue
            fig.add_trace(go.Scatter(
                x=[pos[n][0] for n in ns], y=[pos[n][1] for n in ns], mode="markers",
                name=f"severidad {lab}" if lab != "sin bottleneck" else lab,
                marker=dict(size=[9 + 1.5 * min(counts.get(n, 0), 10) for n in ns], color=color,
                            line=dict(color="#fcfcfb", width=2)),
                text=[f"<b>{m.nodes[n].name}</b><br>capa: {m.layers[m.nodes[n].layer]}"
                      f"<br>segmento: {m.segments[m.nodes[n].segment][1]}<br>empresas: {counts.get(n, 0)}"
                      f"<br>severidad: {sev.get(n, '—')}"
                      + (f" ({', '.join(b.id for b in node_bn[n])})" if n in node_bn else "")
                      + f"<br>insumos: {sg.in_degree(n)} · dependientes: {sg.out_degree(n)}" for n in ns],
                hoverinfo="text"))
        # etiquetado selectivo: los ~12 nodos más severos (desempate por nº de empresas); todos si son pocos.
        # Anotaciones con fondo semitransparente, desplazadas fuera del marcador; la última columna etiqueta
        # hacia la izquierda para no salirse del gráfico.
        labelled = (list(sg.nodes) if len(sg) <= 24 else
                    sorted([n for n in sg if n in sev], key=lambda n: (-sev[n], -counts.get(n, 0)))[:12])
        for n in labelled:
            r = (9 + 1.5 * min(counts.get(n, 0), 10)) / 2
            last = pos[n][0] == len(cols) - 1 and len(cols) > 1
            fig.add_annotation(x=pos[n][0], y=pos[n][1], text=short(m.nodes[n].name, 16, lines=2), showarrow=False,
                               xanchor="right" if last else "left", xshift=-(r + 4) if last else r + 4,
                               align="right" if last else "left",
                               font=dict(size=10, color=D.INK["secondary"]), bgcolor="rgba(252,252,251,0.8)",
                               borderpad=1)
        many = len(cols) > 6
        # sin tickangle fijo (auto): horizontales en 2 líneas si caben; Plotly las rota si el ancho no alcanza
        fig.update_xaxes(tickvals=list(range(len(cols))), showgrid=False, zeroline=False,
                         ticktext=[LAYER_SHORT.get(lid, wrap(m.layers[lid], 14)) if many else wrap(m.layers[lid], 18)
                                   for lid in cols],
                         tickfont=dict(size=11), automargin=True, side="top",
                         range=[-0.4, len(cols) - 1 + (1.0 if len(cols) == 1 else 0.45)])
        tallest = max(len(v) for v in col_nodes.values())
        fig.update_yaxes(visible=False, range=[-(tallest + 1) / 2, (tallest + 1) / 2])
        fig = D.plotly_layout(fig, height=max(560, 34 * tallest + 220))
        fig.update_layout(legend=dict(orientation="h", yanchor="top", y=-0.02, xanchor="left", x=0),
                          margin=dict(t=70, b=40, l=50))   # l: espacio para la 1ª etiqueta rotada
        st.plotly_chart(fig, width="stretch")
        st.caption("Columnas = capas, de insumos físicos (izquierda) a demanda final (derecha); aristas = "
                   "dependencias insumo→producto, ordenadas por baricentro para reducir cruces. Color = severidad "
                   "del bottleneck (misma escala que la jerarquía); tamaño = empresas del universo (tope en 10). "
                   + ("Todas las etiquetas visibles." if len(sg) <= 24 else
                      "Etiquetas solo en los 12 nodos más severos; el resto en el hover."))

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
                                    "severidad": bb.severity,
                                    "confianza": D.es(D.LEVEL_ES, bb.confidence.split()[0])}
                                   for bb in node_bn[nid]]), hide_index=True, width="stretch", placeholder="—")
    else:
        st.write("Ninguno con evidencia registrada.")
    st.markdown("**Empresas del universo en el nodo** (base seleccionada)")
    comp = D.companies()
    if not comp.empty:
        sub = comp[comp["nodes"].map(lambda ns: nid in ns)]
        if sub.empty:
            st.write("Ninguna — candidato para el agente Discovery (`qa discover --nodes " + nid + "`).")
        else:
            sub = sub[["id", "name", "exchange", "country", "status", "verification"]].assign(
                status=sub["status"].map(lambda v: D.es(D.COMPANY_STATUS_ES, v)),
                verification=sub["verification"].map(lambda v: D.es(D.VERIFICATION_ES, v)))
            st.dataframe(sub, hide_index=True, width="stretch", placeholder="—",
                         column_config=D.colcfg(sub, id="ticker", verification="identidad"))
