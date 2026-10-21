# 04 — Plan de implementación por módulos

Estado al 2026-10-21. ✅ hecho y verificado por tests · 🟡 implementado, falta ejecución con datos reales · ⬜ pendiente.

## Fase 1 — Investigación y arquitectura
| Módulo | Criterio de aceptación verificable | Estado |
|---|---|---|
| Metodología AI Finance Labs | informe con cada afirmación clasificada como verificada, secundaria o desconocida, con fuentes (`docs/research/ai_finance_labs.md`) | ✅ con salvedad: fuentes leídas vía resúmenes del buscador; el whitepaper V2 no abrió |
| Arquitectura y stack | `docs/00`, separación de datos en 4 capas en el esquema | ✅ |
| Mapa industrial | YAML válido, DAG, todo nodo con camino a la demanda (`tests/test_map_universe.py`) | ✅ 142 nodos |
| Modelo de datos | DataPoint con fuente, fecha, unidad, moneda, período y `known_at`; test point-in-time | ✅ |
| Fuentes | `docs/03` con estado real de cada una | ✅ |

## Fase 2 — Universo
| Módulo | Criterio | Estado |
|---|---|---|
| Clasificación por función | cada empresa mapeada a ≥1 nodo existente (test) | ✅ |
| 150-250 empresas | test 150 ≤ n ≤ 250; sin market cap cargado a mano (test) | ✅ 245 |
| Relaciones | solo con URL; sin URL = hipótesis (validador) | ✅ semillas (12/20 investigadas) |
| Radar de bottlenecks | ≥15 bottlenecks con evidencia, contra-evidencia y severidad reproducible (test) | ✅ 21 |
| Datos de mercado del universo | `qa ingest` completa market cap y ADV para ≥90% del universo | 🟡 bloqueado por la red |

## Fase 3 — Motor financiero y agentes
| Módulo | Criterio | Estado |
|---|---|---|
| Conector SEC | parser point-in-time con prioridad de conceptos (test con fixture) | 🟡 falta correrlo en vivo |
| Métricas derivadas | TTM, Q4 derivado, sin derivar acciones, faltantes explícitos (tests) | ✅ |
| Valuación | múltiplos con reglas n.m., escenarios 12/24/36m, financiamiento/dilución, sensibilidad, reverse DCF monótono (tests) | ✅ |
| Scoring | rúbricas, topes, 8 kill switches, alertas, confianza (tests) | ✅ |
| Opciones | BS de referencia, rechazo de cadena vieja, filtros de liquidez/spread, P(0)/P(2x/3x/5x) RN y subjetiva, comparación con la acción (tests) | ✅ (falta cadena real) |
| Cliente LLM | structured outputs, web search, fallback, pause_turn, rechazos, costo (test con cliente falso) | ✅ (falta llamada real con API key) |
| 12 agentes | esquemas estrictos validados (test por agente); salida inválida rechazada (test) | ✅ |
| Reutilización + presupuesto | segunda corrida idéntica = `reused` a costo 0; presupuesto agotado = `skipped_budget` (test) | ✅ |
| Embudo L0-L3 + comité | demo de punta a punta con todos los agentes (test) | ✅ con LLM guionado y datos sintéticos |
| Discrepancias | se detectan y persisten | ✅ |

## Fase 4 — Dashboard y validación
| Módulo | Criterio | Estado |
|---|---|---|
| Dashboard | 8 páginas cargan sin error contra la base real y la demo (smoke test AppTest) | en curso |
| Cartera simulada | rebalanceo con costos, append-only, bloqueo de trading real (tests) | ✅ |
| Métricas de performance | retorno, vol, MDD, Sharpe, beta/alpha CAPM, HHI, rotación, Brier (tests) | ✅; alpha multifactor ⬜ |
| Primeros análisis completos de semillas | L2+L3 sobre las 20 semillas con datos SEC reales y API key | ⬜ requiere red + API key |
| Registro de resultados | snapshot diario de la cartera y métricas vs SPY/QQQ | ⬜ tarea programada |

## Próximas iteraciones (orden recomendado)
1. **Habilitar datos** (dominios de red o máquina local): `qa ingest` y revisión manual del L0 con datos reales.
2. **Primera corrida real acotada:**
   ```bash
   qa funnel --only AXTI,FORM,AAOI,COHR,FN --budget 10
   ```
   Después, revisar las salidas de los agentes una por una y ajustar prompts. Construir un **eval** con 10-20 casos.
3. **FX y ADRs** en `market_inputs` (hoy se niega a valuar si la moneda de reporte ≠ cotización).
4. **CSV manual** para HPS.A, IVN y los emisores de TSE/TWSE de mayor prioridad (con URL de SEDAR+ / IR).
5. **Robustez al estilo AI Finance Labs:**
   - 2-3 corridas del comité y votación;
   - opción de ensamble con otro modelo;
   - control de sesgo con identificadores anonimizados.
6. **Tareas programadas:**
   - diaria: `qa ingest` + feed + snapshot de cartera;
   - mensual: embudo completo + comité;
   - por evento: re-análisis.
7. Alpha multifactor (Fama-French + momentum + semis), calibración de probabilidades por cohorte, Batch API para L1.
