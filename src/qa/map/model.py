"""Mapa industrial: carga, validación, grafo de dependencias y criticidad estructural."""
from __future__ import annotations

from collections import Counter
from functools import lru_cache
from pathlib import Path

import networkx as nx
import yaml
from pydantic import BaseModel, Field

from qa.config import MAP_PATH

CONCENTRATION_SCORE = {"monopoly": 1.0, "very_high": 0.85, "high": 0.65, "medium": 0.4, "low": 0.2, None: 0.4}
SUBSTITUTABILITY_SCORE = {"none": 1.0, "low": 0.8, "medium": 0.5, "high": 0.2, None: 0.5}
NODE_TYPES = {"component", "material", "capacity", "service", "technology", "equipment"}


class Node(BaseModel):
    id: str
    name: str
    type: str
    layer: str
    segment: str
    concentration: str | None = None
    substitutability: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    notes: str | None = None


class Edge(BaseModel):
    source: str
    target: str
    relation: str = "input_to"


class IndustryMap(BaseModel):
    version: str
    updated: str
    layers: dict[str, str]                # id -> name
    segments: dict[str, tuple[str, str]]  # id -> (layer_id, name)
    nodes: dict[str, Node]
    edges: list[Edge]

    # ------------------------------------------------------------------ grafo
    def graph(self) -> nx.DiGraph:
        g = nx.DiGraph()
        for n in self.nodes.values():
            g.add_node(n.id, **n.model_dump())
        for e in self.edges:
            g.add_edge(e.source, e.target, relation=e.relation)
        return g

    def upstream(self, node_id: str) -> set[str]:
        """Todos los insumos (directos e indirectos) de un nodo."""
        return nx.ancestors(self.graph(), node_id)

    def downstream(self, node_id: str) -> set[str]:
        """Todo lo que depende (directa o indirectamente) de un nodo."""
        return nx.descendants(self.graph(), node_id)

    def structural_criticality(self) -> dict[str, float]:
        """Prior estructural 0-10 de cuán crítico es un nodo como posible cuello de botella.

        criticality = 10 * (0.4*concentración + 0.4*no-sustituibilidad + 0.2*alcance_downstream)
        Es una heurística del mapa, NO evidencia de escasez: la evidencia vive en `bottlenecks.yaml`.
        """
        g = self.graph()
        reach = {n: len(nx.descendants(g, n)) for n in g.nodes}
        max_reach = max(reach.values()) or 1
        out = {}
        for nid, n in self.nodes.items():
            c = CONCENTRATION_SCORE.get(n.concentration, 0.4)
            s = SUBSTITUTABILITY_SCORE.get(n.substitutability, 0.5)
            r = reach[nid] / max_reach
            out[nid] = round(10 * (0.4 * c + 0.4 * s + 0.2 * r), 2)
        return out

    def path_to_demand(self, node_id: str) -> list[str] | None:
        """Camino más corto desde un nodo hasta la demanda final (explica la exposición económica)."""
        g = self.graph()
        targets = [n for n, d in self.nodes.items() if d.layer == "demand"]
        best = None
        for t in targets:
            try:
                p = nx.shortest_path(g, node_id, t)
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                continue
            if best is None or len(p) < len(best):
                best = p
        return best


def load_map(path: Path | str = MAP_PATH) -> IndustryMap:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    layers, segments, nodes, edges = {}, {}, {}, []
    for layer in raw["layers"]:
        layers[layer["id"]] = layer["name"]
        for seg in layer["segments"]:
            segments[seg["id"]] = (layer["id"], seg["name"])
            for nd in seg["nodes"]:
                if nd["id"] in nodes:
                    raise ValueError(f"nodo duplicado: {nd['id']}")
                nodes[nd["id"]] = Node(layer=layer["id"], segment=seg["id"], **nd)
    for n in nodes.values():
        for dep in n.depends_on:
            edges.append(Edge(source=dep, target=n.id))
    overlay = Path(path).with_name("map_overlay.yaml")
    if overlay.exists():                       # cambios aprobados de agentes (Map Curator)
        ov = yaml.safe_load(overlay.read_text(encoding="utf-8")) or {}
        for nd in ov.get("nodes", []):
            nd = {k: v for k, v in nd.items() if not k.startswith("_")}
            seg = nd.pop("segment")
            if nd["id"] not in nodes and seg in segments:
                nodes[nd["id"]] = Node(layer=segments[seg][0], segment=seg, **nd)
                for dep in nd.get("depends_on", []):
                    edges.append(Edge(source=dep, target=nd["id"]))
        raw.setdefault("extra_edges", [])
        raw["extra_edges"] += [{k: v for k, v in e.items() if not k.startswith("_")} for e in ov.get("edges", [])]
    seen = {(e.source, e.target) for e in edges}
    for e in raw.get("extra_edges", []):
        if (e["source"], e["target"]) not in seen:  # extra_edges que repiten depends_on se ignoran
            edges.append(Edge(**e))
            seen.add((e["source"], e["target"]))
    return IndustryMap(version=str(raw["version"]), updated=str(raw["updated"]), layers=layers,
                       segments=segments, nodes=nodes, edges=edges)


@lru_cache(maxsize=1)
def default_map() -> IndustryMap:
    return load_map()


def validate_map(m: IndustryMap) -> list[str]:
    """Devuelve una lista de errores (vacía si el mapa es válido)."""
    errors = []
    for e in m.edges:
        for end in (e.source, e.target):
            if end not in m.nodes:
                errors.append(f"arista {e.source}->{e.target}: nodo inexistente '{end}'")
    for n in m.nodes.values():
        if n.type not in NODE_TYPES:
            errors.append(f"{n.id}: tipo inválido '{n.type}'")
        if n.concentration not in CONCENTRATION_SCORE:
            errors.append(f"{n.id}: concentration inválida '{n.concentration}'")
        if n.substitutability not in SUBSTITUTABILITY_SCORE:
            errors.append(f"{n.id}: substitutability inválida '{n.substitutability}'")
    dup = [k for k, v in Counter((e.source, e.target, e.relation) for e in m.edges).items() if v > 1]
    errors += [f"arista duplicada {d}" for d in dup]
    if not errors:
        g = m.graph()
        isolated = [n for n in g.nodes if g.degree(n) == 0]
        errors += [f"nodo aislado (sin dependencias): {n}" for n in isolated]
    return errors
