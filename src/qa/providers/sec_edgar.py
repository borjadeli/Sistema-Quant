"""SEC EDGAR XBRL (fuente oficial, gratuita).

Endpoints:
  https://www.sec.gov/files/company_tickers.json            ticker -> CIK
  https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json

Reglas de la SEC: User-Agent identificable y <= 10 requests/s. Configurar `QA_SEC_USER_AGENT`.
Cobertura: emisores que presentan XBRL a la SEC (10-K/10-Q; y 20-F/40-F de extranjeros, que suelen usar
taxonomía IFRS). No cubre emisores solo-SEDAR/TSE/TWSE: para esos se usa `manual_csv` u otro proveedor.

Point-in-time: cada hecho conserva `filed` como `known_at`. Si un mismo período aparece en varias
presentaciones (comparativos, re-expresiones), se conserva la PRIMERA presentación: es lo que el mercado
sabía en ese momento. Las re-expresiones posteriores se registran como nota.
"""
from __future__ import annotations

import time
from datetime import date, datetime, timezone
from typing import Any

import requests

from qa.config import settings
from qa.provenance import DataKind, DataPoint, Source, Verification
from qa.providers.base import ProviderUnavailable
from qa.universe.model import Company

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

# métrica interna -> conceptos XBRL en orden de preferencia
US_GAAP: dict[str, list[str]] = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet"],
    "cost_of_revenue": ["CostOfRevenue", "CostOfGoodsAndServicesSold"],
    "gross_profit": ["GrossProfit"],
    "rd_expense": ["ResearchAndDevelopmentExpense"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "interest_expense": ["InterestExpense", "InterestExpenseNonoperating"],
    "income_tax": ["IncomeTaxExpenseBenefit"],
    "pretax_income": ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                      "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"],
    "d_and_a": ["DepreciationDepletionAndAmortization", "DepreciationAmortizationAndAccretionNet",
                "DepreciationAndAmortization"],
    "sbc": ["ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"],
    "ocf": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue"],
    "short_term_investments": ["ShortTermInvestments", "MarketableSecuritiesCurrent",
                               "AvailableForSaleSecuritiesDebtSecuritiesCurrent"],
    "debt_total": ["LongTermDebt", "DebtInstrumentCarryingAmount"],
    "debt_noncurrent": ["LongTermDebtNoncurrent", "ConvertibleNotesPayableNoncurrent"],
    "debt_current": ["LongTermDebtCurrent", "DebtCurrent", "ConvertibleNotesPayableCurrent"],
    "equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "total_assets": ["Assets"],
    "current_assets": ["AssetsCurrent"],
    "current_liabilities": ["LiabilitiesCurrent"],
    "inventory": ["InventoryNet"],
    "receivables": ["AccountsReceivableNetCurrent"],
    "diluted_shares": ["WeightedAverageNumberOfDilutedSharesOutstanding"],
    "rpo": ["RevenueRemainingPerformanceObligation"],
}
IFRS: dict[str, list[str]] = {
    "revenue": ["Revenue", "RevenueFromContractsWithCustomers"],
    "cost_of_revenue": ["CostOfSales"],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["ProfitLossFromOperatingActivities"],
    "net_income": ["ProfitLossAttributableToOwnersOfParent", "ProfitLoss"],
    "ocf": ["CashFlowsFromUsedInOperatingActivities"],
    "capex": ["PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"],
    "d_and_a": ["DepreciationAndAmortisationExpense"],
    "cash": ["CashAndCashEquivalents"],
    "equity": ["EquityAttributableToOwnersOfParent", "Equity"],
    "total_assets": ["Assets"],
    "current_assets": ["CurrentAssets"],
    "current_liabilities": ["CurrentLiabilities"],
    "debt_noncurrent": ["NoncurrentPortionOfNoncurrentBorrowings", "LongtermBorrowings"],
    "debt_current": ["CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings", "ShorttermBorrowings"],
}
DEI = {"shares_outstanding": ["EntityCommonStockSharesOutstanding"]}
INSTANT_METRICS = {"cash", "short_term_investments", "debt_total", "debt_noncurrent", "debt_current", "equity",
                   "total_assets", "current_assets", "current_liabilities", "inventory", "receivables", "rpo",
                   "shares_outstanding"}
VALID_FORMS = {"10-K", "10-Q", "10-K/A", "10-Q/A", "20-F", "20-F/A", "40-F", "40-F/A", "6-K", "8-K"}


def _period_type(start: str | None, end: str) -> str | None:
    if start is None:
        return "instant"
    days = (date.fromisoformat(end) - date.fromisoformat(start)).days
    if 80 <= days <= 100:
        return "Q"
    if 350 <= days <= 380:
        return "FY"
    if 170 <= days <= 190:
        return "H"
    if 260 <= days <= 280:
        return "9M"
    return None


class SecEdgarProvider:
    name = "sec_edgar"

    def __init__(self, user_agent: str | None = None, session: requests.Session | None = None):
        self.user_agent = user_agent or settings()["providers"]["sec_user_agent"]
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"})
        self.timeout = settings()["providers"].get("request_timeout_s", 30)
        self._tickers: dict[str, int] | None = None
        self._last_call = 0.0

    # ------------------------------------------------------------------ http
    def _get(self, url: str) -> Any:
        wait = 0.12 - (time.time() - self._last_call)       # <= ~8 req/s
        if wait > 0:
            time.sleep(wait)
        try:
            r = self.session.get(url, timeout=self.timeout)
            self._last_call = time.time()
        except requests.RequestException as e:
            raise ProviderUnavailable(f"SEC no accesible: {e}") from e
        if r.status_code == 404:
            raise ProviderUnavailable(f"SEC 404: {url}")
        if r.status_code != 200:
            raise ProviderUnavailable(f"SEC HTTP {r.status_code}: {url}")
        return r.json()

    def cik_for(self, ticker: str) -> int | None:
        if self._tickers is None:
            data = self._get(TICKERS_URL)
            self._tickers = {v["ticker"].upper(): int(v["cik_str"]) for v in data.values()}
        return self._tickers.get(ticker.upper().replace(".", "-"))

    def supports(self, company: Company) -> bool:
        return company.is_us_listed

    # ------------------------------------------------------------------ parsing
    def get_facts(self, company: Company) -> list[DataPoint]:
        cik = self.cik_for(company.ticker)
        if cik is None:
            raise ProviderUnavailable(f"{company.ticker}: sin CIK en SEC (¿no presenta en EDGAR?)")
        raw = self._get(FACTS_URL.format(cik=cik))
        return parse_companyfacts(raw, company.id, cik)


def parse_companyfacts(raw: dict, entity: str, cik: int | None = None) -> list[DataPoint]:
    """Convierte el JSON companyfacts en DataPoints ORIGINALES con trazabilidad completa."""
    facts = raw.get("facts", {})
    out: dict[tuple, DataPoint] = {}
    plans = [("us-gaap", US_GAAP), ("ifrs-full", IFRS), ("dei", DEI)]
    for taxonomy, mapping in plans:
        tx = facts.get(taxonomy)
        if not tx:
            continue
        for metric, concepts in mapping.items():
            for rank, concept in enumerate(concepts):
                node = tx.get(concept)
                if not node:
                    continue
                for unit, rows in node.get("units", {}).items():
                    for row in rows:
                        form = row.get("form", "")
                        if form not in VALID_FORMS:
                            continue
                        ptype = _period_type(row.get("start"), row["end"])
                        if ptype is None or (metric in INSTANT_METRICS) != (ptype == "instant"):
                            continue
                        key = (metric, ptype, row["end"])
                        filed = date.fromisoformat(row["filed"])
                        prev = out.get(key)
                        # preferimos el concepto de mayor prioridad; dentro de él, la primera presentación
                        if prev is not None:
                            prev_rank = int(prev.note.split("rank=")[1].split(";")[0]) if prev.note else 99
                            if prev_rank < rank or (prev_rank == rank and prev.known_at <= filed):
                                continue
                        currency = unit if len(unit) == 3 and unit.isupper() else None
                        accn = row.get("accn")
                        url = (f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type={form}"
                               if cik else None)
                        if accn and cik:
                            url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accn.replace('-', '')}/"
                        out[key] = DataPoint(
                            entity=entity, metric=metric, value=float(row["val"]),
                            unit=unit, currency=currency,
                            period_end=date.fromisoformat(row["end"]), period_type=ptype,
                            fiscal_label=f"{row.get('fy')}-{row.get('fp')}" if row.get("fy") else None,
                            kind=DataKind.ORIGINAL, verification=Verification.CONFIRMED_FILING,
                            source=Source(name="sec_edgar", url=url, document_type=form,
                                          published_at=filed, retrieved_at=datetime.now(timezone.utc)),
                            known_at=filed,
                            note=f"rank={rank};concept={taxonomy}:{concept};accn={accn}",
                        )
    return sorted(out.values(), key=lambda p: (p.metric, p.period_end or date.min))
