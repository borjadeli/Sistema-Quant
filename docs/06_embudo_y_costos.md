# 06 — Embudo de análisis y control de costos

## Idea central
"Cuanto mejor la empresa, más se analiza" está casi bien. El criterio correcto es
**analizar más donde más análisis puede cambiar una decisión con mucho en juego**:

- **Prioridad** (potencial de asimetría) × **incertidumbre** (cuánto podría cambiar la visión). Una empresa
  claramente mala o claramente cara no necesita más análisis. Una prometedora pero dudosa, sí.
- **Las posiciones en cartera** pasan siempre por L2 y L3. El riesgo de pérdida está en lo que ya tenemos.
- **Un evento material** (nuevo filing, movimiento de precio > 15%, noticia en el feed) reabre el análisis.
  Si no pasó nada, se reutiliza el análisis anterior.

## Capas

| Capa | Quién | Modelo (config) | Sobre cuántas | Qué decide | Costo aprox. por empresa |
|---|---|---|---|---|---|
| L0 | reglas | — | todo el universo (245) | filtros duros (market cap, liquidez, estado) y pre-score = 40% bottleneck + 20% exposición + 20% calidad financiera + 20% valuación relativa | US$0 |
| L1 | Triage | Haiku 5.5, effort low | ≤ 80 + cartera | exposición real o narrativa, red flags, prioridad e incertidumbre | ~US$0.001 |
| L2 | 5 agentes | Sonnet 5.5, effort medium, ≤3 búsquedas | ≤ 25 + cartera | escenarios y score completo | ~US$0.85 |
| L3 | Bear → rebuttal → Auditor | Opus 5.5, effort high, ≤8 búsquedas | ≤ 8 + cartera | score final, discrepancias, veto del auditor | ~US$2.1 |
| Comité | Portfolio Manager | Opus 5.5 | 1 llamada | pesos de la cartera | ~US$0.4 |

Promoción L1 → L2: `recommended_next == L2`, `priority ≥ 4` y orden por `priority + 0.3 × uncertainty`.
Promoción L2 → L3: `score ≥ 6.0`, los 8 mejores, más la cartera.

## Palancas de costo implementadas
1. **Embudo con topes por capa** (`funnel.L*.max_promoted`).
2. **Reutilización por hash.** El hash del input usa una vista estable del data pack, con el precio agrupado
   en *buckets* logarítmicos del 15%. Sin cambio material no hay llamada.
3. **Presupuesto duro** por corrida (US$30) y mensual (US$60). Se estima el costo antes de cada llamada; si no
   alcanza, se salta y queda registrado como `skipped_budget`.
4. **Modelo, effort y búsquedas por capa**, configurables en `config/settings.yaml`. Se puede probar Opus a
   effort bajo en lugar de la cascada de modelos.
5. **Prompt caching** del system prompt de cada agente.
6. **`qa funnel --dry-run`** y **`qa estimate`**: muestran el costo antes de gastar.
7. Las capas costosas reciben data packs ya calculados: el LLM no busca números que el pipeline ya tiene.

## Estimación (salida real de `qa estimate`)

Cota superior por ciclo: **US$67.98**, sin reutilización y con todas las capas llenas. Analizar las 245
empresas con el pipeline profundo costaría **unos US$1.353 por ciclo**. El embudo lo reduce unas 20 veces, y la
reutilización lo baja más en los meses sin cambios materiales.

| Agente | Modelo | Llamadas | US$/llamada (pesimista) |
|---|---|---:|---:|
| triage | haiku-5-5 | 90 | 0.0014 |
| industrial / fundamental / catalyst / valuation / expectations | sonnet-5-5 | 35 c/u | ~0.17 |
| bear / valuation_rebuttal / auditor | opus-5-5 | 18 c/u | ~0.69 |
| portfolio_manager | opus-5-5 | 1 | 0.42 |

La estimación es pesimista: supone salida al 60% de `max_tokens` y 6.000 tokens de input por búsqueda web. El
gasto real se registra por llamada en `agent_runs` (tokens, búsquedas, USD) y se ve en el dashboard.

## Próximas optimizaciones (no implementadas)
- Batch API (−50%) para L1, que no es sensible a la latencia.
- Paralelizar las llamadas L2 independientes (Industrial/Fundamental/Catalyst) con un cliente async.
- Medir con un eval qué capas aguantan un modelo más chico sin perder calidad. Hoy la elección de modelos es un
  supuesto de costo, no algo medido.
