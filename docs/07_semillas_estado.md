# 07 — Estado de investigación de las 20 empresas semilla

Generado por `scripts/seed_status_report.py`. **No es una recomendación.** Las cifras salieron de resúmenes de
buscador (`search_summary`) con la URL de la fuente; hay que verificarlas contra el documento primario. El
sistema todavía no corrió escenarios ni scoring reales sobre estas empresas: faltan datos de mercado y la API key.

| Ticker | Último trimestre | Ingresos | Resultado neto GAAP | Fuente | Relaciones confirmadas | Red flags |
|---|---|---|---|---|---:|---:|
| AXTI | Q2 2026 | 47.6 USD millions | 11.1 | [link](https://www.businesswire.com/news/home/20260730124233/en/) | 4 | 5 |
| FORM | Q2 FY2026 | 258.2 USD millions | 56.2 | [link](https://investors.formfactor.com/news-releases/news-release-details/formfactor-inc-reports-2026-second-quarter-results) | 4 | 3 |
| AAOI | Q2 2026 | 191.9 USD millions | -22.8 | [link](https://www.sec.gov/Archives/edgar/data/0001158114/000168316826006055/aaoi_ex9901.htm) | 3 | 4 |
| COHR | Q4 FY2026 | 2,050 USD millions | — | [link](https://ir.coherent.com/news-releases/news-release-details/coherent-corp-reports-fourth-quarter-and-full-year-fiscal-2026) | 3 | 4 |
| FN | Q4 FY2026 | 1,315.8 USD millions | 139.3 | [link](https://www.globenewswire.com/news-release/2026/08/17/3346479/0/en/fabrinet-announces-fourth-quarter-and-fiscal-year-2026-financial-results.html) | 4 | 4 |
| RMBS | Q2 2026 | 207.4 USD millions | 67.6 | [link](https://investor.rambus.com/press-releases/press-release-details/2026/Rambus-Reports-Second-Quarter-2026-Financial-Results/default.aspx) | 2 | 2 |
| AMKR | Q2 2026 | 1,898 USD millions | 174 | [link](https://ir.amkor.com/news-releases/news-release-details/amkor-technology-reports-financial-results-second-quarter-2026) | 3 | 3 |
| AEHR | Q4 FY2026 | 18.8 USD millions | 1.4 | [link](https://www.aehr.com/2026/07/aehr-test-systems-reports-fiscal-2026-fourth-quarter-and-full-year-financial-results-with-record-quarterly-bookings-and-100-million-effective-backlog/) | 2 | 4 |
| POWL | Q3 FY2026 | 312 USD millions | 52.2 | [link](https://powellindustriesinc.gcs-web.com/news-releases/news-release-details/powell-industries-announces-third-quarter-fiscal-2026-results) | 2 | 2 |
| HPS.A | Q2 2026 | 325 CAD millions | — | [link](https://www.globenewswire.com/news-release/2026/07/30/3336509/0/en/hammond-power-solutions-reports-second-quarter-2026-financial-results.html) | 2 | 3 |
| NVT | Q2 2026 | 1,471 USD millions | — | [link](https://s22.q4cdn.com/268397047/files/content_files/Q2-2026-NVT-Press-Release.pdf) | 1 | 3 |
| BWXT | Q2 2026 | 901.6 USD millions | 89.1 | [link](https://www.businesswire.com/news/home/20260803655700/en/BWX-Technologies-Reports-Second-Quarter-2026-Results) | 3 | 3 |
| BE | — | — | — | sin investigar | 0 | 0 |
| ERO | — | — | — | sin investigar | 0 | 0 |
| IVN | — | — | — | sin investigar | 0 | 0 |
| CCJ | — | — | — | sin investigar | 0 | 0 |
| UUUU | — | — | — | sin investigar | 0 | 0 |
| IREN | — | — | — | sin investigar | 0 | 0 |
| CIEN | — | — | — | sin investigar | 0 | 0 |
| CIFR | — | — | — | sin investigar | 0 | 0 |

**Sin investigar (se agotó el cupo de búsquedas):** BE, ERO, IVN, CCJ, UUUU, IREN, CIEN, CIFR. Quedan pendientes de `qa ingest` y de la próxima ronda de investigación.

## AXTI — AXT, Inc.

- **Vínculo IA/energía/commodities:** Measurable: Q2 2026 indium phosphide revenue was a record $30.7M out of total revenue of $47.6M (GaAs $6.6M, Ge $0.272M, raw-material JVs $10.0M), per Q2 2026 results coverage. Company states: growth driven by surging demand for InP used in AI data center optical components, supported by backlog and long-term agreements; capacity target of $60M/quarter InP by year-end 2026 and $130M/quarter by end of 2027 (management statement). Commodity link: China export-control regime (InP added Feb 2025) and indium supply (China ~70% of global output per USGS, cited by Reuters/Kitco).
- ⚠️ Export licenses: China added InP to its export-control list in Feb 2025; all Tongmei wafer families require MOFCOM permits; company calls InP export permits its most significant challenge; U.S.-bound permits described as in final review but not issued (424B5) ([fuente](https://www.sec.gov/Archives/edgar/data/0001051627/000121390026045643/ea0286792-424b5_axtinc.htm))
- ⚠️ Heavy dilution: ~$100M offering (Dec 2025) and ~$632.5M offering (Apr 2026) ([fuente](https://www.businesswire.com/news/home/20260424121883/en))
- ⚠️ Redeemable noncontrolling interest of ~7.28% (~$49M) in Tongmei contingent on Tongmei IPO; investors may request redemption ([fuente](https://www.stocktitan.net/sec-filings/AXTI/10-k-axt-inc-files-annual-report-ed35d0270da0.html))
- ⚠️ Geographic/tariff exposure: all manufacturing in China; North America revenue share fell from ~8% (2024) to ~2% (2025) attributed to U.S. tariffs on China-made wafers (10-K summary) ([fuente](https://www.stocktitan.net/sec-filings/AXTI/10-k-axt-inc-files-annual-report-ed35d0270da0.html))
- ⚠️ Rising bank borrowings: short-term borrowings $84.2M at June 30, 2026 vs $62.8M at year-end 2025 ([fuente](https://www.stocktitan.net/sec-filings/AXTI/10-q-axt-inc-quarterly-earnings-report-db714ed7f0c5.html))
- 📅 2025-12-30: Closed underwritten public offering priced at $12.25/share raising ~$100M gross including full exercise of underwriters' option (shelf filed Nov 7, 2025) ([fuente](https://www.businesswire.com/news/home/20251230349400/en/AXT-Announces-Closing-of-Public-Offering-of-Common-Stock-and-Full-Exercise-of-Underwriters-Option-to-Purchase-Additional-Shares))
- 📅 2026-04-22: Public offering of 8,560,311 shares at $64.25 (~$550M gross) plus 1,284,046-share underwriter option exercised (~$82.5M), ~$632.5M aggregate gross; proceeds primarily to support Tongmei; Northland sole bookrunner ([fuente](https://www.businesswire.com/news/home/20260424121883/en))
- 📅 2026-07-30: Record Q2 2026 revenue $47.6M (+77% q/q), GAAP gross margin 44.9%; guided Q3 revenue at least $66M ([fuente](https://www.businesswire.com/news/home/20260730124233/en/))
- 📅 2026-07-30: Tongmei IPO effort moved from Shanghai STAR Market to Hong Kong Exchange (per Q2 call coverage) ([fuente](https://finance.yahoo.com/markets/stocks/articles/axt-inc-axti-q2-2026-050437348.html))

## FORM — FormFactor, Inc.

- **Vínculo IA/energía/commodities:** Measurable: SK hynix (major HBM supplier) was 22.9% of FY2025 revenue per FormFactor supplemental financial information (same >10% customer table as 10-K). Company states Q2 2026 sequential growth in Probe Cards and Systems was driven by HBM and Co-Packaged Optics, and that Foundry & Logic demand is particularly strong. FY2025 revenue was a record $785.0M. No direct energy/commodity exposure identified.
- ⚠️ Customer concentration: SK hynix 22.9% of FY2025 revenue; Intel/TSMC intermittently above 10% ([fuente](https://investors.formfactor.com/static-files/22ce62a8-fd9d-418b-be4f-e4245e9c3118))
- ⚠️ China exposure (~7% of FY2025 revenue per third-party aggregator) and export-restriction risk - unverified against 10-K ([fuente](https://fintool.com/app/research/companies/FORM))
- ⚠️ Elevated capex ($140-170M in 2026) for Farmers Branch expansion ([fuente](https://gov.texas.gov/news/post/governor-abbott-announces-texas-semiconductor-innovation-fund-grant-to-formfactor))
- 📅 2025-12: Acquired Keystone Photonics to bolster co-packaged optics test capabilities (~$20M cash) ([fuente](https://www.marketbeat.com/instant-alerts/formfactor-q4-earnings-call-highlights-2026-02-04/?.tsrc=rss))
- 📅 2026-07: Texas Semiconductor Innovation Fund grant of $24.2M for new Farmers Branch, TX probe card manufacturing facility (600+ jobs; $140-170M 2026 capex) ([fuente](https://gov.texas.gov/news/post/governor-abbott-announces-texas-semiconductor-innovation-fund-grant-to-formfactor))
- 📅 2026-07-29: Record Q2 FY2026 revenue $258.2M (+31.9% y/y), exceeded high end of outlook; free cash flow $52.6M ([fuente](https://investors.formfactor.com/news-releases/news-release-details/formfactor-inc-reports-2026-second-quarter-results))
- 📅 2026-07-28: Expanded partnership with Keystone Microtech in Taiwan ([fuente](https://investors.formfactor.com/news-releases/news-release-details/formfactor-inc-reports-2026-second-quarter-results))

## AAOI — Applied Optoelectronics, Inc.

- **Vínculo IA/energía/commodities:** Measurable (FY2025 10-K): Microsoft (hyperscale data center customer) 28.8% of 2025 revenue; Digicomm (CATV) 53.1%. Amazon warrant vests on up to $4B of Amazon purchases. Company states 800G volumes more than doubled q/q in Q2 2026, demand expected to outpace capacity through mid-2027, and targets ~650,000 pieces/month of 800G and 1.6T by end of 2026. Q2 2026 data center vs CATV revenue split not found in sources seen.
- ⚠️ Dilution: repeated ATM programs including up to $600M (Aug 2026) plus Amazon warrant for up to 7.9M shares ([fuente](https://kalkinemedia.com/us/news/announcements/applied-optoelectronics-initiates-600-million-at-the-market-stock-offering))
- ⚠️ GAAP net loss of $22.8M in Q2 2026 despite record revenue ([fuente](https://www.stocktitan.net/news/AAOI/applied-optoelectronics-reports-second-quarter-2026-wjz4h91d9ngj.html))
- ⚠️ Extreme customer concentration: Digicomm 53.1% and Microsoft 28.8% of 2025 revenue ([fuente](https://www.sec.gov/Archives/edgar/data/1158114/000143774926005875/aaoi20251231_10k.htm))
- ⚠️ Component (memory) shortages cutting Q3 2026 revenue by an estimated $20-25M per management ([fuente](https://stockanalysis.com/stocks/aaoi/transcripts/662748-q2-2026/))
- 📅 2026-08-06: Record Q2 2026 revenue $191.9M (+86% y/y); return to non-GAAP profitability (non-GAAP NI $5.5M); 800G volume more than doubled q/q ([fuente](https://investors.ao-inc.com/news-releases/news-release-details/applied-optoelectronics-reports-second-quarter-2026-results))
- 📅 2026-08-06: Management said memory shortages would cost ~$20-25M of Q3 revenue on 100G single-mode transceivers; capacity approaching 200,000 units/month ([fuente](https://stockanalysis.com/stocks/aaoi/transcripts/662748-q2-2026/))
- 📅 2026-08-21: New equity distribution (ATM) agreement with Raymond James and Needham for up to $600M of common stock (media report) ([fuente](https://kalkinemedia.com/us/news/announcements/applied-optoelectronics-initiates-600-million-at-the-market-stock-offering))
- 📅 2026: Company announced completion of a previously announced at-the-market offering (exact date not confirmed) ([fuente](https://www.benzinga.com/node/62157083))

## COHR — Coherent Corp.

- **Vínculo IA/energía/commodities:** Measurable: Datacenter and Communications segment revenue $1.62B in fiscal Q4 2026 = 79% of total revenue of $2.05B (company presentation/coverage). Company states order coverage through calendar 2027 is exceptional and 1.6T transceivers/CPO are ramping. Commodity link: indium phosphide supply; Coherent warned of an InP shortage in May 2026 (Reuters via Kitco), and is a prepaying customer of AXT for InP substrates.
- ⚠️ Leverage: ~$3.2B long-term debt at June 30, 2026 (10-K) ([fuente](https://www.sec.gov/Archives/edgar/data/0000820318/000082031826000020/iivi-20260630.htm))
- ⚠️ InP supply risk tied to China export controls; Coherent warned of an InP shortage in May 2026 (Reuters via Kitco) ([fuente](https://www.kitco.com/news/off-the-wire/2026-06-11/chinas-control-over-indium-phosphide-exports-threatens-ai-data-centre))
- ⚠️ Restructuring: ~$62M FY2026 restructuring charges from site closures/workforce reductions (10-K summary, third party) ([fuente](https://www.autobound.ai/companies/coherent/10-k))
- ⚠️ Share count/dilution: elimination of Series B convertible preferred and NVIDIA investment structure (equity?) - mechanism not verified ([fuente](https://www.sec.gov/Archives/edgar/data/0000820318/000082031826000020/iivi-20260630.htm))
- 📅 2026-03: NVIDIA announced $2B investment and multiyear partnership with Coherent ([fuente](https://technologymagazine.com/news/nvidia-partner-coherent-expands-texas-ai-semiconductor-fab))
- 📅 2026-06: CHIPS Act letter of intent for $50M for Sherman, Texas InP site (plan to double production space and quadruple wafer output) ([fuente](https://www.boerse-global.de/coherent-aktie-50-millionen-dollar-foerderung/800382))
- 📅 2026-08-12: Record Q4 FY2026 revenue $2.05B (+34% y/y); FY2026 pro forma revenue $7.12B (+28%); Q1 FY27 guide $2.2-2.4B ([fuente](https://ir.coherent.com/news-releases/news-release-details/coherent-corp-reports-fourth-quarter-and-full-year-fiscal-2026))
- 📅 FY2026: Sale of Aerospace & Defense business to Advent International for $400M (described as completed in FY2026 10-K summary); proceeds to debt reduction ([fuente](https://optics.org/news/coherent-sells-aerospace-and-defense-business-for-400m))

## FN — Fabrinet

- **Vínculo IA/energía/commodities:** Measurable (FY2026 10-K): NVIDIA 16.3% and Amazon 10.5% of FY2026 revenue; NVIDIA was 27.6% in FY2025. Media coverage (Zhitong via Webull) reports data center exceeded 50% of Q4 FY2026 revenue for the first time, with communications infrastructure $413M (31%) and automotive/industrial/other $234M (18%) - not verified against the filing. Company states Building 10 (~2M sq ft) will add capacity by early 2027.
- ⚠️ Customer concentration: four customers ~57% of FY2026 revenue (Cisco 19.9%, NVIDIA 16.3%, Nokia 10.7%, Amazon 10.5%) ([fuente](https://www.sec.gov/Archives/edgar/data/0001408710/000140871026000028/fn-20260626.htm))
- ⚠️ NVIDIA share drop (27.6% to 16.3%) as NVIDIA diversifies 800G sourcing to multiple suppliers (media) ([fuente](https://www.webull.com/news/15424022183625728))
- ⚠️ Free cash flow compression (~$4M FY2026) from capacity capex ([fuente](https://www.marketbeat.com/instant-alerts/fabrinet-q4-earnings-call-highlights-2026-08-17/))
- ⚠️ Amazon warrant (up to 381,922 shares) is a modest dilution / contra-revenue item ([fuente](https://www.sec.gov/Archives/edgar/data/1408710/000140871025000010/fn-20250312.htm))
- 📅 2026-08-17: Record Q4 FY2026 revenue $1,315.8M (+45% y/y); FY2026 revenue $4.64B vs $3.42B; GAAP FY NI $473.0M ([fuente](https://www.globenewswire.com/news-release/2026/08/17/3346479/0/en/fabrinet-announces-fourth-quarter-and-fiscal-year-2026-financial-results.html))
- 📅 2026-08-17: Building 10 (Chonburi) on track for completion by early 2027, ~2M sq ft; total expected cost ~$132.5M with ~$39.7M remaining ([fuente](https://www.nasdaq.com/articles/fabrinet-q4-earnings-call-highlights))
- 📅 2026-08: FY2026 10-K: four 10%+ customers (Cisco, NVIDIA, Nokia, Amazon) vs two in FY2025; NVIDIA share fell to 16.3% from 27.6% ([fuente](https://www.sec.gov/Archives/edgar/data/0001408710/000140871026000028/fn-20260626.htm))
- 📅 2026-08-17: Cash and short-term investments fell ~$70M q/q to ~$876M; FY2026 free cash flow ~$4M due to capex ([fuente](https://www.marketbeat.com/instant-alerts/fabrinet-q4-earnings-call-highlights-2026-08-17/))

## RMBS — Rambus Inc.

- **Vínculo IA/energía/commodities:** Measurable: Q2 2026 product revenue $99.2M of $207.4M total (memory interface chips for servers, which are used in AI/datacenter servers); royalties $84.2M. Royalties were 40% of FY2025 revenue (10-K). Company states demand for memory interface chips and AI-related products drove results; markets HBM4E controller IP for AI accelerators. No direct energy/commodity exposure identified.
- ⚠️ Customer concentration: top five customers 66% of 2025 revenue ([fuente](https://www.sec.gov/Archives/edgar/data/917273/000119312526057101/rmbs-20251231.htm))
- ⚠️ Royalty guidance for Q3 2026 ($69-75M) below Q2 actual ($84.2M), implying lumpy licensing revenue ([fuente](https://investor.rambus.com/press-releases/press-release-details/2026/Rambus-Reports-Second-Quarter-2026-Financial-Results/default.aspx))
- 📅 2026-03-05: Launched HBM4E memory controller IP (16 Gbps/pin, 4.1 TB/s per device) - media report ([fuente](https://stockhub.kr/en/news/news_9d82f4c49da1))
- 📅 2026-07-27: Record Q2 2026 revenue $207.4M, above $192-198M guidance; GAAP NI $67.6M; operating cash flow $61.2M ([fuente](https://investor.rambus.com/press-releases/press-release-details/2026/Rambus-Reports-Second-Quarter-2026-Financial-Results/default.aspx))
- 📅 2026-08-05: $100M accelerated share repurchase with Mizuho (initial delivery ~796,000 shares), expected complete by end of Q3 2026 ([fuente](https://www.businesswire.com/news/home/20260805283969/en/))

## AMKR — Amkor Technology, Inc.

- **Vínculo IA/energía/commodities:** Measurable: Q2 2026 Computing end market 22% of sales (record), Advanced products $1,557M of $1,898M net sales. Company states it advanced key customer programs in AI and HPC and is expanding advanced packaging capacity. AI-specific revenue not disclosed. No direct energy/commodity exposure identified.
- ⚠️ Customer concentration: Apple ~29.8%, Qualcomm ~11.1%, top ten 72% of 2025 net sales (third-party citing 10-K) ([fuente](https://policyrisk.com/world/company/AMKR))
- ⚠️ Heavy capex ($2.5-3.0B in 2026) and Arizona program now ~$12B; CHIPS award (up to $407M) has milestone/clawback covenants ([fuente](https://policyrisk.com/world/company/AMKR))
- ⚠️ Thin gross margins (Q3 2026 guide 18.5%-19.5%); communications revenue expected down high-single digits q/q in Q3 (SiP migration, memory supply constraints) ([fuente](https://www.webull.com/news/15303749284471808))
- 📅 2025-09: Issued $500M senior notes due 2033 to refinance debt and for general corporate purposes ([fuente](https://www.roic.ai/quote/AMKR))
- 📅 2026-05-28: Acquired additional 67-acre parcel adjacent to 104-acre Peoria, Arizona campus ([fuente](https://evertiq.com/design/2026-05-28-amkor-acquires-additional-land-in-arizona-to-expand-advanced-packaging-campus))
- 📅 2026-07-27: Record Q2 2026 net sales $1.90B (+26% y/y); record Computing and Automotive & Industrial revenue ([fuente](https://ir.amkor.com/news-releases/news-release-details/amkor-technology-reports-financial-results-second-quarter-2026))
- 📅 2026-09-08: Arizona phase 2 (~60,000 m2 cleanroom) raises planned Arizona investment to ~$12B, citing customer commitments exceeding phase 1 capacity ([fuente](https://amkortech.gcs-web.com/node/24366))

## AEHR — Aehr Test Systems

- **Vínculo IA/energía/commodities:** Measurable: AI processors and silicon photonics burn-in were more than 80% of Q4 FY2026 revenue vs 56% in the prior-year quarter (company release). Company states hyperscale data center deployments drive silicon photonics screening demand; record bookings of $60.7M and effective backlog ~$100.6M; FY2027 revenue guidance $130M-$150M.
- ⚠️ Dilution: two fully-utilized ATM programs (~$40M and ~$60M) in FY2026 ([fuente](https://www.sec.gov/Archives/edgar/data/0001040470/000165495426003746/aehr_8k.htm))
- ⚠️ Customer concentration: top five ~77% of FY2025 net sales; three 10%+ customers in Q4 FY2026 ([fuente](https://www.sec.gov/Archives/edgar/data/1040470/000165495425008553/aehr_10k.htm))
- ⚠️ FY2026 revenue fell to $50.0M from $59.0M with GAAP net loss $(7.1)M; FY2027 guide implies large step-up dependent on backlog conversion ([fuente](https://www.aehr.com/2026/07/aehr-test-systems-reports-fiscal-2026-fourth-quarter-and-full-year-financial-results-with-record-quarterly-bookings-and-100-million-effective-backlog/))
- ⚠️ Fiscal-year change creates a short transition period (May 30-June 26, 2026), complicating comparisons ([fuente](https://marketchameleon.com/articles/b/2026/10/9/aehr-november-2-2026-q1-fy2027-earnings-call-fiscal-year-shift))
- 📅 2026-04-08: Entered $60M at-the-market equity program with William Blair and Craig-Hallum (commissions up to 3%) ([fuente](https://www.boardroomalpha.com/sec/aehr-8-k-2026-04-08-0001654954-26-003355))
- 📅 2026-04-17: 8-K: ATM fully utilized, ~$60.0M gross proceeds (earlier $40M ATM also fully utilized at ~$35.38 avg per Q3 call) ([fuente](https://www.sec.gov/Archives/edgar/data/0001040470/000165495426003746/aehr_8k.htm))
- 📅 2026-07-14: Q4 FY2026: record bookings $60.7M, backlog $80.6M at May 29 (effective ~$100.6M); FY2027 revenue guide $130-150M ([fuente](https://www.aehr.com/2026/07/aehr-test-systems-reports-fiscal-2026-fourth-quarter-and-full-year-financial-results-with-record-quarterly-bookings-and-100-million-effective-backlog/))
- 📅 2026-10-09: Scheduled Q1 FY2027 results for Nov 2, 2026, reflecting fiscal-year shift ([fuente](https://marketchameleon.com/articles/b/2026/10/9/aehr-november-2-2026-q1-fy2027-earnings-call-fiscal-year-shift))

## POWL — Powell Industries, Inc.

- **Vínculo IA/energía/commodities:** Measurable: Q3 FY2026 largest order was a data center order valued above $400M for behind-the-meter on-site generation assets; orders $934M (book-to-bill 3.0x), record backlog $2.4B. Commercial & Other Industrial revenue +54% y/y, Electric Utility +18%, Petrochemical -49% (company release). Energy link: LNG (~$60M) and petrochemical (~$75M) mega orders; oil & gas historically core end market.
- ⚠️ Order lumpiness / single-order concentration: one data center order >$400M represents a large share of $934M quarterly orders ([fuente](https://powellindustriesinc.gcs-web.com/news-releases/news-release-details/powell-industries-announces-third-quarter-fiscal-2026-results))
- ⚠️ Petrochemical revenue down 49% y/y in Q3 FY2026 ([fuente](https://powellindustriesinc.gcs-web.com/news-releases/news-release-details/powell-industries-announces-third-quarter-fiscal-2026-results))
- 📅 2025 (fiscal Q4 FY2025): Acquired U.K.-based Remsdaq Limited (SCADA remote terminal units); reported price ~$18.4M per secondary summary ([fuente](https://powellindustriesinc.gcs-web.com/node/15511/html))
- 📅 2025-11: FY2025 results: revenue $1.1B (+9%), net income $181M ($14.86/diluted share), backlog $1.4B at Sept 30, 2025 ([fuente](https://www.sec.gov/Archives/edgar/data/80420/000008042025000152/powl-20250930.htm))
- 📅 2026-08-03: Q3 FY2026: record orders $934M (+158%), record backlog $2.4B, including >$400M data center behind-the-meter generation order ([fuente](https://powellindustriesinc.gcs-web.com/news-releases/news-release-details/powell-industries-announces-third-quarter-fiscal-2026-results))

## HPS.A — Hammond Power Solutions Inc.

- **Vínculo IA/energía/commodities:** Measurable: Q2 2026 U.S. & Mexico sales C$272.7M (+73.0% y/y) of total C$325M; backlog +96.9% y/y primarily due to large project orders driven by data centre activity (company release). One call summary states data centers are now more than 30% of revenue (not confirmed in release). Commodity link: transformer input costs (copper, electrical steel) - not quantified in sources seen.
- ⚠️ New leverage: up to US$300M acquisition term debt for AEG (previously low-debt balance sheet) ([fuente](https://asia.hammondpowersolutions.com/news/2026/june/hps-completes-acquisition-of-aeg-power-solutions))
- ⚠️ YTD 2026 net earnings fell 26.9% to C$28.96M (higher share-based comp, SG&A, C$4.99M acquisition costs) despite sales growth ([fuente](https://www.globenewswire.com/news-release/2026/07/30/3336509/0/en/Hammond-Power-Solutions-Reports-Q2-2026-Financial-Results.html))
- ⚠️ Sequential backlog decline from Q1 2026 and dependence on large data-centre project orders ([fuente](https://www.globenewswire.com/news-release/2026/07/30/3336509/0/en/hammond-power-solutions-reports-second-quarter-2026-financial-results.html))
- 📅 2026-06-29: Completed acquisition of AEG Power Solutions for ~C$365M, funded by new secured credit facility (US$300M term + US$150M revolver) ([fuente](https://asia.hammondpowersolutions.com/news/2026/june/hps-completes-acquisition-of-aeg-power-solutions))
- 📅 2026-07-30: Record Q2 2026 sales C$325M (+44.7% y/y); adjusted EBITDA C$53.2M (+59.4%); backlog +96.9% y/y on data centre orders ([fuente](https://www.globenewswire.com/news-release/2026/07/30/3336509/0/en/hammond-power-solutions-reports-second-quarter-2026-financial-results.html))
- 📅 2026-07-30: Canada sales fell 23.7% y/y (timing of large custom projects); backlog declined sequentially from Q1 ([fuente](https://finance.yahoo.com/markets/stocks/articles/hammond-power-solutions-reports-second-211500499.html))

## NVT — nVent Electric plc

- **Vínculo IA/energía/commodities:** Measurable/company-stated: nVent expects more than $2B of 2026 data-center sales and cites a ~$2.5B (data center) backlog (Q2 2026 materials/coverage); new products contributed more than 30 points of Q2 sales growth. Expanding liquid-cooling manufacturing (Blaine 2, MN, 160,000 sq ft, production 1H 2027). Pending Maverick Power acquisition (~$700M est. 2026 revenue) adds data center switchgear. Utility/infrastructure (power) end markets also referenced; ~$100M incremental tariff impact expected in 2026.
- ⚠️ Acquisition leverage: Maverick deal funded with cash and new debt on top of $1.5B existing debt; up to $550M contingent consideration ([fuente](https://www.bnnbloomberg.ca/business/company-news/2026/08/24/nvent-electric-to-buy-maverick-power-for-us175-billion-to-expand-data-center-offerings/))
- ⚠️ Tariffs: ~ $100M incremental 2026 tariff impact expected ([fuente](https://www.benzinga.com/markets/earnings/26/07/60845239))
- ⚠️ Capacity investments expected to limit 2H 2026 incremental margins to mid-20% range (media) ([fuente](https://www.marketbeat.com/instant-alerts/nvent-electric-nysenvt-updates-q3-2026-earnings-guidance-2026-07-31/))
- 📅 2026-07-31: Q2 2026 sales +53% (organic +47%); raised FY2026 guidance (adj. EPS to $5.00-$5.10 from $4.45-$4.55) ([fuente](https://s22.q4cdn.com/268397047/files/content_files/Q2-2026-NVT-Press-Release.pdf))
- 📅 2026-07-31: Announced Blaine 2 (Minnesota) 160,000 sq ft liquid cooling manufacturing expansion, production expected 1H 2027; 2026 capex ~ $130M ([fuente](https://www.chartmill.com/news/external/64799/can-capacity-expansion-support-nvent-electric-s-next-growth-phase))
- 📅 2026-08-24: Agreed to acquire Maverick Power for $1.75B + up to $550M earnout; close expected Q4 2026 ([fuente](https://www.foley.com/news/2026/08/foley-represents-nvent-electric-in-1-75-billion-acquisition-of-maverick-power/))

## BWXT — BWX Technologies, Inc.

- **Vínculo IA/energía/commodities:** Measurable (FY2025): Government Operations revenue $2,350.1M and Commercial Operations $853.1M of $3,198.4M consolidated; Commercial grew 63% y/y; U.S. Government ~68% of 2025 consolidated revenue (10-K). Energy link: commercial nuclear power components, fuel and services (nuclear new-build/refurbishment demand). Q2 2026 Government Operations growth from naval propulsion and special materials, partially offset by lower advanced nuclear volumes. No company statement directly tying revenue to AI datacenters was seen.
- ⚠️ Government concentration: U.S. Government ~68% of 2025 revenue (appropriations/contract risk) ([fuente](https://www.sec.gov/Archives/edgar/data/1486957/000148695726000007/bwxt-20251231.htm))
- ⚠️ Amended quarterly report (10-Q/A) filed for Q2 2026 - reason not verified; should be reviewed ([fuente](https://www.sec.gov/Archives/edgar/data/0001486957/000148695726000044/bwxt-20260630.htm))
- ⚠️ Lower advanced nuclear volumes in Q2 2026 Government Operations ([fuente](https://www.businesswire.com/news/home/20260803655700/en/BWX-Technologies-Reports-Second-Quarter-2026-Results))
- 📅 2026-02-23: FY2025 results: revenue $3,198.4M; initiated 2026 guidance ([fuente](https://www.businesswire.com/news/home/20260223918392/en/BWX-Technologies-Reports-Fourth-Quarter-and-Full-Year-2025-Results-Initiates-2026-Guidance))
- 📅 2026-04-20: Agreed to acquire Precision Components Group (heavy manufacturing footprint); closed July 1, 2026 ([fuente](https://www.sec.gov/Archives/edgar/data/0001486957/000148695726000042/bwxt_63026x10qdocument.htm))
- 📅 2026-07-31: Agreed to sell medical business to Nordic Capital (value up to $800M) ([fuente](https://www.bwxt.com/bwxt-selling-medical-business-to-nordic-capital-in-transaction-valued-at-up-to-800-million/))
- 📅 2026-08-03: Q2 2026 revenue $901.6M (vs $764.0M), net income $89.1M, FCF $115.0M; raised FY2026 EBITDA/EPS/FCF guidance ([fuente](https://www.businesswire.com/news/home/20260803655700/en/BWX-Technologies-Reports-Second-Quarter-2026-Results))
