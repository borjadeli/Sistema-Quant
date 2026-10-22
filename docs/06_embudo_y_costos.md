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
2. **Reutilización por hash.** El hash usa la plantilla del prompt, sin la fecha, y una vista estable del data pack:
   - se quitan la fecha de corrida, el market cap/ADV del día y el pre-score;
   - el precio entra en *buckets* logarítmicos de `price_move_trigger_pct`.

   Sin cambio material (filing nuevo, re-expresión, evento o movimiento mayor al umbral) no hay llamada. Cada
   reutilización queda registrada en `agent_runs` con costo 0. Reglas que mantienen el hash estable y honesto:
   - ningún texto del pack depende del día: la advertencia de balance viejo dice "> 200 días", la antigüedad del
     precio va por tramos (> 3, > 7, > 30 días) y los motivos de L0 no llevan montos (van en campos volátiles);
   - **solo se reutiliza un análisis hecho a la misma fecha de decisión o antes** (`decision_time`), y la antigüedad
     se mide entre fechas de decisión: un replay nunca recibe búsquedas web hechas después;
   - **Map Curator y Discovery nunca se reutilizan**: su trabajo es buscar novedades;
   - los movimientos de precio generan **un evento por episodio** (otro solo si el precio se mueve de nuevo más que
     el umbral desde el último evento), y en el pack entran como máximo 2, así no desplazan a una re-expresión;
   - el registro de bottlenecks es point-in-time: en un replay solo entra la evidencia conocida a la fecha de
     decisión (una fecha exacta vale por sí misma; `evidence_reviewed_at` acota solo las fechas parciales o
     ausentes, y `qa validate` avisa si hay evidencia fechada después de la revisión) y la severidad se recalcula.
   - Todo usa **un solo reloj, UTC**: la fecha de decisión por defecto es la fecha UTC, la misma con que se sellan
     precios y eventos.
3. **Presupuesto duro** por corrida (US$30) y mensual (US$60). Antes de cada llamada se verifica una **cota
   superior** de la primera solicitud (todo `max_tokens`, todas las búsquedas y sus resultados); dentro de la
   llamada, cada continuación `pause_turn` se corta si lo gastado más la cota de la próxima supera el restante. Si no
   alcanza, la llamada se salta y queda como `skipped_budget`.
4. **Modelo, effort y búsquedas por capa**, configurables en `config/settings.yaml`. Se puede probar Opus a
   effort bajo en lugar de la cascada de modelos.
5. **Prompt caching** del system prompt de cada agente.
6. **`qa funnel --dry-run`** y **`qa estimate`**: muestran el costo antes de gastar. El dry-run simula el peor caso: la
   capa se llena hasta su tope por pre-score, e incluye L2, L3 (bear, rebuttal y auditor) y el comité. Aplica el
   **mismo control de presupuesto** que la corrida real (cota por solicitud y reserva del comité) e informa qué
   llamadas se saltarían y el presupuesto necesario (`budget_needed_usd`). No deja rastros: no escribe filas en
   `agent_runs` ni cambia el estado del embudo.
7. Las capas costosas reciben data packs ya calculados: el LLM no busca números que el pipeline ya tiene.
8. **Contabilidad completa.** También se registran las llamadas fallidas que se facturaron (max_tokens, rechazos).
   Las escrituras de caché y los intentos de fallback se valúan al precio de su modelo. El costo se commitea después de
   cada llamada, así que un corte a mitad de corrida no lo borra del presupuesto mensual. Un stream cortado (error de
   transporte) o un Ctrl-C registran lo ya facturado, incluido el input del pedido cortado, y el embudo sigue con la
   próxima empresa.
9. **Corridas incompletas.** Cada corrida queda en `pipeline_runs` (`running` → `completed`). El dashboard ignora
   scores, escenarios, discrepancias y recomendaciones de corridas que no terminaron, y el estado del embudo solo
   se escribe al final.

## Estimación (salida real de `qa estimate`)

Costo por ciclo con **todas las capas llenas** y sin reutilización: **US$85.93**. Analizar las 245 empresas con
el pipeline profundo costaría **unos US$1.353 por ciclo**. El embudo lo reduce unas 16 veces, y la reutilización lo
baja más en los meses sin cambios materiales.

| Agente | Modelo | Llamadas | US$/llamada (pesimista) |
|---|---|---:|---:|
| triage | haiku-5-5 | 90 | 0.0014 |
| industrial / fundamental / catalyst | sonnet-5-5 | 35 c/u | ~0.17 |
| valuation / expectations | sonnet-5-5 | 35 c/u | ~0.21 / ~0.23 |
| bear / valuation_rebuttal / auditor | opus-5-5 | 18 c/u | ~0.88 / ~0.96 / ~1.04 |
| portfolio_manager | opus-5-5 | 1 | 0.48 |

La estimación es pesimista: supone salida al 60% de `max_tokens`, 6.000 tokens de input por búsqueda web y que los
packs de cada capa incluyen las salidas de los agentes previos con ese mismo tamaño (el dry-run usa el mismo
supuesto). El gasto real se registra por llamada en `agent_runs` (tokens, búsquedas, USD) y se ve en el dashboard.

**Presupuesto por corrida.** El control previo a cada llamada exige su **cota superior**, no su costo esperado:
`qa estimate` informa el presupuesto necesario para que no se saltee nada con las capas llenas (US$86.20). Con el
presupuesto por defecto (US$30) un ciclo con todas las capas llenas no entra entero: se saltan las últimas llamadas
de L3. La cota del **Portfolio Manager queda reservada** desde el inicio, así que el comité siempre corre, y las
posiciones cuyo L3 se salteó quedan con K9 (se mantienen, sin aumentos). En la práctica el embudo promueve menos
empresas que sus topes y la reutilización evita la mayoría de las llamadas de las posiciones sin cambios.

## Próximas optimizaciones (no implementadas)
- Batch API (−50%) para L1, que no es sensible a la latencia.
- Paralelizar las llamadas L2 independientes (Industrial/Fundamental/Catalyst) con un cliente async.
- Medir con un eval qué capas aguantan un modelo más chico sin perder calidad. Hoy la elección de modelos es un
  supuesto de costo, no algo medido.
