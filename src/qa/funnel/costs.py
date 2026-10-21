"""Estimación del costo de un ciclo completo del embudo (antes de gastar)."""
from __future__ import annotations

import json
from datetime import date

from qa.agents.base import AGENTS, system_prompt, user_message
from qa.agents.llm import estimate_cost
from qa.config import settings

L2_AGENTS = ["industrial", "fundamental", "catalyst", "valuation", "expectations"]
L3_AGENTS = ["bear", "valuation_rebuttal", "auditor"]


def estimate_cycle(sample_pack_chars: dict[str, int] | None = None, n_universe: int = 245,
                   n_holdings: int = 10) -> dict:
    """Costo máximo esperado por ciclo con los topes del embudo de settings.yaml.

    sample_pack_chars: tamaño típico del data pack por capa (chars). Por defecto valores medidos aproximados.
    Supone que cada capa se llena hasta su tope (cota superior) y que no hay reutilización.
    """
    cfg = settings()
    f = cfg["funnel"]
    tiers = cfg["llm"]["tiers"]
    sizes = {"L1": 3500, "L2": 14000, "L3": 30000, "committee": 25000} | (sample_pack_chars or {})
    n1 = min(f["L0"]["max_promoted"] + n_holdings, n_universe)
    n2 = f["L1"]["max_promoted"] + n_holdings
    n3 = f["L2"]["max_promoted"] + n_holdings
    rows = []

    def add(agent: str, n: int, chars: int) -> None:
        spec = AGENTS[agent]
        t = tiers[spec.tier]
        sp = len(system_prompt(spec, date.today()))
        unit = estimate_cost(t["model"], sp + chars + 200, t["max_tokens"], t.get("web_search_uses", 0))
        rows.append({"agent": agent, "model": t["model"], "calls": n, "usd_per_call": round(unit, 4),
                     "usd_total": round(unit * n, 2)})

    add("triage", n1, sizes["L1"])
    for a in L2_AGENTS:
        add(a, n2, sizes["L2"])
    for a in L3_AGENTS:
        add(a, n3, sizes["L3"])
    add("portfolio_manager", 1, sizes["committee"])
    total = sum(r["usd_total"] for r in rows)
    naive = None
    # comparación: todo el universo con el análisis completo (L2+L3) con el modelo de L3
    t3 = tiers["L3_deep"]
    per_company_full = sum(estimate_cost(t3["model"], sizes["L3"], t3["max_tokens"], t3.get("web_search_uses", 0))
                           for _ in L2_AGENTS + L3_AGENTS)
    naive = round(per_company_full * n_universe, 2)
    return {"rows": rows, "total_usd_upper_bound": round(total, 2), "naive_all_deep_usd": naive,
            "calls": {"L1": n1, "L2": n2, "L3": n3},
            "note": "cota superior: sin reutilización y con todas las capas llenas; el gasto real se registra en agent_runs"}


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(estimate_cycle(), indent=1))
