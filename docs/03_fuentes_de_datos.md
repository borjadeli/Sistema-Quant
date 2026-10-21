# 03 — Fuentes de datos

| Necesidad | Fuente | Costo | Estado en v0.1 | Notas |
|---|---|---|---|---|
| Estados financieros EE.UU. (10-K/10-Q, y 20-F/40-F de extranjeros con XBRL) | **SEC EDGAR XBRL** (`data.sec.gov/api/xbrl/companyfacts`) | gratis | implementado y testeado con fixture; **no ejecutado** (red bloqueada en el contenedor) | requiere User-Agent con email (`QA_SEC_USER_AGENT`), ≤10 req/s |
| Ticker → CIK | `www.sec.gov/files/company_tickers.json` | gratis | implementado | |
| Precios, market cap, liquidez, FX | **yfinance** | gratis, no oficial | implementado; **no ejecutado** | sufijos .T .TW .TO .L .DE…; los precios GBp se convierten |
| Cadenas de opciones | yfinance | gratis, con retraso | implementado; se exige cadena con < 24 h | reemplazar por IBKR/Polygon para cotizaciones en tiempo real |
| Emisores fuera de EDGAR (TSX/SEDAR+, TSE, TWSE, KRX) | **CSV manual con fuente obligatoria** (`data/manual/*.csv`) | — | implementado | se rechaza cualquier fila sin URL o sin `known_at` |
| Evidencia de bottlenecks / investigación cualitativa | agentes con búsqueda web (Claude `web_search`) | ~US$0.01/búsqueda | 21 bottlenecks y 20 semillas investigadas (calidad `search_summary`) | |
| Interactive Brokers | conector MCP disponible, **requiere autorización** | según cuenta | no conectado | autorizarlo en la configuración de conectores de claude.ai; solo lectura (regla 14) |
| Alternativas pagas | FMP, Polygon, Koyfin, S&P CapIQ | pago | interfaz preparada (`providers/base.py`) | agregar una clase y registrarla en `providers/registry.py` |

## Para habilitar los datos reales
1. **Si corrés en tu máquina:** no hace falta nada más que `export QA_SEC_USER_AGENT="Nombre email@dominio"`.
2. **Si corrés en este entorno cloud:** en la configuración de red del entorno, agregá a *Allowed domains*:
   `data.sec.gov`, `www.sec.gov`, `query1.finance.yahoo.com`, `query2.finance.yahoo.com`, `fc.yahoo.com`.
3. Después:
   ```bash
   qa init
   qa ingest --seeds --benchmarks
   qa ingest
   qa funnel --tiers L0
   ```
   Primero las 20 semillas y los benchmarks; después todo el universo; al final el screen gratuito.
4. Para L1+ hace falta `ANTHROPIC_API_KEY`. Antes de gastar, `qa estimate` y `qa funnel --dry-run`.

## Limitaciones conocidas
- La cobertura XBRL de emisores extranjeros (20-F con IFRS) es parcial. La moneda de reporte puede diferir de la
  de cotización: en ese caso el sistema **no valúa** hasta que exista un FX explícito (no se asume).
- ADRs: el ratio ADR/acción subyacente (`adr_ratio`) no se obtiene automáticamente todavía.
- yfinance puede cambiar o bloquearse; por eso todo queda etiquetado con fuente y timestamp.
- Las cifras de `seed_companies_verified.json` y `bottlenecks_evidence.json` salieron de resúmenes de buscador.
  Antes de operar, el auditor debe contrastarlas con los documentos primarios.
