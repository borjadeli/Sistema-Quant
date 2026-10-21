from datetime import date

from qa.db import repo
from qa.fundamentals.metrics import compute_fundamentals
from qa.provenance import DataKind, DataPoint, Source, Verification, point_in_time, stable_hash
from qa.providers.manual import FixtureProvider, import_seed_research
from qa.providers.sec_edgar import parse_companyfacts
from qa.universe.model import Company, Exposure


def _row(val, start, end, filed, form="10-Q", fy=2025, fp="Q1"):
    return {"val": val, "start": start, "end": end, "filed": filed, "form": form, "fy": fy, "fp": fp, "accn": "0001-25-1"}


def test_sec_parser_keeps_first_filing_and_priority():
    raw = {"facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [
            _row(100, "2025-01-01", "2025-03-31", "2025-05-01"),
            _row(105, "2025-01-01", "2025-03-31", "2026-05-01"),           # comparativo posterior: se ignora
            _row(400, "2025-01-01", "2025-12-31", "2026-02-15", "10-K", 2025, "FY")]}},
        "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
            _row(99, "2025-01-01", "2025-03-31", "2025-05-01")]}},           # menor prioridad
        "CashAndCashEquivalentsAtCarryingValue": {"units": {"USD": [
            {"val": 50, "end": "2025-03-31", "filed": "2025-05-01", "form": "10-Q", "fy": 2025, "fp": "Q1"}]}}}}}
    pts = parse_companyfacts(raw, "X", cik=1)
    q = [p for p in pts if p.metric == "revenue" and p.period_type == "Q"]
    assert len(q) == 1 and q[0].value == 100 and q[0].known_at == date(2025, 5, 1)
    assert any(p.metric == "revenue" and p.period_type == "FY" and p.value == 400 for p in pts)
    cash = [p for p in pts if p.metric == "cash"]
    assert cash and cash[0].period_type == "instant"
    assert all(p.verification == Verification.CONFIRMED_FILING and p.source.url for p in pts)


def test_point_in_time_excludes_unknown_dates():
    src = Source(name="t")
    a = DataPoint(entity="X", metric="m", value=1, source=src, known_at=date(2025, 1, 1))
    b = DataPoint(entity="X", metric="m", value=2, source=src, known_at=date(2026, 1, 1))
    c = DataPoint(entity="X", metric="m", value=3, source=src, known_at=None)
    assert point_in_time([a, b, c], date(2025, 6, 1)) == [a]


def test_stable_hash_deterministic():
    assert stable_hash({"a": 1, "b": [1, 2]}) == stable_hash({"b": [1, 2], "a": 1})


def _fixture(cid="TEST_ALPHA"):
    c = Company(ticker=cid, name="t", exchange="NASDAQ", country="US", currency="USD", nodes=["gpu"], exposure=Exposure())
    return FixtureProvider().get_facts(c)


def test_fundamentals_ttm_and_quality():
    f = compute_fundamentals("TEST_ALPHA", _fixture())
    assert f.data_quality == "synthetic"
    assert f.v("revenue_ttm") > 0 and 0.30 < f.v("revenue_growth_yoy") < 0.40   # 8% trimestral ~ 36% anual
    assert abs(f.v("gross_margin") - 0.55) < 1e-6
    assert f.v("fcf_ttm") == f.v("ocf_ttm") - f.v("capex_ttm")
    assert f.missing == []


def test_fundamentals_point_in_time():
    pts = _fixture()
    early = compute_fundamentals("TEST_ALPHA", pts, as_of=date(2024, 6, 1))
    late = compute_fundamentals("TEST_ALPHA", pts)
    assert early.v("revenue_ttm") is None or early.v("revenue_ttm") < late.v("revenue_ttm")


def test_q4_derived_from_fy_but_not_for_shares():
    src = Source(name="t")

    def dp(metric, v, end, ptype):
        return DataPoint(entity="X", metric=metric, value=v, period_end=date.fromisoformat(end), period_type=ptype,
                         source=src, known_at=date(2026, 3, 1))
    pts = [dp("revenue", 10, "2025-03-31", "Q"), dp("revenue", 10, "2025-06-30", "Q"), dp("revenue", 10, "2025-09-30", "Q"),
           dp("revenue", 50, "2025-12-31", "FY")]
    pts += [dp("diluted_shares", 100, "2025-03-31", "Q"), dp("diluted_shares", 100, "2025-12-31", "FY")]
    f = compute_fundamentals("X", pts, as_of=date(2026, 6, 1))
    assert f.quarterly["revenue"][date(2025, 12, 31)] == 20
    assert f.v("revenue_ttm") == 50
    assert date(2025, 12, 31) not in f.quarterly.get("diluted_shares", {})


def test_seed_import_only_sourced_numbers():
    pts = import_seed_research()
    assert pts, "seed research should yield datapoints"
    for p in pts:
        assert p.source.url and p.verification == Verification.SEARCH_SUMMARY and p.kind == DataKind.ORIGINAL


def test_datapoint_roundtrip(session):
    pts = _fixture()
    repo.upsert_datapoints(session, pts)
    repo.upsert_datapoints(session, pts)              # idempotente
    back = repo.load_datapoints(session, "TEST_ALPHA")
    assert len(back) == len(pts)
