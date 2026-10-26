# Quant Advisory — resumen para auditoría externa

Estado al 26-oct-2026, commit `ac53d8e` (rama `main` de `borjadeli/Sistema-Quant`). Este documento describe qué es
el sistema, qué se construyó, cómo se verificó, qué falta, y dónde conviene auditar primero. Está escrito para que
otra IA (o una persona) lo audite sin contexto previo.

---

## 1. Qué es y qué reglas tiene

Sistema de investigación de inversiones **multiagente, sistemático y trazable**, centrado en los cuellos de botella
físicos de la IA: cómputo, fotónica, energía, red eléctrica y materiales críticos. Busca empresas (sobre todo de
nicho) cuyo potencial económico el mercado subestima.

Funciona como las carteras de AI Finance Labs: **el modelo elige, compra y vende en una cartera SIMULADA**, y el
usuario replica las operaciones a mano. Inspirado en el trabajo de Alejandro López-Lira.

Reglas que el usuario fijó y que el código debe cumplir (auditar que se cumplan):
1. No inventar datos financieros, relaciones comerciales, fuentes, citas ni documentos.
2. No tratar declaraciones de la empresa como hechos verificados.
3. No usar precios históricos como actuales.
4. No ocultar evidencia contraria.
5. No presentar probabilidades subjetivas como estadísticas.
6. Trazabilidad completa (fuente, fecha, tipo de dato) de todo número.
7. Diseño sustituible de modelos y proveedores.
8. **No enviar órdenes ni conectar trading real sin autorización explícita** (`live_trading_enabled: false` y bloqueo
   en código, `portfolio/ledger.py: assert_simulated`).
9. Costo de análisis lo más bajo posible: filtros y capas (embudo), más análisis donde más puede cambiar la decisión.

## 2. Números del sistema

| Elemento | Valor |
|---|---|
| Mapa industrial | 12 capas, 142 nodos, 225 dependencias (`data/map/industry_map.yaml`) |
| Universo | 245 empresas por función industrial; 20 semillas (`data/universe/companies.yaml`) |
| Cuellos de botella | 21, con evidencia, contra-evidencia y severidad por regla (`data/bottlenecks/`, `data/research/bottlenecks_evidence.json`) |
| Agentes | 12 (Claude): Triage, Industrial, Fundamental, Catalyst, Valuation, Expectations, Bear Case, Valuation rebuttal, Research Auditor, Portfolio Manager, Map Curator, Discovery |
| Código | ~6.800 líneas en `src/qa`, ~2.100 en `tests`, ~2.100 en `dashboard` (Python 3.13) |
| Tests | 154 (pytest), todos pasan |
| Persistencia | SQLAlchemy 2 + SQLite, migración liviana automática |
| Dashboard | Streamlit, 8 páginas, solo lectura |

## 3. Arquitectura (flujo)

```
datos  ──►  datapoints con procedencia  ──►  fundamentales point-in-time  ──►  embudo de agentes  ──►  comité  ──►  cartera simulada
(SEC, Yahoo,            (provenance.py,            (fundamentals/metrics.py)      (funnel/pipeline.py)    (PM + reglas   (portfolio/ledger.py)
 CSV manual,             db/repo.py)                                                                       de código)
 semillas)
```

Módulos (`src/qa/`):
- `provenance.py`: `DataPoint` con fuente, `known_at` (cuándo se hizo público), verificación y tipo
  (original / derivado / estimación / interpretación).
- `providers/`: `sec_edgar.py` (XBRL companyfacts, vintages), `yahoo.py` (precios, snapshot, opciones, FX),
  `manual.py` (CSV manual con fuente obligatoria, investigación semilla, fixtures sintéticos), `registry.py`
  (ingesta, reconciliación, FX explícito, avisos de huecos).
- `db/`: esquema, sesión y migración, repositorio (upsert por vintage, reconciliación por proveedor).
- `fundamentals/metrics.py`: selección point-in-time, normalización de unidades, moneda de reporte, trimestres
  reconstruidos desde YTD, TTM, balance a UNA fecha, deuda, ROIC, dilución, etc.
- `valuation/core.py`: múltiplos, escenarios bear/base/bull a 12/24/36 meses (con financiamiento y dilución),
  DCF, reverse DCF, sensibilidad.
- `scoring/engine.py`: 6 factores con rúbricas (importancia del cuello de botella 20%, captura económica 20%,
  asimetría de valuación 25%, catalizadores 15%, calidad financiera 10%, ventaja informacional 10%), topes y kill
  switches K1–K9.
- `bottlenecks.py`: registro, severidad por regla, vista point-in-time.
- `agents/`: cliente LLM (`llm.py`), esquemas de salida (`schemas.py`), base (reutilización por hash,
  presupuesto, registro de cada llamada), agentes del mapa (`map_agents.py`), prompts (`prompts/*.md`).
- `funnel/`: data packs (`pack.py`), pipeline L0→L3→comité (`pipeline.py`), estimación de costos (`costs.py`).
- `options/analyzer.py`: calls con probabilidades risk-neutral (Black-Scholes) y por escenarios, siempre contra
  comprar la acción, con costo de financiamiento.
- `portfolio/`: restricciones (`construction.py`), libro de operaciones append-only (`ledger.py`), métricas y
  calibración Brier (`metrics.py`).
- `feed/events.py`: research feed (filings, re-expresiones, movimientos de precio, huecos de datos).
- `cli.py`: `qa init | validate | ingest | estimate | funnel | curate-map | discover | proposals | options |
  portfolio | export | demo`.

Embudo de costos (`docs/06_embudo_y_costos.md`):
- **L0** reglas, sin costo, todo el universo: filtros duros y pre-score.
- **L1** triage (Haiku 5.5), hasta 80 empresas más la cartera.
- **L2** cinco agentes (Sonnet 5.5), hasta 25 más la cartera.
- **L3** bear → rebuttal de valuación → auditor (Opus 5.5), hasta 8 más la cartera.
- **Comité** Portfolio Manager (Opus 5.5). El PM propone; el código hace cumplir topes, kill switches y caja mínima.
- Promoción por valor de la información (prioridad + 0.3 × incertidumbre); las posiciones en cartera se analizan
  siempre.
- Reutilización por hash del data pack (solo desde la misma fecha de decisión o anterior); presupuesto duro por
  corrida y mensual con cota superior por solicitud; reserva de presupuesto para el comité.

## 4. Decisiones de diseño que conviene auditar

1. **Point-in-time.** Toda decisión en t usa solo datos con `known_at ≤ t`. La SEC se guarda por vintages: la
   primera presentación es lo que sabía el mercado y cada re-expresión es un vintage nuevo. Los replays filtran
   también la evidencia de bottlenecks y la reutilización de análisis.
2. **Fail-closed.** Si falta un insumo crítico, no se inventa ni se asume:
   - sin caja → no se valúa;
   - deuda reciente en otra fecha → K7;
   - análisis incompleto → K9, que impide entrar a cartera.
   Excepción documentada: un filing sin etiqueta de deuda se valúa con deuda SUPUESTA 0 y una alerta.
3. **Separación de capas epistemológicas.** Original, derivado, estimación e interpretación nunca se mezclan; cada
   métrica derivada guarda sus `inputs`.
4. **El LLM interpreta, el código calcula.** Los agentes proponen supuestos (escenarios) y rúbricas; los números
   (escenarios, scores, restricciones, kill switches) los calcula código determinístico.
5. **Probabilidades subjetivas** etiquetadas como tales; la calibración se mide ex post (Brier).
6. **Cartera simulada append-only.** Cada recomendación guarda precio, fuente, timestamp, data cutoff, hash de
   inputs y de configuración. Benchmarks SPY, QQQ y ETFs sectoriales, alineados as-of.

## 5. Cómo se verificó

- **Tests:** 154, incluidos tests de regresión que reproducen cada falla encontrada
  (`tests/test_review_regressions.py`, `tests/test_review_round3.py`).
- **Cinco rondas de revisión adversarial**, cada una revisando también las correcciones de la anterior:

| Ronda | Qué encontró | Ejemplos |
|---|---|---|
| 1 | 48 hallazgos (4 revisores + 2 escépticos por hallazgo) | look-ahead en packs, costos no contabilizados, ventas por omisión |
| 2 | Residuales y regresiones introducidas por los fixes | mezcla de vintages en trimestres YTD, moneda por conteo, EV/FCF con FCF apalancado |
| 3 | 28 hallazgos, 1 crítico | balance anclado en la fecha de portada `dei` (bloqueaba la valuación de todo emisor SEC real) |
| 4 | 5 en fundamentales y 4 en costos | comunicado posterior que pisaba un balance más completo; dry-run sin simular el control de presupuesto |
| 4b | 3 mayores en comité y 2 mayores en datos | banda de no-operación sin chequeo de caja; un CSV con `;` borraba filas manuales |

- **Primera ingesta real (26-oct-2026)** con red habilitada. Corrigió problemas que solo aparecen con datos reales:
  - cambios de concepto XBRL marcados como re-expresiones (de 1.281 eventos a 65 materiales en las semillas);
  - caja con efectivo restringido;
  - FX explícito para emisores que reportan en otra moneda;
  - market cap ausente en Yahoo;
  - tickers de TPEX.

**Importante para el auditor:**
- La ronda 4b se corrigió y se testeó, pero **no tuvo una revisión independiente posterior**.
- **Los agentes nunca corrieron contra la API real de Claude**: todos los tests y el demo usan respuestas
  pregrabadas (`ScriptedLLM`). La calidad real de las salidas de los agentes no está medida.

## 6. Resultados de la primera ingesta real

- **Cobertura:** 245/245 empresas con precio, market cap y liquidez (Yahoo); 152 con fundamentales SEC; el resto no
  presenta en EDGAR.
- **Semillas:** 18 de 19 con datos SEC se pueden valuar. IVN y HPS.A no tienen datos en EDGAR (requieren CSV manual
  con fuente).
- **Huecos de la SEC:** 10 empresas tienen un filing presentado que `companyfacts` todavía no incorporó (RMBS, AMKR,
  CDNS, SMTC, CARR, JCI, DLR, HUBB, TSM, BHP). Se avisa y se usa el último balance. TSM queda bloqueada (K4) porque
  su último balance utilizable es de dic-2024.
- **Screen L0:** pasan 243/245 y se promueven 80. Arriba quedan MU, FN, MYRG, GEV, CRDO, JBL, PRIM, AMKR, CMI y
  HUBB.
- **Dry-run del primer ciclo:** ~US$46 con todas las capas llenas. Con el presupuesto por defecto (US$30 por corrida)
  se saltarían 18 llamadas de L3.

## 7. Limitaciones conocidas y riesgos abiertos

1. **Agentes sin evaluar** con el modelo real (ver §5). Tampoco hay evals de calidad por capa: la elección de
   modelos es un supuesto de costo, no algo medido.
2. **Investigación de semillas y evidencia de bottlenecks** leídas en resúmenes de buscador
   (`verification=search_summary`), no en documentos primarios.
3. **SEC companyfacts** con huecos (ver §6); no hay todavía un parser del XBRL del filing individual como respaldo.
4. **Yahoo (yfinance)** es no oficial: responde 401 a parte de su API desde la IP del entorno y aplica límites de
   tasa. El market cap se calcula como precio × acciones.
5. **Emisores fuera de EDGAR** (Japón, Taiwán, Corea, Canadá) sin fundamentales salvo CSV manual.
6. **IBKR** conectado como conector de Claude, pero **no integrado al código** (solo lectura prevista; nunca órdenes).
7. **ADRs:** el ratio ADR/acción no se obtiene automáticamente.
8. **Sin backtest**, por diseño: los backtests con LLM sufren memorización y look-ahead. La evaluación es solo
   forward, en paper trading.
9. **Alpha multifactor** (Fama-French) pendiente; hoy solo alpha CAPM contra benchmark.

## 8. Guía para auditar

### Instalar y correr
```bash
pip install -e ".[dev,dashboard,market]"
pytest -q                       # 154 tests, sin red ni API
qa demo                         # ciclo completo con datos SINTÉTICOS (TEST_*) y respuestas pregrabadas
streamlit run dashboard/app.py  # dashboard (base demo o real)
# con red (SEC/Yahoo):
export QA_SEC_USER_AGENT="Nombre email@dominio"
qa init && qa ingest --seeds --benchmarks && qa ingest
qa funnel --tiers L0            # screen gratuito
qa funnel --dry-run             # costo y presupuesto necesario
# agentes (L1+): requiere ANTHROPIC_API_KEY
```

### Dónde mirar primero (mayor riesgo de error con impacto en dinero o decisiones)
1. `src/qa/fundamentals/metrics.py`:
   - `_balance_date`, deuda y `debt_elsewhere`;
   - `_quarters` / `_consistent_pair` / `_consistent_sum` (trimestres desde acumulados YTD con vintages);
   - `_ttm` y `_ttm_year_ago`.
   Probar con XBRL real de distintos tipos de emisor: 10-Q, 20-F IFRS, años de 52/53 semanas, re-expresiones.
2. `src/qa/providers/sec_edgar.py: parse_companyfacts` (vintages, conceptos, materialidad de re-expresiones) y
   `_filing_gap`.
3. `src/qa/valuation/core.py`: `run_scenario` (FCF apalancado, financiamiento, dilución, piso de equity),
   `balance_inputs`, `reverse_dcf`, FX y `adr_ratio`.
4. `src/qa/funnel/pipeline.py`: `committee` y `_no_trade_band`:
   - K9 en posiciones, `hold_only`;
   - recomendaciones por cada cambio de peso;
   - banda vs caja mínima y topes.
   Y `_score` (K9).
5. `src/qa/agents/base.py: run_agent` (reutilización, presupuesto, reserva del comité, registro de costos) y
   `agents/llm.py` (pause_turn, errores de transporte, costo parcial).
6. `src/qa/funnel/pack.py` (qué ven los agentes; point-in-time; FX) y `src/qa/bottlenecks.py: registry_as_of`.
7. `src/qa/db/repo.py: upsert_datapoints` (reconciliación por proveedor: que nunca borre datos que no corresponde).
8. `src/qa/agents/prompts/*.md`: si inducen a inventar datos, a sobrevalorar narrativa o a ignorar contra-evidencia.

### Preguntas que valdría la pena responder
- ¿Hay algún camino por el que un dato posterior a la fecha de decisión llegue a un agente, al score o a la cartera?
- ¿Algún número que ve un agente o el usuario no tiene fuente y fecha, o mezcla fechas de balance?
- ¿Las reglas de scoring y los topes pueden producir decisiones absurdas con datos reales incompletos?
- ¿El control de presupuesto puede excederse o, al revés, dejar sin analizar lo importante?
- ¿Hay algún camino de código que pueda enviar una orden real?

## 9. Documentación del repo
- `docs/00`: diagnóstico y arquitectura.
- `docs/01`: mapa industrial.
- `docs/02`: modelo de datos y trazabilidad.
- `docs/03`: fuentes de datos y limitaciones.
- `docs/04`: plan e historial de las revisiones.
- `docs/05`: scoring, rúbricas y kill switches.
- `docs/06`: embudo y costos.
- `docs/07`: estado de las semillas.
- `docs/research/ai_finance_labs.md`: investigación de la metodología de referencia, separando lo verificado, lo
  reconstruido de fuentes secundarias y el diseño propio.
