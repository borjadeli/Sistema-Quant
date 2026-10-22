"""SEC EDGAR XBRL (fuente oficial, gratuita).

Endpoints:
  https://www.sec.gov/files/company_tickers.json            ticker -> CIK
  https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json

Reglas de la SEC: User-Agent identificable y <= 10 requests/s. Configurar `QA_SEC_USER_AGENT`.
Cobertura: emisores que presentan XBRL a la SEC (10-K/10-Q; y 20-F/40-F de extranjeros, que suelen usar
taxonomía IFRS). No cubre emisores solo-SEDAR/TSE/TWSE: para esos se usa `manual_csv` u otro proveedor.

Point-in-time por VINTAGES: cada hecho conserva `filed` como `known_at`. Para un mismo período:
  - el primer vintage es la PRIMERA presentación (lo que sabía el mercado), sin importar qué concepto XBRL
    usó; la prioridad de conceptos solo desempata dentro de una misma presentación;
  - cada presentación posterior que CAMBIA el valor (re-expresión, 10-K/A, comparativo corregido) se guarda
    como un vintage nuevo con su propio `known_at` y la nota `restates=...`;
  - comparativos que repiten el mismo valor no generan filas nuevas.
Al leer, `compute_fundamentals` toma el vintage más reciente con known_at <= fecha de decisión.
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


def parse_companyfacts(raw: dict, entity: str, cik: int | None = None,
                       restatement_tolerance: float = 0.001) -> list[DataPoint]:
    """Convierte el JSON companyfacts en DataPoints ORIGINALES (uno por vintage) con trazabilidad completa."""
    facts = raw.get("facts", {})
    # (metric, ptype, end) -> {filed: (rank, row, taxonomy, concept, unit)}
    cands: dict[tuple, dict[date, tuple]] = {}
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
                        if row.get("form", "") not in VALID_FORMS:
                            continue
                        ptype = _period_type(row.get("start"), row["end"])
                        if ptype is None or (metric in INSTANT_METRICS) != (ptype == "instant"):
                            continue
                        filed = date.fromisoformat(row["filed"])
                        slot = cands.setdefault((metric, ptype, row["end"]), {})
                        prev = slot.get(filed)
                        # dentro de UNA presentación desempata la prioridad de concepto
                        if prev is None or rank < prev[0]:
                            slot[filed] = (rank, row, taxonomy, concept, unit)
    out: list[DataPoint] = []
    for (metric, ptype, end), by_filed in cands.items():
        last_val, last_filed = None, None
        for filed in sorted(by_filed):
            rank, row, taxonomy, concept, unit = by_filed[filed]
            val = float(row["val"])
            if last_val is not None:
                denom = max(abs(last_val), 1e-9)
                if abs(val - last_val) / denom <= restatement_tolerance:
                    continue                      # comparativo idéntico: no es un vintage nuevo
            note = f"rank={rank};concept={taxonomy}:{concept};accn={row.get('accn')}"
            if last_val is not None:
                note += f";restates={last_val}@{last_filed}"
            out.append(_datapoint(entity, metric, ptype, end, val, unit, row, filed, cik, note))
            last_val, last_filed = val, filed
    return sorted(out, key=lambda p: (p.metric, p.period_end or date.min, p.known_at or date.min))


def _datapoint(entity: str, metric: str, ptype: str, end: str, val: float, unit: str, row: dict, filed: date,
               cik: int | None, note: str) -> DataPoint:
    form, accn = row.get("form"), row.get("accn")
    url = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type={form}" if cik else None
    if accn and cik:
        url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accn.replace('-', '')}/"
    currency = unit if len(unit) == 3 and unit.isupper() else None
    # La etiqueta fiscal describe el PERÍODO del hecho (frame de la SEC si existe), no la presentación.
    label = row.get("frame") or f"{ptype} al {end}"
    return DataPoint(entity=entity, metric=metric, value=val, unit=unit, currency=currency,
                     period_end=date.fromisoformat(end), period_type=ptype, fiscal_label=label,
                     kind=DataKind.ORIGINAL, verification=Verification.CONFIRMED_FILING,
                     source=Source(name="sec_edgar", url=url, document_type=form, published_at=filed,
                                   retrieved_at=datetime.now(timezone.utc)),
                     known_at=filed, note=note)
