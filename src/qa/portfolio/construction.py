"""Construcción de cartera: hace cumplir restricciones sobre los pesos propuestos por el Portfolio Manager.

El PM (LLM) propone; el código dispone. Ninguna restricción depende de que el modelo la respete.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from qa.config import settings

SPECULATIVE_MAX = 0.03


@dataclass
class PositionMeta:
    company_id: str
    layer: str
    primary_bottleneck: str | None
    eligible: bool
    speculative: bool = False
    kill_reasons: list[str] = field(default_factory=list)


def enforce_constraints(targets: dict[str, float], meta: dict[str, PositionMeta], cfg: dict | None = None,
                        notes_by: dict[str, list[str]] | None = None) -> tuple[dict[str, float], list[str]]:
    """`notes_by` (opcional) recibe, por empresa, TODAS las restricciones que cambiaron su peso (incluidas las
    de grupo: capa, bottleneck, caja mínima), para registrarlas en su recomendación."""
    cfg = cfg or settings()["portfolio"]
    notes: list[str] = []
    by = notes_by if notes_by is not None else {}

    def note(cid: str, txt: str) -> None:
        notes.append(f"{cid}: {txt}")
        by.setdefault(cid, []).append(f"{cid}: {txt}")

    w = {k: max(0.0, float(v)) for k, v in targets.items() if v and v > 0}
    for cid in list(w):
        m = meta.get(cid)
        if m is None:
            note(cid, "sin dossier -> 0")
            w.pop(cid)
        elif not m.eligible:
            note(cid, f"kill switch ({'; '.join(m.kill_reasons)}) -> 0")
            w.pop(cid)
        elif m.speculative and w[cid] > SPECULATIVE_MAX:
            note(cid, f"especulativa, {w[cid]:.1%} -> {SPECULATIVE_MAX:.0%}")
            w[cid] = SPECULATIVE_MAX
    for cid in list(w):
        if w[cid] > cfg["max_weight"]:
            note(cid, f"{w[cid]:.1%} -> tope {cfg['max_weight']:.0%}")
            w[cid] = cfg["max_weight"]
    if len(w) > cfg["max_positions"]:
        keep = sorted(w, key=w.get, reverse=True)[: cfg["max_positions"]]
        for cid in w:
            if cid not in keep:
                note(cid, f"fuera por máximo de {cfg['max_positions']} posiciones")
        w = {k: w[k] for k in keep}
    for group_key, cap in (("layer", cfg["max_weight_per_layer"]), ("primary_bottleneck", cfg["max_weight_per_bottleneck"])):
        groups: dict[str, list[str]] = {}
        for cid in w:
            g = getattr(meta[cid], group_key)
            if g:
                groups.setdefault(g, []).append(cid)
        for g, members in groups.items():
            tot = sum(w[c] for c in members)
            if tot > cap + 1e-9:
                f = cap / tot
                txt = f"{group_key}={g}: {tot:.1%} -> {cap:.0%} (escalado proporcional)"
                for c in members:
                    w[c] *= f
                    by.setdefault(c, []).append(txt)
                notes.append(txt)
    invest_max = 1 - cfg["min_cash"]
    tot = sum(w.values())
    if tot > invest_max:
        f = invest_max / tot
        w = {k: v * f for k, v in w.items()}
        txt = f"total {tot:.1%} -> {invest_max:.0%} (caja mínima)"
        notes.append(txt)
        for c in w:
            by.setdefault(c, []).append(txt)
    if 0 < len(w) < cfg["min_positions"]:
        notes.append(f"solo {len(w)} posiciones (< mínimo {cfg['min_positions']}): se mantiene el resto en caja")
    return {k: round(v, 4) for k, v in w.items()}, notes
