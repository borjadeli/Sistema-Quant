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
| Relaciones | solo con URL; sin URL = hipótesis (validador) | ✅ semillas (20/20 investigadas, calidad `search_summary`) |
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

## Revisión adversarial (2026-10-22)
1. **Revisión.** 4 revisores independientes (matemática financiera, procedencia/point-in-time, orquestación, CLI/dashboard)
   reportaron 48 hallazgos, que dos escépticos por hallazgo intentaron refutar.
2. **Corrección.** Se corrigieron todos (commit `bea9fdb`) y cada uno tiene un test de regresión que reproduce el
   escenario de falla original (`tests/test_review_regressions.py`).
3. **Ronda 2 (commit `644954c`).** Una reverificación adversarial encontró residuales y regresiones introducidas por
   los propios fixes (mezcla de vintages en trimestres YTD, ancla del balance, moneda por conteo, EV/FCF con FCF
   apalancado, deuda de otra fecha, migración de bases viejas, reemplazo de filas manuales, cadencia de la
   performance). Se corrigieron con tests `test_r2_*`.
4. **Ronda 3 (2026-10-22).** Nueva reverificación de 9 frentes: 28 residuales o bugs nuevos, uno crítico (la fecha
   de balance quedaba anclada a la fecha de portada `dei` de las acciones, lo que bloqueaba la valuación de todo
   emisor SEC real). Correcciones principales:
   - balance: ancla en cuentas patrimoniales, balance posterior completo de menor precedencia, balance de > 480
     días bloquea, `debt_elsewhere` solo con deuda no nula reciente; derivación Q4 = FY − (Q1+Q2+Q3) con vintages
     coherentes; un FY posterior reemplaza a un TTM viejo;
   - reutilización: solo desde la misma fecha de decisión o anterior; Map Curator/Discovery sin reutilización;
     hash estable (sin conteos de días ni montos); un reloj UTC; eventos de precio por episodio; registro de
     bottlenecks point-in-time;
   - comité: K9 de una posición en cartera no la vende (se mantiene sin aumentos); K9 por escenarios o bear
     faltantes; recomendación registrada para todo cambio de peso; banda HOLD = umbral de operación (0.5% del NAV);
     notas de restricción completas por empresa;
   - costos: errores de transporte y Ctrl-C registran lo facturado y no abortan el embudo; tope de presupuesto por
     solicitud y corte de `pause_turn`; dry-run sin efectos y con el rebuttal estimado; `pipeline_runs` para que el
     dashboard ignore corridas cortadas;
   - mapa: las propuestas se validan sobre el mapa/universo RESULTANTE con los loaders reales (claves reservadas,
     tipos, duplicados sin distinguir mayúsculas ni símbolo de datos); Discovery conserva los nodos originales y la
     moneda sale de la bolsa (nunca USD por defecto);
   - datos manuales: BOM/codificación/CSV mal formado sin abortar, rechazo de saldos tipados como flujo,
     reconciliación por proveedor (borra huérfanas), fechas de semillas estrictas.
   Cada caso tiene su test en `tests/test_review_round3.py`; los scripts de reproducción de los verificadores
   también se volvieron a correr contra el código corregido.
5. **Ronda 4.** La reverificación de las correcciones de la ronda 3 confirmó todas como corregidas y encontró
   efectos colaterales, también corregidos (tests `test_r4_*`):
   - un comunicado posterior con solo caja y deuda reemplazaba un balance de filing con inversiones CP y
     patrimonio (ahora debe cubrir las mismas cuentas; si no, se conserva el filing y K7 se controla en ambos);
   - la deuda etiquetada solo en el 10-K quedaba fuera de la ventana de 200 días al tercer trimestre (ahora 400);
   - un emisor sin etiqueta de deuda puntuaba apalancamiento y ROIC como faltantes (ahora usa el supuesto
     explícito "deuda 0", con alerta); un faltante núcleo cuenta 2.5 en lugar de 0;
   - un emisor anual con un trimestre posterior aislado perdía el TTM (ahora conserva el último FY);
   - un 20-F presentado en plazo quedaba "demasiado antiguo" unos días por año (umbral 480 -> 520 días);
   - un stream 200 vacío o con un evento SSE truncado abortaba el embudo (ahora es un error registrado);
   - el dry-run no simulaba el control de presupuesto por cota (ahora sí, con las salidas previas embebidas y la
     reserva del comité, e informa el presupuesto necesario);
   - las fechas exactas de evidencia posteriores a la revisión del registro se adelantaban en los replays.
