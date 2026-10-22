# 02 — Modelo de datos y trazabilidad

Esquema: `src/qa/db/schema.py` (SQLAlchemy, SQLite por defecto).

## Cuatro capas epistemológicas (nunca se mezclan)

| Capa | Dónde | Ejemplo |
|---|---|---|
| 1. Original | `datapoints(kind=original)`, `prices`, `market_snapshots`, `option_quotes`, `documents` | ingresos del 10-Q, precio de cierre |
| 2. Derivado | `datapoints(kind=derived)` / `fundamentals.metrics` con `inputs` | margen bruto TTM, FCF, EV/EBITDA |
| 3. Estimación | `scenarios.assumptions`, `datapoints(kind=estimate)` | crecimiento base 25%, probabilidad bull 25% |
| 4. Interpretación | `agent_runs.output`, `disagreements`, `scores.factors[*].rationale` | "poder de precios fuerte" |

## DataPoint: todo número relevante tiene
`entity, metric, value, unit, currency, period_end, period_type (Q/FY/TTM/instant), fiscal_label, kind,
verification, source{name, url, document_type, published_at, retrieved_at}, known_at, inputs, author, note`

- **`known_at`** es la fecha en que el dato se hizo público (en SEC, la fecha `filed`). Una decisión en t solo usa
  datos con `known_at ≤ t`. Los datos sin `known_at` se excluyen de las decisiones históricas.
- **`verification`** puede ser `confirmed_by_filing`, `company_statement`, `media_report`, `search_summary`,
  `analyst_prior`, `hypothesis` o `synthetic_test_data`.
- **Vintages y re-expresiones.** Cada presentación que cambia el valor de un período es una fila nueva (vintage) con su
  propio `known_at` y la nota `restates=...`. Los comparativos idénticos no generan filas nuevas. Una decisión en t usa el
  vintage más reciente con `known_at ≤ t`. Las re-expresiones generan un evento `restatement` en el feed.
- **Proveedores editables** (CSV manual, investigación semilla, fixtures): cada fila guarda su `provider`. En cada
  ingesta el contenido actual del proveedor es la verdad para esa entidad: se borran las filas que ya no están, las que
  ahora se rechazan y las que cambiaron de fuente citada, aunque el lote quede vacío. Si un archivo no se pudo leer
  entero, no se borra nada (se avisa).
- **Selección al calcular** (`fundamentals/metrics.py`):
  1. point-in-time;
  2. unidades normalizadas (por ejemplo `USD_millions`);
  3. períodos a ≤10 días agrupados (años de 52/53 semanas), con precedencia de fuente: filing > declaración > prensa >
     resumen de buscador > prior > hipótesis > sintético;
  4. moneda de reporte dominante: los puntos en otra moneda se descartan, `currency_conflict` bloquea la valuación;
  5. trimestres reconstruidos desde los acumulados YTD del 10-Q (Q2=H−Q1, Q3=9M−H, Q4=FY−9M), con TTM solo sobre 4
     trimestres contiguos;
  6. todas las cuentas de balance a **una** fecha de balance. La fecha la fija la mejor fuente entre cuentas
     patrimoniales (no las acciones de portada `dei`, que llevan la fecha de la portada). Un balance posterior de una
     fuente de menor precedencia se usa solo si trae caja y deuda; uno de más de 480 días bloquea (K4). Si falta
     deuda a esa fecha pero hay deuda no nula reciente, se bloquea (K7); un dato viejo o un 0 explícito va al
     supuesto "deuda 0" con alerta, y solo si el balance viene de un filing;
  7. comparaciones interanuales por fecha (±20 días).

## Tablas principales
- **Mapa:**
  - `map_nodes`, `map_edges`
  - `map_change_proposals`: propuestas de agentes con estado pending/approved/applied
- **Universo:**
  - `companies`: no se borran; las que salen del YAML quedan `excluded` para evitar survivorship bias
  - `company_relationships`
  - `bottlenecks`: evidencia, contra-evidencia y severidad calculada
- **Agentes:**
  - `agent_runs`: agente, capa, modelo, versión de prompt, `input_hash`, `config_hash`, `decision_time`, salida, estado, tokens, USD, fuentes web
  - `pipeline_runs`: estado de cada corrida (`running`/`completed`); lo de corridas cortadas no se muestra
  - `disagreements`
  - `funnel_state`
  - `scores`: factores con rationale y evidencia, topes, kill switches, alertas, faltantes, confianza
  - `scenarios`: supuestos, resultados, precio y fuente al crear, base de las probabilidades
- **Cartera (append-only):**
  - `recommendations`: acción, peso, convicción, precio, hora y fuente, tesis, catalizadores, riesgos, gatillos de invalidación, `data_cutoff`, `mode=simulated|operated`, `supersedes_id`
  - `paper_trades`: con slippage y comisiones
  - `portfolio_snapshots`
- **Feed:** `feed_events` (filings nuevos, movimientos de precio, propuestas de mapa, eventos manuales).

## Reproducibilidad
Cada `agent_run` guarda el hash del prompt, del input y de `config/settings.yaml`. Los data packs son la única
información que ve un agente, y tienen fuente y fecha. Los resultados de las corridas quedan en `data/runs/<run_id>.json`.
