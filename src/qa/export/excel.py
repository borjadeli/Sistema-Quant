"""Exportación a Excel/CSV de universo, embudo, scores, escenarios, recomendaciones, trades, bottlenecks y costos."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from qa.db.schema import (AgentRun, Bottleneck, Company, DataPointRow, Disagreement, FunnelState, MapChangeProposal,
                          PaperTrade, PortfolioSnapshot, Recommendation, ScenarioSet, ScoreRow)

TABLES = {
    "universe": Company, "funnel": FunnelState, "scores": ScoreRow, "scenarios": ScenarioSet,
    "recommendations": Recommendation, "paper_trades": PaperTrade, "portfolio": PortfolioSnapshot,
    "bottlenecks": Bottleneck, "disagreements": Disagreement, "map_proposals": MapChangeProposal,
    "agent_runs": AgentRun, "datapoints": DataPointRow,
}


def table_df(s: Session, model) -> pd.DataFrame:  # noqa: ANN001
    rows = s.scalars(select(model)).all()
    cols = [c.name for c in model.__table__.columns]
    data = []
    for r in rows:
        d = {}
        for c in cols:
            v = getattr(r, c)
            if isinstance(v, (dict, list)):
                v = str(v)
            if hasattr(v, "tzinfo") and getattr(v, "tzinfo", None) is not None:
                v = v.replace(tzinfo=None)
            d[c] = v
        data.append(d)
    return pd.DataFrame(data, columns=cols)


def export_all(s: Session, out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    xlsx = out_dir / "quant_advisory_export.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl") as xw:
        for name, model in TABLES.items():
            df = table_df(s, model)
            df.to_csv(out_dir / f"{name}.csv", index=False)
            paths[name] = out_dir / f"{name}.csv"
            df.head(1_000_000).to_excel(xw, sheet_name=name[:31], index=False)
    paths["xlsx"] = xlsx
    return paths
