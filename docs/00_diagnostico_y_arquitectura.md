# 00 — Diagnóstico y arquitectura

## Punto de partida

- **Directorio vacío.** No había código, datos ni configuración previa: todo se construyó desde cero (2026-10-21).
- **Red del contenedor de desarrollo restringida.** El contenedor en el que se construyó la v0.1 no llega a
  `data.sec.gov`, `www.sec.gov` ni `query1/2.finance.yahoo.com` (proxy 403). Los conectores reales están
  implementados y testeados con fixtures, pero **todavía no se ejecutaron contra las APIs**. Ver `03_fuentes_de_datos.md`.
- **Investigación web limitada.** Los agentes de investigación solo pudieron leer resúmenes del buscador
  (WebFetch no resolvía DNS). Todo dato que salió de ahí está etiquetado `search_summary` y el sistema lo trata
  como no verificado (alerta + penalización de confianza).

## Diagnóstico: qué sistema conviene construir

Lo que pedís (que el modelo elija, compre y venda como AI Finance Labs, y vos lo repliques) tiene tres riesgos:

1. **Riesgo de narrativa.** Un LLM tiende a confirmar la tesis alcista y a "recordar" números.
   *Mitigación:* los números salen de un pipeline determinístico con trazabilidad. Los agentes interpretan y
   proponen supuestos, y el código calcula. Hay un Bear Case adversarial y un Auditor. Kill switches y topes
   están escritos en código.
2. **Riesgo de costo.** Analizar 245 empresas con 8 agentes cada mes cuesta del orden de US$1.350 por ciclo.
   *Mitigación:* un embudo de 4 capas con promoción por valor de la información, reutilización por hash y
   presupuesto duro. La cota superior es de unos US$68 por ciclo (`qa estimate`) y el gasto real suele ser menor.
3. **Riesgo de evaluación.** Los backtests con LLM no sirven, por memorización y look-ahead (ver `research/ai_finance_labs.md` §3).
   *Mitigación:* la evaluación es solo forward, en paper trading append-only. Cada decisión guarda precio, fuente,
   timestamp, data cutoff, hash de inputs y hash de configuración. También se mide la calibración de las probabilidades.

## Stack elegido y por qué

| Componente | Elección | Por qué |
|---|---|---|
| Lenguaje | Python 3.11+ | Ecosistema de datos financieros y del SDK de Claude |
| Persistencia | SQLAlchemy 2 + SQLite (Postgres cuando haga falta) | Cero infraestructura hoy. Se migra cambiando `database_url` |
| Configuración curada | YAML versionado en git (mapa, universo, bottlenecks, settings) | Lo revisa un humano, se compara en diffs y es reproducible |
| Validación | Pydantic v2 | Esquemas de datos y de salida de agentes, con validación estricta |
| Grafo industrial | networkx | Dependencias, ancestros/descendientes, criticidad y caminos a la demanda |
| LLM | Claude vía SDK oficial (`anthropic`), con *structured outputs*, web search server-side y fallback server-side | JSON garantizado por esquema, búsqueda con fuentes y modelo configurable por capa |
| Datos oficiales | SEC EDGAR XBRL (companyfacts) | Fuente oficial, gratuita, point-in-time (`filed`) |
| Mercado/opciones | yfinance (reemplazable) | Gratis y global, pero no oficial: queda etiquetado y se exige frescura |
| Dashboard | Streamlit + Plotly | Una app interactiva sin frontend separado |
| Tests | pytest | 52 tests: datos, valuación, scoring, opciones, cartera, agentes y pipeline |

**Sustituibilidad (regla 13).** Proveedores de datos detrás de interfaces (`providers/base.py`). Para LLM, cualquier
objeto con `.complete()`. Modelo, effort y búsqueda se configuran por capa en `config/settings.yaml`.

## Arquitectura

```
                 ┌──────────────────────── data/ (curado, git) ────────────────────────┐
                 │ map/industry_map.yaml  universe/companies.yaml  bottlenecks/*.yaml  │
                 │ research/*.json (evidencia web)   fixtures/ (SINTÉTICO, tests)      │
                 └───────────────┬─────────────────────────────────────────────────────┘
                                 │ qa init (sync)
 SEC EDGAR ─┐   ┌────────────────▼─────────────────┐
 Yahoo ─────┼──►│ providers/ -> DataPoint (fuente,  │   capa 1 ORIGINAL
 CSV manual─┘   │ fecha, unidad, moneda, known_at)  │
                └────────────────┬─────────────────┘
                                 ▼
                 fundamentals/metrics.py  (DERIVED, con inputs)          capa 2 DERIVADO
                 valuation/core.py        (múltiplos, escenarios, reverse DCF)
                                 │
            ┌────────────────────▼─────────────────────────────┐
            │ funnel/pipeline.py  EMBUDO                         │
            │  L0 reglas (0 USD) ─► L1 triage (Haiku)            │
            │  ─► L2 Industrial·Fundamental·Catalyst·            │      capa 3 ESTIMACIÓN (escenarios)
            │       Valuation·Expectations (Sonnet)              │      capa 4 INTERPRETACIÓN (agentes)
            │  ─► L3 Bear ─► Valuation rebuttal ─► Auditor (Opus)│
            │  ─► Comité: Portfolio Manager (Opus)               │
            └────────────────────┬─────────────────────────────┘
                                 ▼
     scoring/engine.py (rúbricas, topes, kill switches) ─► portfolio/construction.py (restricciones en código)
                                 ▼
     portfolio/ledger.py  CARTERA SIMULADA append-only ─► metrics.py (vs SPY/QQQ/sectoriales, Brier)
                                 ▼
     dashboard/ (Streamlit)   export/ (Excel/CSV)   feed/ (eventos que reabren tesis)

 Mantenimiento del mapa:  Map Curator (web) ─► propuestas ─► aprobación ─► map_overlay.yaml
                          Discovery (web)   ─► candidatas ─► aprobación ─► discovered.yaml ─► L0
```

## Agentes (12 roles)

| Agente | Capa / modelo por defecto | Produce | Rol en el debate |
|---|---|---|---|
| Triage | L1 · Haiku 5.5 · effort low | exposición real, prioridad, incertidumbre | filtro escéptico |
| 1 Industrial Research | L2 · Sonnet 5.5 | rol en la cadena, ajuste ±2 al puntaje de bottleneck | |
| 2 Fundamental Analyst | L2 · Sonnet 5.5 | % de exposición, poder de precios, red flags, `economic_capture` | |
| 3 Valuation Analyst | L2 · Sonnet 5.5 | supuestos bear/base/bull (el código calcula) | |
| 4 Catalyst Analyst | L2 · Sonnet 5.5 | catalizadores fechados ±, `catalysts` | |
| 6 Market Expectations | L2 · Sonnet 5.5 | lo implícito en el precio vs nuestro base, `informational_edge` | contrasta el upside |
| 5 Bear Case Analyst | L3 · Opus 5.5 · web | argumentos con evidencia, *thesis killers* | **adversario** |
| 3b Valuation rebuttal | L3 · Opus 5.5 | escenarios revisados tras el bear case | responde al bear |
| 8 Research Auditor | L3 · Opus 5.5 | veredicto pass/fail, issues, discrepancias | puede vetar (K8) |
| 7 Portfolio Manager | Comité · Opus 5.5 | pesos, tesis, gatillos de invalidación | |
| Map Curator | mantenimiento · Sonnet 5.5 · web | propuestas de cambio al mapa/bottlenecks | |
| Discovery | mantenimiento · Sonnet 5.5 · web | empresas candidatas fuera del universo | |

**Discrepancias.** Se detectan automáticamente: exposición triage vs fundamental, expectativas vs upside,
*thesis killers* del bear y las que registra el auditor. Quedan en la tabla `disagreements` con qué
evidencia las resolvería. Bajan la confianza del score y las ve el PM.

## Qué se adoptó de AI Finance Labs (ver `research/ai_finance_labs.md`)

| Elemento | Estado de la fuente | Cómo lo usamos |
|---|---|---|
| LLM gestiona cartera, rebalanceo mensual, el humano replica | **Verificado** (Autopilot) | cartera modelo simulada, rebalanceo mensual + por evento |
| Tesis escrita por posición, registro de perdedores | **Verificado** (AI Finance Labs, ago-2026) | `recommendations` append-only con tesis, riesgos y gatillos |
| Validación forward (problema de memorización) | **Verificado** (paper "The Memorization Problem") | sin backtests LLM; paper trading con data cutoff |
| ~15 posiciones, pesos desiguales | **Secundario** (prensa/blog) | 6-15 posiciones, tope 10% |
| Etapas macro → sector → empresa → verificación | **Secundario** | embudo L0-L3 + auditor (adaptado a cadena de suministro) |
| 97 variables, escala 1-100 | **Secundario no verificado** | **no copiado**: 6 factores con rúbricas explícitas |
| Mapa industrial, bottlenecks, embudo de costos, bear adversarial, kill switches, opciones | **Propio** | — |

Pendiente, inspirado en sus papers: ensambles y múltiples corridas por decisión, y control de sesgo con
identificadores anonimizados. Ver el plan.
