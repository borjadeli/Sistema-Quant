import networkx as nx

from qa.bottlenecks import load_bottlenecks
from qa.map.model import load_map, validate_map
from qa.universe.model import coverage_report, load_universe, validate_universe


def test_map_valid_and_acyclic():
    m = load_map()
    assert validate_map(m) == []
    assert nx.is_directed_acyclic_graph(m.graph())
    assert len(m.nodes) >= 130


def test_every_node_reaches_final_demand():
    m = load_map()
    for nid, n in m.nodes.items():
        if n.layer != "demand":
            assert m.path_to_demand(nid), f"{nid} sin camino a la demanda"


def test_structural_criticality_range():
    c = load_map().structural_criticality()
    assert all(0 <= v <= 10 for v in c.values())
    assert c["euv_litho"] > c["copper"]          # monopolio > commodity


def test_universe_valid_size_and_seeds():
    m, u = load_map(), load_universe()
    assert validate_universe(u, m) == []
    assert 150 <= len(u.companies) <= 250
    seeds = {c.id for c in u.seeds()}
    assert {"AXTI", "FORM", "AAOI", "COHR", "FN", "RMBS", "AMKR", "AEHR", "POWL", "HPS.A", "NVT", "BWXT", "BE",
            "ERO", "IVN", "CCJ", "UUUU", "IREN", "CIEN", "CIFR"} == seeds
    assert all(isinstance(c.ticker, str) for c in u.companies.values())   # YAML 'ON' -> bool (regresión)


def test_universe_has_no_hand_entered_market_data():
    raw = open("data/universe/companies.yaml", encoding="utf-8").read().lower()
    for forbidden in ("market_cap:", "price:", "mcap:"):
        assert forbidden not in raw, "market data must come from APIs with provenance"


def test_coverage_and_bottleneck_mapping():
    m, u = load_map(), load_universe()
    cov = coverage_report(u, m)
    assert cov["nodes_covered"] >= 120
    reg = load_bottlenecks(m, u)
    assert len(reg) >= 15
    for b in reg.values():
        assert all(n in m.nodes for n in b.map_nodes)
        assert 0 <= b.severity <= 9.0          # 10 solo por override justificado
        assert b.raw.get("evidence"), f"{b.id} sin evidencia"
