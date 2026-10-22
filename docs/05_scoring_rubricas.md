# 05 — Scoring, rúbricas, topes y condiciones eliminatorias

Implementación: `src/qa/scoring/engine.py` (rúbricas en `RUBRICS`), severidad de bottlenecks en `src/qa/bottlenecks.py`.

## Factores (0-10) y pesos iniciales

| Factor | Peso | Método | Cómo se calcula |
|---|---:|---|---|
| Importancia del cuello de botella | 20% | **híbrido** | regla sobre el registro de bottlenecks; el agente Industrial ajusta ±2 con evidencia |
| Capacidad de captura económica | 20% | agente + rúbrica | Fundamental Analyst elige una banda de la rúbrica; el puntaje queda forzado dentro de la banda |
| Valuación y asimetría | 25% | **regla pura** | calculado sobre los escenarios |
| Catalizadores | 15% | agente + rúbrica | Catalyst Analyst |
| Calidad financiera | 10% | **regla pura** | métricas derivadas |
| Ventaja informacional | 10% | híbrido | 50% proxy (tamaño, listado, cobertura) + 50% agente (rúbrica) |

Los factores de mayor peso que dependen de números (valuación, calidad financiera) **no los decide un LLM**.

### Severidad de un bottleneck (0-10)
`base(estado)` + `confianza` + `concentración` + `fuentes primarias` − `contra-evidencia`:
- estado: current_shortage 6, projected 4.5, speculative 3, easing 2.5
- confianza de la evidencia: high +1.5, medium +0.5, low −0.5
- concentración máxima de los nodos mapeados: monopoly/very_high +1, high +0.5
- ≥2 fuentes primarias (empresa o gobierno): +0.5
- tanta o más contra-evidencia que evidencia: −0.5

Máximo por regla: 9. Un 10 solo es posible con un `override` justificado en `bottlenecks.yaml`.
Factor de la empresa = máx(severidad × multiplicador de estado) sobre los bottlenecks de sus nodos.
Con confianza baja se aplica ×0.8. Sin bottleneck con evidencia, se usa el prior estructural del mapa con tope 4.

### Valuación y asimetría (regla)
- `s_er = 5 + 20 × (r − 10%)`, con r = retorno esperado anualizado a 36 meses (10% → 5, 35% → 10, −15% → 0)
- `s_ud = 2.5 × (retorno bull / |retorno bear|)` a 36 meses (4:1 → 10)
- `score = 0.6·s_er + 0.4·s_ud − 2·[P(pérdida permanente) > 30%]`

Pérdida permanente = escenario con retorno a 36 meses < −30%. Las probabilidades son **subjetivas** y quedan
etiquetadas así. La calibración se mide ex post con el Brier score.

### Calidad financiera (regla)
Es el promedio de los sub-puntajes. Las cinco métricas **núcleo** son:
- margen bruto: 20×GM
- margen FCF: 5+33×m
- ROIC: 5+25×(ROIC−10%); si el EBIT es positivo y el capital invertido ≤ 0, vale 10 (n.m., no es un faltante)
- apalancamiento: 10−2.5×ND/EBITDA, o 10 si tiene caja neta
- dilución: 10−100×dilución

Una métrica núcleo **faltante cuenta como 0** (peor caso): ocultar o no tener un dato nunca sube el puntaje. El
runway (trimestres×10/12) se suma solo para quien quema caja y no reemplaza a una métrica núcleo.

### Rúbricas de agentes (bandas)
Están en `RUBRICS`; se inyectan en el prompt de cada agente.
- **Captura económica.**
  - 9-10: >50% de los ingresos del nodo restringido, proveedor top-2, suba de precios demostrada.
  - 0-2: exposición solo narrativa.
- **Catalizadores.**
  - 9-10: catalizador duro y fechado en menos de 6 meses.
  - 0-2: no hay catalizadores, o los pendientes son negativos.
- **Ventaja informacional.**
  - 9-10: cambio no reflejado en el consenso, menos de 5 analistas, dependencia poco conocida.
  - 0-2: megacap hipercubierta.

## Topes: la narrativa no compensa

| Si… | entonces el total queda limitado a |
|---|---|
| calidad financiera < 3 | 5.0 |
| valuación/asimetría < 3 | 5.5 |
| captura económica < 2 | 5.0 |

Un factor **no evaluado** (agente fallido u omitido) cuenta como 0 para los topes: los controles fallan cerrados.

## Condiciones eliminatorias (kill switches): bloquean la cartera

| Código | Condición |
|---|---|
| K1 | duda de empresa en marcha (going concern) |
| K2 | runway de caja < 4 trimestres sin financiamiento comprometido |
| K3 | liquidez diaria < mínimo de configuración (US$2M) |
| K4 | sin datos fundamentales verificables, **cualquier** dato sintético mezclado, balance incompleto (sin caja), monedas mezcladas, o balance de más de 480 días |
| K5 | retorno esperado ponderado a 24 meses negativo |
| K6 | P(pérdida permanente) > 50% |
| K7 | deuda neta/EBITDA > 6x, o deuda no nula reportada en los ~200 días previos (o después) pero no a la fecha de balance |
| K8 | el Research Auditor rechazó el análisis |
| K9 | análisis incompleto: sin evaluación fundamental válida (red flags/going concern no evaluados), sin escenarios de valuación válidos (K5/K6 no evaluables), sin bear case o sin veredicto del auditor en L3, o candidato que no completó L3 |

**K9 en una posición que ya está en cartera.** Un análisis incompleto (error del agente, presupuesto agotado,
`--tiers` sin L3) no es evidencia negativa. Si los únicos bloqueos de una posición son K9, se mantiene con su peso
actual como máximo (puede recortarse, nunca aumentarse) y se anota "revisar". Solo K1–K8 la llevan a 0.

## Alertas (no bloquean, se muestran y bajan la confianza)
- dilución > 8% anual
- SBC > 15% de los ingresos
- cobertura de intereses < 1.5x
- concentración de clientes > 40%
- exposición a controles de exportación
- re-expresiones contables
- emisión reciente de acciones
- datos no verificados contra el documento primario
- empresa especulativa (peso máximo 3%)
- escenario base que requiere financiamiento externo

## Confianza
Arranca en 3 puntos. Resta 2 si los datos son sintéticos o no hay datos, 1 si no vienen de un filing,
1 si hay más de 4 faltantes y 1 si hay 2 o más discrepancias abiertas. Resultado: 3 = high, 2 = medium, ≤1 = low.
