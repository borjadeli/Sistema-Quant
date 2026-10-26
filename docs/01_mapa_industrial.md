# 01 — Mapa industrial IA + Energía + Commodities

Archivo fuente: `data/map/industry_map.yaml` (v0.1.1). Código: `src/qa/map/model.py`.

## Estructura
- **12 capas → 28 segmentos → 142 nodos**, con 225 dependencias. El grafo es acíclico y está validado por test.
- Un nodo es una **función económica**: componente, material, capacidad, equipo, servicio o tecnología.
  No es un sector bursátil.
- Las aristas van del insumo al producto (`input_to`), del producto al comprador (`sold_to`) o marcan sustitutos
  (`substitute_for`). Todo nodo tiene un camino hasta la demanda final (test), lo que permite explicar *por qué*
  una empresa está expuesta. Ejemplo: `indium → compound_substrates → eml_lasers → dci_optical_systems → hyperscaler_capex`.

| Capa | Ejemplos de nodos |
|---|---|
| demand | capex de hyperscalers, neoclouds, IA soberana, mineros reconvertidos a HPC |
| compute | GPU, ASIC custom, CPU, DPU, EDA/IP, chips de interfaz de memoria, foundry |
| memory | HBM, capacidad de obleas DRAM, DRAM de servidor, SSD, HDD nearline |
| packaging_test | CoWoS, OSAT, hybrid bonding, sustratos ABF, film ABF, T-glass, PCB de alta capa, probe cards, ATE, burn-in, **MLCC** |
| semicap | EUV, deposición/grabado, metrología, subsistemas WFE, obleas, sustratos compuestos (InP/GaAs/Ge), SiC, gases, fotoresinas, cuarzo |
| networking_optical | transceptores 800G/1.6T, LPO, CPO, EML, láseres CW, SiPh, MOCVD, DSP, retimers, AEC, switch ASICs, DCI |
| datacenter | powered land, EPC, colocation, ODM, switchgear BT/MT, UPS, busway, 800V DC, generadores, liquid cooling, chillers, bombas |
| grid | transformadores de potencia y distribución, switchgear AT, e-houses, cables AT, acero eléctrico GOES, interconexión, mano de obra |
| generation | turbinas de gas, aeroderivadas, fundiciones de álabes, superaleaciones, gas natural, celdas SOFC, IPPs, nuclear, SMR, BESS, SiC/GaN |
| nuclear_fuel | minería, conversión, enriquecimiento, HALEU, fabricación de combustible |
| critical_materials | cobre, aluminio, galio, germanio, indio, tierras raras, tungsteno, helio, litio, grafito, **itrio/escandio** |
| industrial_services | mecanizado de precisión, instrumentación, ingeniería, distribución eléctrica |

## Atributos y criticidad estructural
Cada nodo tiene `concentration` (de monopoly a low) y `substitutability` (de none a high). Son **priors del
analista**, no evidencia. La criticidad estructural es una heurística de 0 a 10:

`10 × (0.4·concentración + 0.4·no-sustituibilidad + 0.2·alcance downstream)`

Solo se usa como prior (tope 4 en el scoring) cuando no hay evidencia de escasez. La escasez real vive en el
registro de bottlenecks (`data/bottlenecks/`): 21 bottlenecks con evidencia, contra-evidencia y fecha.

## Cómo se mantiene vivo
1. **Map Curator** (`qa curate-map`). Busca en la web nuevas restricciones, tiempos de entrega, plantas, capex y
   regulación, y genera propuestas `pending`.
2. **Discovery** (`qa discover`). Elige nodos sin cobertura, o con un bottleneck severo y pocas empresas, y
   busca proveedores cotizantes que no estén en el universo.
3. **Revisión** (`qa proposals --approve ID`). Las propuestas aprobadas se escriben a overlays
   (`map_overlay.yaml`, `discovered.yaml`) que los loaders combinan con la base curada. El YAML humano queda
   limpio y cada cambio automático es auditable. Antes de escribir, el cambio se simula con los loaders reales y
   se rechaza si el mapa o el universo resultante tiene errores nuevos (tipos, nodo aislado, ciclo, ticker o
   símbolo duplicado sin distinguir mayúsculas). Un nodo raíz se agrega con `feeds` (sus aristas salientes); las
   claves descriptivas que el modelo no tiene pasan a `notes`; `layer` y `_*` están reservadas. La moneda de una
   empresa descubierta sale de la bolsa (USD solo en bolsas con líneas en dólares, como LSE o TSX).

La v0.1.1 ya incorporó así 2 nodos (MLCC, itrio/escandio) y 13 empresas (Murata, Technoprobe, AT&S, Kioxia,
Nittobo, etc.) que salieron de la evidencia de bottlenecks.

## Brechas conocidas (nodos sin empresas en el universo)
`chiplets_hybrid, cleanroom_fab_build, dram_wafer_capacity, grid_interconnection, high_purity_quartz,
immersion_cooling, power_conversion_grid, sf6_alternatives, silicon_interposer, skilled_labor, tungsten,
vanadium, yttrium_scandium`. Son los primeros objetivos de Discovery.

## Universo
245 empresas en `data/universe/companies.yaml`, clasificadas por nodos, con exposición prior H/M/L/N a IA,
energía y commodities. El market cap, la liquidez y el CIK **no se cargan a mano**: los trae `qa ingest` con fuente
y fecha. Los tickers con dudas tienen el campo `verify`; las especulativas o pre-ingresos van con `status: candidate`.
Las relaciones comerciales confirmadas (con URL) están en `data/research/seed_companies_verified.json`. Toda
relación sin fuente se trata como hipótesis.
