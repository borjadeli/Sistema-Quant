# Quant Advisory — IA + Energía + Commodities

Sistema de investigación de inversiones multiagente, sistemático y trazable. Busca empresas, sobre todo de nicho,
cuyo potencial económico el mercado subestima, a partir de los cuellos de botella físicos de la IA: cómputo,
fotónica, energía, red eléctrica y materiales críticos.

Funciona como las carteras de AI Finance Labs: **el modelo elige, compra y vende en una cartera simulada**, y vos
la replicás manualmente. Nunca envía órdenes reales (`live_trading_enabled: false` y bloqueo en código).

> Principio: una industria extraordinaria no es necesariamente una buena inversión.

## Qué hay en la v0.1
- **Mapa industrial**: 12 capas, 142 nodos, 225 dependencias. Cada nodo traza su camino hasta la demanda final de IA.
- **Universo**: 245 empresas clasificadas por función industrial, con las 20 semillas incluidas.
- **Cuellos de botella**: 21, con evidencia, contra-evidencia y severidad reproducible.
- **Motor financiero**:
  - métricas point-in-time;
  - múltiplos con reglas de aplicabilidad;
  - escenarios bear/base/bull a 12, 24 y 36 meses, con financiamiento y dilución;
  - sensibilidad y reverse DCF.
- **Scoring** de 6 factores con rúbricas, topes y 8 kill switches.
- **12 agentes** (Claude): Triage, Industrial, Fundamental, Valuation, Catalyst, Bear Case, Market Expectations,
  Valuation rebuttal, Research Auditor, Portfolio Manager, **Map Curator** y **Discovery**.
- **Embudo de costos** L0→L3 + comité, con reutilización por hash y presupuesto duro: cota de unos US$68 por ciclo,
  contra unos US$1.350 si se analizara todo en profundidad.
- **Opciones**: calls con probabilidades risk-neutral y por escenarios, comparadas siempre contra comprar la acción.
- **Cartera simulada** append-only, métricas vs SPY/QQQ/sectoriales y calibración (Brier).
- **Dashboard** Streamlit, export a Excel/CSV y 52 tests.

## Instalación
```bash
pip install -e ".[dev,dashboard,market]"
export QA_SEC_USER_AGENT="Tu Nombre tu@email.com"   # requerido por la SEC
export ANTHROPIC_API_KEY=...                        # solo para capas L1+ y agentes de mapa
```

## Uso
```bash
qa init                      # crea la base y sincroniza mapa, universo, bottlenecks e investigación semilla
qa validate                  # valida todo y muestra la cobertura del mapa
qa demo                      # punta a punta OFFLINE con datos SINTÉTICOS y agentes guionados
qa ingest --seeds --benchmarks && qa ingest   # SEC + Yahoo con trazabilidad
qa estimate                  # costo máximo de un ciclo
qa funnel --tiers L0         # screen gratuito de todo el universo
qa funnel --dry-run          # estima costos sin llamar al LLM
qa funnel --only AXTI,COHR,FN --budget 10    # corrida real acotada
qa funnel                    # ciclo completo L0-L3 + comité + rebalanceo simulado
qa curate-map                # Map Curator: propone cambios al mapa
qa discover --k 3            # Discovery: busca empresas en nodos sin cobertura
qa proposals [--approve ID]  # revisar y aplicar propuestas
qa options COHR              # calls con cadena actual (se niega si no hay datos frescos)
qa portfolio                 # cartera simulada, performance, recomendaciones
qa export                    # Excel + CSV en data/exports/
streamlit run dashboard/app.py
pytest
```

## Estado de los datos (importante)
- El contenedor donde se construyó **no tiene acceso a SEC ni a Yahoo** (política de red). Los conectores están
  implementados y testeados, pero no se ejecutaron en vivo. Ver `docs/03_fuentes_de_datos.md` para habilitarlos.
- La investigación web (semillas, bottlenecks, AI Finance Labs) se hizo a partir de **resúmenes de buscador**, no
  de los documentos completos. El sistema la etiqueta `search_summary` y la trata como no verificada.
- `data/fixtures/` contiene **datos sintéticos** (`TEST_*`) solo para tests y demo. El kill switch K4 impide que
  entren a la cartera real.

## Documentación
- `docs/00_diagnostico_y_arquitectura.md`: diagnóstico, stack, arquitectura y agentes; qué se tomó de AI Finance Labs.
- `docs/research/ai_finance_labs.md`: metodología pública, separando lo verificado, lo secundario y lo desconocido.
- `docs/01_mapa_industrial.md`, `docs/02_modelo_de_datos.md`, `docs/03_fuentes_de_datos.md`.
- `docs/04_plan_implementacion.md`: módulos, criterios de aceptación y estado.
- `docs/05_scoring_rubricas.md`, `docs/06_embudo_y_costos.md`.
- `docs/07_semillas_estado.md`: lo que se sabe hoy de las 20 empresas semilla (sin recomendación).

## Reglas del sistema
No inventa datos, relaciones ni fuentes. Las declaraciones corporativas no cuentan como hechos verificados. No usa
precios viejos como actuales. Las probabilidades subjetivas quedan etiquetadas como tales. No oculta la evidencia
contraria. No opera con dinero real.
