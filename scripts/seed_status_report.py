"""Genera docs/07_semillas_estado.md a partir de data/research/seed_companies_verified.json (sin juicios de inversión)."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
rows = json.loads((ROOT / "data/research/seed_companies_verified.json").read_text(encoding="utf-8"))
out = ["# 07 — Estado de investigación de las 20 empresas semilla", "",
       "Generado por `scripts/seed_status_report.py`. **No es una recomendación.** Las cifras salieron de resúmenes de",
       "buscador (`search_summary`) con la URL de la fuente; hay que verificarlas contra el documento primario. El",
       "sistema todavía no corrió escenarios ni scoring reales sobre estas empresas: faltan datos de mercado y la API key.", "",
       "| Ticker | Último trimestre | Ingresos | Resultado neto GAAP | Fuente | Relaciones confirmadas | Red flags |",
       "|---|---|---|---|---|---:|---:|"]
details = []
for r in rows:
    q = r.get("latest_reported_quarter") or {}
    rev = q.get("revenue")
    unit = (q.get("revenue_unit") or "").split(" (")[0]
    ni = q.get("gaap_net_income")
    src = q.get("source_url")
    out.append(f"| {r['ticker']} | {q.get('period_label') or '—'} | {f'{rev:,} {unit}' if isinstance(rev, (int, float)) else '—'} | "
               f"{f'{ni:,}' if isinstance(ni, (int, float)) else '—'} | {f'[link]({src})' if src else 'sin investigar'} | "
               f"{len(r.get('confirmed_relationships') or [])} | {len(r.get('red_flags') or [])} |")
    if not str(r.get("data_quality", "")).startswith("none"):
        details.append(f"\n## {r['ticker']} — {r.get('name')}\n")
        details.append(f"- **Vínculo IA/energía/commodities:** {r.get('ai_energy_commodity_link')}")
        for f in (r.get("red_flags") or [])[:5]:
            txt = f if isinstance(f, str) else (f.get("flag") or f.get("description") or json.dumps(f, ensure_ascii=False))
            url = None if isinstance(f, str) else f.get("source_url")
            details.append(f"- ⚠️ {txt}" + (f" ([fuente]({url}))" if url else ""))
        for e in (r.get("key_recent_events") or [])[:4]:
            txt = e if isinstance(e, str) else f"{e.get('date', '')}: {e.get('event') or e.get('description') or ''}"
            url = None if isinstance(e, str) else e.get("source_url")
            details.append(f"- 📅 {txt}" + (f" ([fuente]({url}))" if url else ""))
missing = [r["ticker"] for r in rows if str(r.get("data_quality", "")).startswith("none")]
out += ["", f"**Sin investigar (se agotó el cupo de búsquedas):** {', '.join(missing)}. Quedan pendientes de `qa ingest` "
        "y de la próxima ronda de investigación."]
out += details
(ROOT / "docs/07_semillas_estado.md").write_text("\n".join(out) + "\n", encoding="utf-8")
print("ok", len(rows), "missing", missing)
