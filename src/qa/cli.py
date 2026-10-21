"""CLI de Quant Advisory.

  qa init                     crea la base y sincroniza mapa, universo, bottlenecks e investigación semilla
  qa validate                 valida mapa/universo/bottlenecks y muestra cobertura
  qa ingest [--tickers ...]   descarga fundamentales (SEC) y mercado (Yahoo) con trazabilidad
  qa estimate                 costo máximo estimado de un ciclo del embudo
  qa funnel [--tiers ...]     corre el embudo L0..L3 + comité (requiere ANTHROPIC_API_KEY salvo L0/--dry-run)
  qa curate-map / qa discover agentes de mantenimiento del mapa y búsqueda de empresas
  qa proposals [--approve ID] revisar propuestas de cambios al mapa/universo
  qa options TICKER           análisis de calls con cadena actual
  qa portfolio                estado y performance de la cartera simulada
  qa export                   Excel + CSV
  qa demo                     demo offline con datos sintéticos y agentes guionados
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path

from qa.config import DATA_DIR, settings


def _ctx(s, args, llm=None):  # noqa: ANN001, ANN202
    from qa.agents.base import Budget, RunContext
    if llm is None and not getattr(args, "dry_run", False):
        from qa.agents.llm import AnthropicLLM
        llm = AnthropicLLM()
    return RunContext(session=s, llm=llm, budget=Budget.from_settings(s, getattr(args, "budget", None)),
                      reuse=not getattr(args, "no_reuse", False), dry_run=getattr(args, "dry_run", False))


def _load():  # noqa: ANN202
    from qa.bottlenecks import load_bottlenecks
    from qa.map.model import load_map
    from qa.universe.model import load_universe
    m = load_map()
    u = load_universe()
    return m, u, load_bottlenecks(m, u)


def cmd_init(args) -> None:  # noqa: ANN001
    from qa.db import repo
    from qa.db.session import session_scope
    from qa.providers.manual import import_seed_research
    m, u, reg = _load()
    with session_scope() as s:
        repo.sync_map(s, m)
        repo.sync_universe(s, u, m)
        repo.sync_bottlenecks(s, [b.as_dict() for b in reg.values()])
        n = repo.upsert_datapoints(s, import_seed_research())
    print(f"OK: mapa v{m.version} ({len(m.nodes)} nodos), {len(u.companies)} empresas, {len(reg)} bottlenecks, "
          f"{n} datapoints de investigación semilla (search_summary)")


def cmd_validate(args) -> None:  # noqa: ANN001
    from qa.map.model import validate_map
    from qa.universe.model import coverage_report, validate_universe
    m, u, reg = _load()
    errs = validate_map(m) + validate_universe(u, m)
    for b in reg.values():
        errs += [f"bottleneck {b.id}: nodo inexistente {n}" for n in b.map_nodes if n not in m.nodes]
    print(json.dumps(coverage_report(u, m), indent=1, ensure_ascii=False))
    print("errores:", errs or "ninguno")
    sys.exit(1 if errs else 0)


def cmd_ingest(args) -> None:  # noqa: ANN001
    from qa.db.session import session_scope
    from qa.feed.events import detect_new_filings, detect_price_moves
    from qa.providers.registry import IngestReport, ingest_benchmarks, ingest_company
    m, u, reg = _load()
    targets = [u.companies[t] for t in args.tickers] if args.tickers else (
        u.seeds() if args.seeds else list(u.companies.values()))
    rep = IngestReport()
    with session_scope() as s:
        for c in targets:
            ingest_company(s, c, rep)
            s.commit()
        if args.benchmarks:
            ingest_benchmarks(s, rep)
        nf = detect_new_filings(s, date.today() - timedelta(days=30))
        npm = detect_price_moves(s)
    print(f"OK {len(rep.ok)} | con errores {len(rep.failed)} | feed: {nf} filings, {npm} movimientos de precio")
    for cid, errs in list(rep.failed.items())[:40]:
        print(f"  {cid}: {errs}")


def cmd_estimate(args) -> None:  # noqa: ANN001
    from qa.funnel.costs import estimate_cycle
    r = estimate_cycle()
    for row in r["rows"]:
        print(f"  {row['agent']:20} {row['model']:18} {row['calls']:4} x ${row['usd_per_call']:.4f} = ${row['usd_total']:.2f}")
    print(f"Cota superior por ciclo: ${r['total_usd_upper_bound']:.2f}  (vs ${r['naive_all_deep_usd']:.0f} analizando todo "
          f"el universo en profundidad). {r['note']}")


def cmd_funnel(args) -> None:  # noqa: ANN001
    from qa.db.session import session_scope
    from qa.funnel.pipeline import Pipeline
    m, u, reg = _load()
    tiers = tuple(args.tiers.split(","))
    if tiers != ("L0",) and not args.dry_run and not os.environ.get("ANTHROPIC_API_KEY"):
        print("Falta ANTHROPIC_API_KEY (o usar --tiers L0 / --dry-run).")
        sys.exit(2)
    with session_scope() as s:
        ctx = _ctx(s, args, llm=None if tiers != ("L0",) else object())
        rep = Pipeline(ctx, m, u, reg).run(tiers=tiers, only=args.only.split(",") if args.only else None)
    print(json.dumps({"run_id": rep.run_id, "counts": rep.counts, "cost_usd": rep.cost_usd,
                      "est_cost_usd(dry_run)": rep.est_cost_usd, "decisions": rep.decisions,
                      "constraint_notes": rep.constraint_notes, "notes": rep.notes}, indent=1, ensure_ascii=False))
    print("\nTop L0 (pre-score):")
    for r in rep.l0_table[:25]:
        print(f"  {r['company']:12} pre={r['prescore']:5.2f} pass={r['pass']!s:5} bn={r['bottleneck']:4} "
              f"fq={r['financial_quality']:4} {'; '.join(r['reasons'])[:80]}")


def cmd_curate(args) -> None:  # noqa: ANN001
    from qa.agents.map_agents import run_map_curator
    from qa.db.session import session_scope
    m, u, reg = _load()
    with session_scope() as s:
        ids = run_map_curator(_ctx(s, args), m, reg, u)
    print(f"{len(ids)} propuestas creadas (pendientes de revisión): {ids}")


def cmd_discover(args) -> None:  # noqa: ANN001
    from qa.agents.map_agents import discovery_targets, run_discovery
    from qa.db.session import session_scope
    m, u, reg = _load()
    nodes = args.nodes.split(",") if args.nodes else discovery_targets(m, reg, u, k=args.k)
    print("nodos objetivo:", nodes)
    with session_scope() as s:
        ids = run_discovery(_ctx(s, args), m, reg, u, nodes)
    print(f"{len(ids)} empresas candidatas propuestas: {ids}")


def cmd_proposals(args) -> None:  # noqa: ANN001
    from qa.agents.map_agents import pending, review_proposal
    from qa.db.session import session_scope
    with session_scope() as s:
        if args.approve or args.reject:
            pid = args.approve or args.reject
            p = review_proposal(s, pid, approve=bool(args.approve), reviewer=args.reviewer, note=args.note or "")
            print(f"propuesta {pid}: {p.status}")
            return
        for p in pending(s):
            print(f"#{p.id} [{p.change_type}] {p.rationale[:120]}\n    {json.dumps(p.payload, ensure_ascii=False)[:200]}")


def cmd_options(args) -> None:  # noqa: ANN001
    from sqlalchemy import select
    from qa.db.schema import ScenarioSet as Row
    from qa.db.session import session_scope
    from qa.options.analyzer import analyze_calls
    from qa.providers.base import ProviderUnavailable
    from qa.providers.yahoo import YahooProvider
    from qa.valuation.core import ScenarioSet
    m, u, reg = _load()
    c = u.companies[args.ticker]
    try:
        chain = YahooProvider().chain(c.yahoo_symbol)
    except ProviderUnavailable as e:
        print(f"Sin cadena de opciones verificable: {e}. No se evalúan calls sin datos actuales.")
        sys.exit(2)
    with session_scope() as s:
        row = s.scalars(select(Row).where(Row.company_id == c.id).order_by(Row.created_at.desc())).first()
        ss = ScenarioSet.model_validate(row.results) if row else None
    rep = analyze_calls(chain, ss, r=settings()["risk_free_rate"])
    print(f"{rep.underlying} spot {rep.spot} | cadena {rep.chain_time.isoformat()} ({rep.chain_source}) | {rep.status}")
    print("rechazados:", rep.rejected)
    for cnd in rep.candidates[:15]:
        print(f"  {cnd.expiry} K={cnd.strike:<8} prima={cnd.premium:.2f} BE={cnd.breakeven_move:+.0%} IV={cnd.iv:.0%} "
              f"RN P(0)={cnd.rn['p_total_loss']:.0%} P(2x)={cnd.rn['p_2x']:.0%} | "
              f"subj P(0)={cnd.subj.get('p_total_loss', float('nan')):.0%} P(3x)={cnd.subj.get('p_3x', float('nan')):.0%} "
              f"edge={cnd.edge_vs_stock if cnd.edge_vs_stock is None else round(cnd.edge_vs_stock, 2)} {cnd.flags}")
    print("Notas del modelo:", *rep.model_notes, sep="\n  - ")


def cmd_portfolio(args) -> None:  # noqa: ANN001
    import pandas as pd
    from sqlalchemy import select
    from qa.db import repo
    from qa.db.schema import PortfolioSnapshot, Recommendation
    from qa.db.session import session_scope
    from qa.portfolio import ledger
    from qa.portfolio.metrics import concentration, performance
    name = settings()["portfolio"]["name"]
    with session_scope() as s:
        h = ledger.holdings(s, name)
        print(f"Cartera {name} (SIMULADA) | caja ${ledger.cash(s, name):,.0f} | posiciones: {h}")
        snaps = s.scalars(select(PortfolioSnapshot).where(PortfolioSnapshot.portfolio == name)
                          .order_by(PortfolioSnapshot.date)).all()
        if snaps:
            nav = pd.Series({x.date: x.nav for x in snaps})
            for b in ("SPY", "QQQ"):
                print(b, json.dumps(performance(nav, repo.price_series(s, b) or None), default=str))
            print("concentración", concentration({k: v["weight"] for k, v in snaps[-1].positions.items()}))
        for r in s.scalars(select(Recommendation).order_by(Recommendation.created_at.desc()).limit(20)):
            print(f"  {r.created_at:%Y-%m-%d %H:%M} {r.company_id:10} {r.action:5} w={r.target_weight:.1%} "
                  f"px={r.price_at_decision} ({r.price_source}) [{r.mode}]")


def cmd_export(args) -> None:  # noqa: ANN001
    from qa.db.session import session_scope
    from qa.export.excel import export_all
    with session_scope() as s:
        paths = export_all(s, Path(args.out))
    print("exportado:", paths["xlsx"])


def cmd_demo(args) -> None:  # noqa: ANN001
    from qa.demo import DEMO_DB, run_demo
    p = Path(DEMO_DB.removeprefix("sqlite:///"))
    if p.exists():
        p.unlink()
    r = run_demo()
    print("DEMO con DATOS SINTÉTICOS y agentes GUIONADOS — no es análisis real.")
    print(json.dumps({k: r[k] for k in ("counts", "scores", "decisions", "constraint_notes", "trades")},
                     indent=1, ensure_ascii=False, default=str))


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=os.environ.get("QA_LOG", "WARNING"))
    ap = argparse.ArgumentParser(prog="qa", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("init").set_defaults(fn=cmd_init)
    sp.add_parser("validate").set_defaults(fn=cmd_validate)
    p = sp.add_parser("ingest")
    p.add_argument("--tickers", nargs="*")
    p.add_argument("--seeds", action="store_true", help="solo las 20 empresas semilla")
    p.add_argument("--benchmarks", action="store_true")
    p.set_defaults(fn=cmd_ingest)
    sp.add_parser("estimate").set_defaults(fn=cmd_estimate)
    p = sp.add_parser("funnel")
    p.add_argument("--tiers", default="L0,L1,L2,L3,committee")
    p.add_argument("--only", help="lista de tickers separados por coma")
    p.add_argument("--budget", type=float, help="presupuesto USD de esta corrida")
    p.add_argument("--dry-run", action="store_true", help="no llama al LLM, estima costos")
    p.add_argument("--no-reuse", action="store_true", help="forzar re-análisis aunque el data pack no cambió")
    p.set_defaults(fn=cmd_funnel)
    for name, fn in (("curate-map", cmd_curate), ("discover", cmd_discover)):
        p = sp.add_parser(name)
        p.add_argument("--budget", type=float)
        p.add_argument("--dry-run", action="store_true")
        if name == "discover":
            p.add_argument("--nodes")
            p.add_argument("--k", type=int, default=3)
        p.set_defaults(fn=fn)
    p = sp.add_parser("proposals")
    p.add_argument("--approve", type=int)
    p.add_argument("--reject", type=int)
    p.add_argument("--reviewer", default="user")
    p.add_argument("--note")
    p.set_defaults(fn=cmd_proposals)
    p = sp.add_parser("options")
    p.add_argument("ticker")
    p.set_defaults(fn=cmd_options)
    sp.add_parser("portfolio").set_defaults(fn=cmd_portfolio)
    p = sp.add_parser("export")
    p.add_argument("--out", default=str(DATA_DIR / "exports"))
    p.set_defaults(fn=cmd_export)
    sp.add_parser("demo").set_defaults(fn=cmd_demo)
    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
