"""Genera data/fixtures/synthetic_companies.json — DATOS SINTÉTICOS para tests/demo. No son empresas reales."""
import json
from datetime import date, timedelta
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "synthetic_companies.json"


def quarter_ends(n, last=date(2026, 6, 30)):
    ends, y, m = [], last.year, last.month
    for _ in range(n):
        d = date(y, m, 30 if m in (6, 9) else 31)
        ends.append(d)
        m -= 3
        if m <= 0:
            m += 12
            y -= 1
    return list(reversed(ends))


def company(rev0, g_q, gm, om, burn=False, shares0=100e6, dil_q=0.002, cash0=300e6, debt=100e6):
    facts, rev, shares, cash = [], rev0, shares0, cash0
    for d in quarter_ends(12):
        known = (d + timedelta(days=40)).isoformat()
        oi = rev * om
        ocf = oi * 1.1 + rev * 0.03
        capex = rev * (0.25 if burn else 0.05)
        cash += ocf - capex
        for metric, val in [("revenue", rev), ("gross_profit", rev * gm), ("operating_income", oi),
                            ("net_income", oi * 0.8), ("d_and_a", rev * 0.04), ("ocf", ocf), ("capex", capex),
                            ("sbc", rev * 0.04), ("diluted_shares", shares), ("income_tax", max(oi, 0) * 0.2),
                            ("pretax_income", oi), ("interest_expense", debt * 0.06 / 4)]:
            facts.append({"metric": metric, "value": round(val, 2), "period_end": d.isoformat(),
                          "period_type": "Q", "known_at": known})
        for metric, val in [("cash", cash), ("debt_total", debt), ("equity", 600e6 + cash - cash0),
                            ("current_assets", cash + rev), ("current_liabilities", rev * 0.6)]:
            facts.append({"metric": metric, "value": round(val, 2), "period_end": d.isoformat(),
                          "period_type": "instant", "known_at": known})
        rev *= 1 + g_q
        shares *= 1 + dil_q
    return {"facts": facts}


data = {
    "_warning": "SYNTHETIC TEST DATA — NOT REAL COMPANIES — DO NOT USE FOR DECISIONS",
    "companies": {
        "TEST_ALPHA": company(100e6, 0.08, 0.55, 0.22),
        "TEST_BURN": company(20e6, 0.15, 0.30, -0.60, burn=True, cash0=60e6, dil_q=0.03, debt=50e6),
        "TEST_SLOW": company(500e6, 0.005, 0.25, 0.08, debt=900e6),
    },
    "market": {
        "TEST_ALPHA": {"price": 40.0, "shares": 103e6, "adv_usd_20d": 25e6},
        "TEST_BURN": {"price": 5.0, "shares": 140e6, "adv_usd_20d": 1.5e6},
        "TEST_SLOW": {"price": 18.0, "shares": 100e6, "adv_usd_20d": 8e6},
    },
}
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(data, indent=1))
print("wrote", OUT)
