# AI Finance Labs / Alejandro Lopez-Lira: qué metodología publicaron sobre sus portafolios con LLM

**Fecha de la investigación:** 2026-10-21 · **Alcance:** solo fuentes públicas · **Idioma:** español.

## 0. Cómo se hizo esta investigación y qué tan confiable es

- **Problema de acceso, importante.** `WebFetch` falló con error de DNS (`ENOTFOUND`) en **todas** las URLs, incluidas las tres de partida (`start.joinautopilot.com/strategies/gpt-portfolio-63080`, `ai-street.co/p/how-ai-runs-200-million-in-portfolios`, `paulgravette.com/ai-insights/...`). El proxy de salida también bloqueó `curl` (403) hacia esos dominios, arXiv, SSRN, Google Docs y Scribd. **No pude leer ninguna de esas páginas en forma directa.** Todo lo que sigue sale de los resúmenes y fragmentos del índice de `WebSearch` (cerca de 50 búsquedas). La única excepción es el repositorio de GitHub `alejandroll10/llm_trading_sim`, que cloné y leí entero.
- Por eso las "citas textuales" de prensa y blogs son fragmentos que devolvió el buscador. Conviene revisarlas contra el original antes de citarlas afuera.
- La URL de Gravette que figura en el índice es `paulgravette.com/business/i-copied-an-ai-portfolio-managers-whitepaper-and-built-my-own-heres-how`, con `/business/` en lugar de `/ai-insights/`.
- **El whitepaper original ("The GPT Portfolio White Paper", V2, publicado en Google Docs) no se pudo abrir.** Lo que se diga sobre su contenido es, como mucho, (S).

**Leyenda:**
- **(V)** VERIFICADO-ORIGINAL: lo dicen Lopez-Lira, AI Finance Labs, Autopilot o sus papers.
- **(S)** SECUNDARIO: lo informa un tercero, y se indica quién.
- **(U)** DESCONOCIDO o no publicado.

---

## 1. Actores y contexto

- **Alejandro Lopez-Lira.** Profesor de finanzas en la Universidad de Florida (UF). Es autor de *The Predictive Edge* (Wiley, julio de 2024) (S: listado de Porchlight y Warrington News). Su paper con Yuehua Tang se publicó en el *Journal of Financial Economics*, vol. 184 (2026) (V: registro en RePEc).
- **AI Finance Labs.** Finance Magnates (Tareq Sikder, 7-sep-2026) presenta a Lopez-Lira como su fundador (S). Autopilot escribe: *"The GPT Portfolio and Claude Portfolio strategies are managed by Dr. Lira's AI Finance Labs, an independent Third-Party Pilot"* (V: Autopilot Journal, sin fecha visible). La firma tiene la cuenta de X @aifinancelabs.
- **Autopilot.** Es una plataforma de copy-trading. El asesoramiento lo presta *Autopilot Advisers LLC, an SEC registered investment advisor* (V: post en X de Chris Josephs, de Autopilot, 20-may-2025). En el mismo hilo dice: *"Each portfolio is managed in partnership with Dr. Lopez"* y *"Performance doesn't include fees"* (V).
- **Escala.** Cerca de 52.000 inversores asignaron unos US$200 millones a siete portafolios públicos armados con ChatGPT, Claude, DeepSeek, Grok y otros modelos (S: Matt Robinson, AI Street, fines de julio de 2026 según metadatos del buscador).
- **Origen del GPT Portfolio.** Brian Schardt, CEO de Autopilot, convirtió la investigación de Lopez-Lira en un portafolio real (S: AI Street, 5-dic-2024). La propia cuenta del portafolio lo describe así: *"It's a prompting engine run with a PhD professor. Here is the full white paper"* (V: @thegptinvestor/"Thegptportfolio", 16-abr-2025). Finbold ya hablaba en septiembre de 2023 de un "GPT 2.0 Portfolio" (S). No está claro si era la misma estrategia (U).

---

## 2. Los 9 puntos

### 2.1 Universo de acciones
- (S) Acciones más ETFs sectoriales: *"15 monthly-rebalanced assets (10 stocks and 5 sector ETFs)"* (AI Street, 5-dic-2024). Lo mismo aparece en una nota de prensa republicada en Warrington News de UF (año no visible).
- (S) Las tenencias del GPT Portfolio incluían un ETF de bonos ligados a inflación (10%) y un ETF de oro (9%) (MarketScreener, datos al 17-feb-2026). Es decir, los 5 ETFs no son todos "sectoriales" en sentido estricto.
- (S) El portafolio de DeepSeek operó Vista Energy el 14-jul-2026 (Business Insider vía Yahoo Finance). *Inferencia nuestra:* Vista no está en el S&P 500, así que el universo de al menos ese portafolio va más allá del S&P 500.
- (S) Según debelegger.nl (2026), el Claude Portfolio parte de unas 1.000 acciones del Russell 1000.
- (S) Gravette usó las 503 acciones del S&P 500 vía yfinance. **Fue una decisión suya**, no está atribuida al whitepaper.
- **(U)** No encontré definición oficial del universo: índice base, filtros de liquidez o capitalización, ni lista de ETFs elegibles.

### 2.2 Variables financieras
- (S) El scoring usa *"current market data, news, and financial metrics"* (AI Street, 2024).
- (S) El análisis parte del contexto macro, las finanzas de la empresa y lecturas sectoriales (MarketScreener, ~feb-2026).
- (S) Gravette afirma que el whitepaper trae *"the exact 97 financial variables scored per stock"*. También dice que él pasó de "roughly 25" campos a los 97 (blog, 16-jun-2026).
- (S, poco confiable) Para el Grok Portfolio, Grokipedia (wiki generada por IA) menciona condiciones macro, riesgo geopolítico, estados financieros, noticias y precios.
- **(U)** No encontré publicada la lista de las 97 variables, sus fuentes de datos ni cuándo se actualizan.

### 2.3 Sistema de scoring y ponderaciones
- (S) Los activos los puntúa la IA, que *"weighs economic and company-specific factors"*. Finbold (21-sep-2023) informa puntajes de 85 para UNH y REGN.
- (S) Gravette describe la jerarquía *"macro first, then sector, then company, then news"* y una escala de 1 a 100 aplicada *"using the whitepaper's exact prompt structure"*. Que la escala 1–100 venga del whitepaper lo dice solo él.
- (S, poco confiable) Grokipedia atribuye una escala de 1 a 100 al Grok Portfolio.
- **(U)** No encontré los pesos de cada capa (macro, sector, empresa, noticias), una fórmula de agregación, umbrales de selección ni reglas de desempate.

### 2.4 Arquitectura de agentes
- (S, entrevista con Lopez-Lira) Los portafolios *"break investing into a series of steps—macro analysis, company research, portfolio construction and verification—with different AI agents handling each stage"* (AI Street, ~jul-2026).
- (S) En el Claude Portfolio, Claude *"was used for research and investment ideas, while Lopez-Lira and his team set the strategy and risk parameters and controlled the investment process"*. El proyecto no tiene afiliación con Anthropic (Finance Magnates, 7-sep-2026).
- (S) Otras versiones se contradicen:
  - debelegger.nl habla de "vijftien AI-agents" que ponderan compras y ventas entre sí, y de "geen menselijke tussenkomst" (sin intervención humana).
  - explainx.ai (ago-2026) cita "every pick inside those rails is mine", en la voz del propio Claude Portfolio.
- (S, réplica de terceros) Gravette armó su propia versión:
  - Claude Opus genera un panorama macro a 3 meses.
  - Hace 11 llamadas sectoriales, una por sector GICS.
  - Sonnet puntúa cada acción.
  - Opus arma el portafolio de 15 activos.
- **(U)** No encontré qué modelo y versión corre en cada etapa, cómo se orquesta, cómo funciona el agente de "verification", ni la temperatura o la cantidad de corridas.

### 2.5 Prompts publicados
- (S) Según Business Insider/Yahoo (~jul-2026), el whitepaper del GPT Portfolio que está en la página de Autopilot trae ejemplos de prompts, entre ellos uno *"for producing investment reports and scores from financial data"*.
- (S) MarketScreener (~feb-2026): *"the exact prompts used to run these portfolios are not known"*.
- **(U)** No obtuve el texto de los prompts del producto, porque no pude abrir el Google Doc ni Scribd.
- **(V) Prompt académico** de Lopez-Lira y Tang (arXiv 2304.07619). Fragmentos textuales confirmados:
  - *"Forget all your previous instructions. Pretend you are a financial expert."*
  - *"Answer "YES" if good news, "NO" if bad news, or "UNKNOWN" if uncertain in the first line."*
  - Después pide una frase breve de justificación y pregunta si el titular es bueno o malo para el precio de la empresa en el corto plazo. Esa parte está parafraseada.
  - Las respuestas se mapean a +1, −1 y 0 y se promedian por empresa (S: CXO Advisory).
- **(V) Prompts del simulador** (repositorio `alejandroll10/llm_trading_sim`, último commit del 25-jul-2026 por Alejandro Lopez-Lira). Por ejemplo, `src/agents/prompts/value.md`: *"You are a value investor who focuses on fundamental analysis. You believe in mean reversion and try to buy undervalued assets and sell overvalued ones."* El repo trae unos 20 perfiles (momentum, news, market_maker, contrarian, etc.) en Markdown.

### 2.6 Metodología de valuación
- **(U)** No encontré DCF, múltiplos objetivo, precios objetivo ni márgenes de seguridad en ninguna fuente sobre los portafolios. Lo único publicado es la mención genérica de "financial metrics".
- (S, CNBC, abr-2023) Sobre su experimento académico, Lopez-Lira advirtió que no incluía precios objetivo ni cálculos hechos por el modelo, y que la tecnología tipo ChatGPT suele inventar números.

### 2.7 Construcción del portafolio y rebalanceo
- **Cantidad de activos.**
  - (S) 15 activos, 10 acciones y 5 ETFs (AI Street, 2024; Warrington; Scribd).
  - (V, fragmento) Autopilot Journal dice que el portafolio tiene "15 stocks", lo que contradice el desglose 10+5.
- **Frecuencia de rebalanceo.**
  - (V) Mensual. Chris Josephs (Autopilot, 20-may-2025): *"the portfolio gets rebalanced on a monthly basis"*.
  - (V) Autopilot Journal: rebalanceo mensual.
  - (S) Warrington: *"holds the assets for one month — after which the AI run system conducts an analysis and rebalances accordingly"*.
- **Ponderación.**
  - (S) No es equiponderada. Pesos observados: NVDA 10%, ETF de bonos ligados a inflación 10%, ETF de oro 9%, Vertiv 9%, MSFT 7% (MarketScreener, ~feb-2026).
  - **(U)** No está publicada la regla de ponderación: si la fija el LLM, si es proporcional al score, ni los topes.
- **Réplica en las cuentas de los usuarios** (V, Autopilot Journal).
  - El monto se reparte según el peso de cada tenencia.
  - Las cuentas *"won't match it perfectly"*, por aprobaciones, redondeo a acciones enteras, filtros y listas negras del usuario.
  - En el plan Premium las órdenes van solas al broker; en el Basic solo llega una alerta.
- **Claude Portfolio.**
  - (V, @aifinancelabs, 24-ago-2026) Hay *"a written thesis behind every position, trades posted publicly after they fill, and losers left on the record next to the winners"*.
  - (S) Un buscador devolvió "límite de dos trades por semana", pero no lo pude atribuir a ninguna fuente: **descartado**.
- **(U)** No encontré ventana de ejecución, tipo de orden, ni reglas de rotación o de permanencia mínima.

### 2.8 Gestión de riesgo
- (S) Los "risk parameters" los fija el equipo humano (Finance Magnates). explainx habla de "rails" definidos por el operador.
- (S) Hay rasgos defensivos en las tenencias, como oro y bonos ligados a inflación (MarketScreener). Warrington cita un rebalanceo del 11 de marzo (año no visible) con tres acciones "safe-haven" ante preocupaciones macro.
- (S, poco confiable) Grokipedia describe el Grok Portfolio como long-only.
- (S, afiliación dudosa) `claudeportfolio.com` menciona un tope de 25% por activo, pero se refiere a una cartera cripto. **No lo atribuyo a AI Finance Labs.**
- **(U)** No encontré topes por posición o sector, stop-loss, límites de drawdown, objetivos de volatilidad, apalancamiento, mínimos de liquidez ni reglas de caja.

### 2.9 Evaluación de performance y cifras reportadas
- **Método** (V/S).
  - El benchmark es el S&P 500/SPY (V: posts de Autopilot).
  - La performance que publica Autopilot no descuenta comisiones (V: Josephs).
  - Lopez-Lira advierte que *"it can take a decade to know whether a diversified strategy is outperforming the market"* (S: AI Street).
  - **(U)** No encontré métricas ajustadas por riesgo (Sharpe, alfa por factores, tracking error), auditoría independiente ni metodología de cálculo (TWR o MWR).
- **Cifras con fecha** (todas autodeclaradas o de prensa; ninguna auditada):

| Fecha | Portafolio | Cifra | Fuente |
|---|---|---|---|
| 5-dic-2024 | GPT | ~+32% en el año vs ~+28% del S&P 500; US$11,3M copiados | (S) AI Street |
| Sin fecha | GPT | +69,2% desde el inicio | (V) ficha de Autopilot Marketplace |
| 16-jun-2026 | GPT / DeepSeek | +76,4% / +61,2% desde el inicio, "verified, live" | (S) Gravette, citando a Autopilot |
| ~jul-2026 | DeepSeek | +56% en 12 meses vs +16% del S&P 500 | (S) AI Street |
| ~jul-2026 | GPT | +78% desde el lanzamiento, +16% en el año | (S) BI/Yahoo |
| 4-ago-2026 | Claude (desde 3-mar-2026, con US$50k) | +19,04% vs +12,24% | (S) Finance Magnates, explainx |
| 24-ago-2026 | Claude | +29,2% desde el 3-mar; >US$40M; >9.000 suscriptores | (V) @aifinancelabs |
| 4-sep-2026 | Claude | +31,1%; ~US$49,4M | (S) Finance Magnates |

La fecha de inicio del GPT Portfolio no está clara (U). MarketScreener mide desde el 12-feb-2025, pero hay cifras de 2024 y una versión "2.0" de 2023.

---

## 3. Lecciones académicas para nuestro sistema

1. **Sentimiento de titulares (V)** — Lopez-Lira y Tang, arXiv 2304.07619 (v1 abr-2023, última versión 30-oct-2025), SSRN 4412788, JFE 2026.
   - Los puntajes de ChatGPT predicen retornos diarios fuera de muestra y superan a los métodos tradicionales.
   - El efecto es más fuerte en acciones chicas y tras noticias negativas.
   - GPT-1, GPT-2 y BERT no logran predecir retornos: es una capacidad que aparece recién con los modelos grandes.
   - Versión temprana: titulares de oct-2021 a dic-2022, posteriores al corte de entrenamiento de ChatGPT. El long-short da más de 400% acumulado *antes de costos*. Los autores asumen costos de 10 o 25 pb por operación.
   - Versión reciente: GPT-4 sobre titulares posteriores a su corte logra un hit rate cercano al 90% portafolio-día, pero sobre la reacción inicial, que no es operable. Predice además el drift posterior. El long-short diario rinde 34 pb/día *antes de costos*.
   - Los retornos de la estrategia caen a medida que crece la adopción de LLMs.
   - Paquete de replicación: Mendeley, DOI 10.17632/f39x226htv.2.
   - (S, CNBC 2023) Lopez-Lira conjeturó que la predictibilidad llegaría a cero en unos cinco años.
   - **Implicancia:** es una señal diaria, concentrada en acciones chicas, que se degrada con el tiempo. No se traslada sin más a un portafolio mensual de 15 activos de gran capitalización.
2. **Memorización y sesgo de anticipación (V)** — "The Memorization Problem", Lopez-Lira, Tang y Zhu, arXiv 2504.14765 (abr-2025, revisado dic-2025).
   - GPT-4o recuerda casi a la perfección indicadores y niveles del S&P 500 anteriores a su corte de entrenamiento.
   - En titulares previos al corte, ubica la fecha y el nivel del índice del día siguiente con un MAPE de alrededor de 0,01%. Fuera del período de entrenamiento falla.
   - Pedirle al modelo que "respete" la fecha no evita que recuerde, y enmascarar el nombre de la empresa protege poco.
   - La capacidad de pronóstico contrafáctico **no está identificada** dentro del período de entrenamiento.
   - Tang (X, 21-abr-2025): *"test and apply LLMs only on data outside their training period."*
   - **Implicancia directa: un backtest con LLM sobre datos históricos dentro del período de entrenamiento no sirve. Hace falta paper-trading forward, con marca de tiempo.**
3. **Evidencia de terceros sobre el sesgo de anticipación (S, académico).**
   - Glasserman y Lin (arXiv 2309.17322): dentro de la muestra, los titulares anonimizados *superan* a los originales. El "efecto distracción" pesa más que el sesgo de anticipación, sobre todo en empresas grandes.
   - Gao, Jiang y Yan (arXiv 2512.23847, jun-2026) proponen la "Lookahead Propensity". Al replicar a Lopez-Lira y Tang dentro de la muestra, la señal se amplifica en los pares empresa-fecha con alta propensión (+32% del efecto por cada desvío estándar). Esa interacción desaparece después del corte.
4. **Comportamiento de agentes LLM (V)** — "Can LLMs Trade?", arXiv 2504.10789, SSRN 5217340 (14-abr-2025).
   - Los LLM siguen la estrategia que se les indica, incluso cuando es contraproducente.
   - Prompts similares generan **comportamiento correlacionado**, con riesgo para la estabilidad del mercado.
   - El repo exige salidas estructuradas y advierte: *"Smaller models like `llama-3.1-8b-instruct` fail structured output validation. Use 70B+ models"*. Recomienda barridos con varias semillas, temperaturas, modelos y prompts, y tratar cada familia de prompts como un clúster: *"runs sharing a prompt family are not independent draws"*.
5. **Sesgos de inversión de los LLM (V, con Lopez-Lira como coautor)** — "Your AI, Not Your View", ICAIF 2025, arXiv 2507.20957.
   - Preferencia por empresas grandes y por el sector tecnología, y estilo contrarian antes que momentum.
   - Hay sesgo de confirmación: el modelo sostiene su juicio inicial aunque aparezca evidencia en contra.
6. **Costos.**
   - (S) Gravette dice correr su réplica por "about $4 a month" en API; cada armado de portafolio con Opus cuesta unos US$0,50.
   - (V) Los retornos académicos son *brutos*. Los publicados por Autopilot no descuentan comisiones, y la suscripción cuesta US$29 por trimestre o US$100 por año (S: The College Investor).
   - (S, no verificado) Futurism cita a Lopez-Lira diciendo que los resultados del paper son "much more optimistic" que lo que obtendría un inversor real con un monto razonable. No pude confirmar la cita.

---

## 4. Buenas prácticas replicables para nuestro sistema (*inferencia y diseño propios*)

1. **Validación solo forward.**
   - Paper-trading con registro inmutable: timestamp, versión del modelo, hash del prompt e inputs guardados.
   - Ningún backtest con LLM sobre fechas anteriores al corte de entrenamiento cuenta como evidencia.
   - Si se hace algún análisis histórico, usar modelos con corte conocido y solo fechas posteriores a ese corte.
2. **Pipeline jerárquico con datos deterministas.**
   - El orden es macro, sector (11 GICS), empresa y noticias.
   - Los números vienen de un pipeline de datos propio. El LLM interpreta y puntúa, nunca "recuerda" cifras.
3. **Salidas estructuradas.**
   - JSON con esquema: score de 1 a 100, rúbrica explícita por componente y una tesis escrita por posición.
   - Validación automática, y modelos grandes para que el formato no falle.
4. **Agente verificador independiente.**
   - Compara cada cifra que cita el LLM con la fuente.
   - Rechaza tesis con datos inventados.
   - Controla que el portafolio respete las reglas.
5. **Riesgo definido por humanos y aplicado en código.** El LLM elige dentro de esos límites:
   - topes por posición y por sector;
   - número fijo de posiciones (por ejemplo, 15);
   - mínimos de liquidez;
   - reserva de caja o activos defensivos.
6. **Rebalanceo mensual con costos modelados.** Contemplar 10 a 25 pb por lado, slippage y lag de ejecución; Autopilot admite que la réplica no es exacta.
7. **Robustez.**
   - Varias corridas por decisión (semillas, temperatura y variantes de prompt) y promedio o votación.
   - Ensamble de dos o más proveedores de modelo, para no depender de un solo sesgo.
   - Inferencia estadística agrupada por familia de prompt.
8. **Atribución honesta de la performance.**
   - Además del SPY, comparar contra un S&P 500 equiponderado, contra benchmarks con el mismo sesgo sectorial y contra regresiones de factores (mercado, tamaño, valor, momentum, tecnología/semis).
   - Reportar neto de costos, con Sharpe, drawdown máximo y tracking error.
9. **Controles de sesgo del LLM.**
   - Medir cuánto se inclina hacia empresas grandes y tecnología.
   - Probar con identificadores anonimizados, mirando la distracción y la anticipación.
10. **Transparencia.** Registrar perdedores junto con ganadores y conservar todo el historial de decisiones (AI Finance Labs lo hace en el Claude Portfolio).

---

## 5. Qué NO copiar y limitaciones

- **Cifras de marketing.** Son autodeclaradas, cubren ventanas cortas (5 a 20 meses) y no descuentan comisiones. Además, se promociona "el mejor" de siete portafolios en paralelo: es un problema de pruebas múltiples y supervivencia. Lopez-Lira mismo dice que hacen falta unos diez años.
- **"97 variables" sin evidencia.** No está publicado que agregar más variables mejore el resultado. Meter más contexto en el prompt puede diluir el foco del modelo. La lista ni siquiera está verificada.
- **El "exact prompt structure" de Gravette** es una réplica de un tercero. No es la metodología oficial verificada.
- **Opacidad.** El producto no publica prompts, pesos de las capas, regla de ponderación ni reglas de riesgo. No hay cómo auditarlo ni replicarlo con fidelidad.
- **Relatos contradictorios sobre la autonomía.** Finance Magnates habla de control humano; debelegger, de cero intervención. No sirve como prueba de que "el LLM solo" genere alfa.
- **Concentración.** El GPT Portfolio estaba muy cargado en tecnología (NVDA, MSFT, VRT). Sin control de factores, la "habilidad" puede ser beta tecnológica en un año alcista.
- **Trasladar alfa académico.** El resultado de los titulares es diario, bruto, concentrado en acciones chicas y con malas noticias, y en declive. No justifica un portafolio mensual de grandes empresas.
- **Backtests con LLM sobre historia conocida por el modelo.** Hay que evitarlos por completo, por memorización.
- **Limitaciones de este informe:**
  - No se pudo leer en forma directa ninguna página web, solo resúmenes del buscador.
  - El whitepaper original sigue sin leerse.
  - Varias fechas son inferidas por metadatos.
  - Hay discrepancias sin resolver: "15 stocks" vs "10+5", y marzo vs abril de 2026 como inicio del Claude Portfolio.

---

## 6. Fuentes

**Originales (V)**
- Autopilot Journal, "How rebalancing works when you follow a Portfolio": https://start.joinautopilot.com/journal/how-rebalancing-works-when-you-follow-a-portfolio
- Página de la estrategia (no se pudo abrir): https://start.joinautopilot.com/strategies/gpt-portfolio-63080 · Ficha de Marketplace: https://marketplace.joinautopilot.com/landing/5/63080
- Chris Josephs (Autopilot) en X, 20-may-2025: https://x.com/Chrisjjosephs/status/1924930483072467187 · https://x.com/Chrisjjosephs/status/1924900634899443887
- @thegptinvestor en X, 16-abr-2025: https://x.com/thegptinvestor/status/1912565776919458200
- Whitepaper V2 (no se pudo abrir): https://docs.google.com/document/d/1BMCAyP6f4RYk5oPuB-2CXRBxRe5uyz3CIavX311E5K4/edit · Copia en Scribd: https://www.scribd.com/document/884108746/The-GPT-Portfolio-White-Paper
- @aifinancelabs en X, 24-ago-2026: https://x.com/aifinancelabs/status/2091992556288786622
- Lopez-Lira y Tang: https://arxiv.org/abs/2304.07619 · https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4412788 · https://ideas.repec.org/a/eee/jfinec/v184y2026ics0304405x26001066.html · https://data.mendeley.com/datasets/f39x226htv/2
- Lopez-Lira, Tang y Zhu, "The Memorization Problem": https://arxiv.org/abs/2504.14765 · Tang en X: https://x.com/YuehuaTang/status/1914145098675167258
- Lopez-Lira, "Can LLMs Trade?": https://arxiv.org/abs/2504.10789 · https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5217340 · Repositorio (leído directamente): https://github.com/alejandroll10/llm_trading_sim
- Lee et al. (con Lopez-Lira), "Your AI, Not Your View": https://arxiv.org/html/2507.20957v1
- Sitio de Lopez-Lira: https://alejandrolopezlira.site/

**Secundarias (S)**
- Matt Robinson, AI Street, "How AI Runs $200 Million in Portfolios" (~jul-2026): https://www.ai-street.co/p/how-ai-runs-200-million-in-portfolios
- Matt Robinson, AI Street, "ChatGPT Portfolio Outperforming S&P500" (5-dic-2024): https://www.ai-street.co/p/chatgpt-portfolio-outperforming-s-p500
- Paul Gravette (16-jun-2026): https://www.paulgravette.com/business/i-copied-an-ai-portfolio-managers-whitepaper-and-built-my-own-heres-how
- Finance Magnates, Tareq Sikder (7-sep-2026): https://www.financemagnates.com/forex/retail-investors-put-27-million-into-claude-trading-portfolio-after-19-gain/
- Business Insider vía Yahoo Finance (~jul-2026): https://finance.yahoo.com/markets/stocks/articles/ai-model-best-stock-trader-091501419.html
- MarketScreener (~feb-2026): https://www.marketscreener.com/news/can-chatgpt-and-grok-beat-the-market-ce7e5cdfd081fe24
- MarketWatch, Laila Maidan (7-jun-2025), republicado por Warrington: https://news.warrington.ufl.edu/faculty-and-research/morningstar-a-professor-testing-chatgpts-deepseeks-and-groks-stock-picking-skills-suggests-stockbrokers-should-worry
- Warrington News (nota sobre el GPT Portfolio): https://news.warrington.ufl.edu/?p=75317
- Finbold, Vahid Karaahmetovic (21-sep-2023): https://finbold.com/heres-how-a-chatgpt-2-0-portfolio-picks-its-winning-stocks/
- explainx.ai (ago-2026): https://explainx.ai/blog/claude-portfolio-autopilot-ai-trading-experiment-august-2026
- debelegger.nl (2026): https://www.debelegger.nl/post/claude-portfolio-onthult-de-15-aandelen-waarmee-ai-de-s-p-500-wil-verslaan
- Grokipedia (baja confiabilidad): https://grokipedia.com/page/Grok_Portfolio_Autopilot
- The College Investor (comisiones): https://thecollegeinvestor.com/59796/autopilot-review/
- CNBC (12-abr-2023): https://www.cnbc.com/2023/04/12/chatgpt-may-be-able-to-predict-stock-movements-finance-professor-says.html
- CXO Advisory: https://cxoadvisory.com/sentiment-indicators/chatgpt-as-stock-sentiment-analyst
- Glasserman y Lin: https://arxiv.org/abs/2309.17322 · Gao, Jiang y Yan: https://arxiv.org/abs/2512.23847
