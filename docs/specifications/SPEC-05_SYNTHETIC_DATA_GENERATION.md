# SPEC-05 — Synthetic Data Generation

**Status:** Design-approved recommendation; documentation consistency gate passed  
**Version:** 1.0 · 2026-10-11  
**Scope:** Fictional Baltic Trade Supplies UAB; synthetic data only

## Purpose and generation contract
Generate deterministic, **fictional** truth datasets for SPEC-03 entities and finance-compliant commercial events under SPEC-04. No real people, customers, supplier lists or copied business records. Reference datasets before transactions; never fabricate hidden target labels into analysis tables.

## Configuration defaults and population
| Setting | Recommended baseline |
|---|---|
| master_seed | `20261010` |
| period | `2022-01-01` through `2026-09-30` |
| markets | LT, LV, EE |
| customers | 4,000 |
| products | 750 |
| salespeople | 30 |
| fulfilled+unfulfilled order target | ~120,000 (tolerance ±5%) |
| order lines | 250,000–400,000 |
| return events | 8,000–15,000, majority accepted |
| categories | exactly 12 from SPEC-02 |
| channels | exactly 5 from SPEC-02 |
| extension_end | 2026-12-31, configurable; do not report post-cutoff |
| CSV output | UTF-8, LF, YYYY-MM-DD dates, stable column/row order, decimal strings |

## Determinism and generators
- Use NumPy `Generator(PCG64(SeedSequence(master_seed, spawn_key=(stable_numeric_component_id,))))`; define stable numeric component IDs in config, never Python `hash()`, shared global RNG, object id or filesystem ordering. Components: geography=1, customers=2, products=3, salespeople=4, assignments=5, customer_history=6, orders=7, returns=8, targets=9, defects=10, stories=11. Within each component key all record draws from stable entity IDs or deterministic ordered chunk numbers to avoid execution-order drift.
- Stable sequential prefixed identifiers after deterministic sort of generated canonical records; explicit separate surrogate version IDs. Seed and config hash stored with manifest, library versions and exact output SHA-256. Repeat identical config = byte-identical files on supported platform. Different master seed changes non-key stochastic attributes; reference taxonomies stay stable.
- Avoid global `random`, wall-clock dependent names, unstable pandas grouping order, and floating point monetary posting. Convert sampled rates into `Decimal` using string-based conversion before posting. Generated truth does not contain injected defects.

## Geography and catalogue realism
- Use only the frozen SPEC-03 §§4.1–4.3 geography catalogue. Lithuania and Estonia use the documented county parents; Latvia uses the documented 2024 statistical/planning reporting regions for the whole synthetic window. The generator cannot invent regions/cities or reinterpret reporting regions as administrative units. City mix may be skewed to larger commercial centres, but weights are explicitly simulation assumptions.
- Product counts are allocated among 12 categories with explicit weights and the baseline four configured subcategories per category. Each product has exactly one valid primary supplier at any active date; 20% have an approved alternative (tolerance ±3 percentage points). No procurement fulfilment simulation. Category/subcategory stay stable. The baseline has exactly 80 suppliers; a deliberate config change may alter the count and must change the manifest/config hash. Reference pricing follows positive-skew category-specific bands, never one arbitrary uniform range.

## Customer, people and time distributions
- Geography weights suggested LT 0.55, LV 0.27, EE 0.18; bands ±0.03. Segments Small Business 0.40, Mid-Market 0.28, Enterprise 0.12, Public Sector 0.08, Trade/Reseller 0.12, each ±0.03. These are **simulation assumptions**, not claims about Baltic economies.
- Industry/segment/size jointly sampled with correlation but not deterministic identity. Customer acquisition hazard varies by year/market; include pre-period existing customers when explicitly configured. Generate administrative active/suspended/closed independently of purchase-derived churn. SCD Type 2 customer histories include meaningful minority segment/size/geography changes, non-overlapping `[from,to)` and one current version, without rewriting early orders.
- 30 reps apportioned by market with plausible uneven account books; hires, separations and ownership transfers effective-dated. No order credit outside employment. Orders can remain uncredited. No transfer duplicates or overlapping primary account owners.
- Date dimension covers reporting+configured extension; only realised reportable events within cutoff enter default dashboard.

## Transactions and commercial dynamics
- Build customer-month exposure conditional on acquisition date, segment, industry, latent purchasing propensity, seasonality and scenario shocks; use Poisson/negative-binomial style count models with overdispersion, avoiding fixed-frequency schedules. Do not leak latent customer propensities.
- Use category preference vectors (Dirichlet-like heterogeneity) and sparse baskets with repeat affinities. Prefer realistic 1–6 line orders with long tail; explicit larger account baskets; order-line quantity positive integer; no orders before customer acquisition or product launch.
- Channel mix depends on segment/rep ownership, but both direct and managed channels support null credit. Geographies can differ across customer home/ship-to/territory without being synonymous.
- Prices use historical reference list prices, negotiated discount bands and margin floors; supplier cost inflation and category drift documented in SPEC-06. Discount may be zero, but negative and >1 invalid. Fulfilled status dates can straddle month boundary; cancelled and placed amounts never counted as recognised sales.
- Sales targets generated independently from actual realised sales using historical plan baseline, modest annual growth, monthly seasonality and approved rep/market allocations, including unallocated country buckets. Do not back-solve targets to produce a chosen performance score.
- Return incidence depends on category, reason and known purchase history. Generate `requested_quantity` for all events and `accepted_quantity` only for accepted events; repeated accepted quantities are cumulatively capped. Recognition uses `return_decision_date`, posted refund, recovered COGS and VAT reversal exactly as SPEC-04.

## Scenario independence
- Private simulation controls in `data/truth/private/` are allowed for planted-story validation only. Normal business outputs whitelisted; scenario IDs, latent churn states, injected anomaly flags, risk tiers and source defect labels never pass to silver/warehouse/ML predictors. A discovered finding must emerge from observable data.

## Generation steps and outputs
1. Validate config and controlled reference taxonomies; fail before partial output on impossible configuration.
2. Generate geographic reference, channels, segments, industries, suppliers, categories/subcategories, products and salespeople.
3. Generate customer master, SCD history, account assignments and supplier relationship intervals.
4. Generate orders and lines; post finance snapshots and recognised dates per SPEC-04.
5. Generate returns, monthly target allocations, extended calendar.
6. Validate all entity/date/amount constraints; atomically publish sorted CSV/Parquet to truth folder and write manifest with hashes.
7. Generate raw clean copies, then defects only in SPEC-07 process. No fixture labels in analytical exports.

## Acceptance tests
- Exact reference counts; configured transaction ranges; every PK/FK valid and historical point-in-time join unique.
- Deterministic repeat-run SHA256, alternative seed changes non-key stochastic payload, master seed centrally configurable.
- Positive spread across all approved markets/categories/segments/channels; target unevenness and nontrivial segment variation.
- Financial reconciliation exact to cents; no post-cutoff leakage; return caps hold across repeated returns.
- Distribution-summary CSV compares actual fractions to configured tolerance and fails on unrealistic degenerate distributions.

## Governance, dependencies, and change control

**Project contract:** All identities and commercial events are synthetic. Analysis covers LT/LV/EE in EUR, reporting 2022-01-01 through 2026-09-30, without implying an actual client engagement. SPEC-01 and SPEC-02 define intent; SPEC-03 governs source grain and identities; SPEC-04 governs economics; SPEC-05/06 govern generated distributions and private truth; SPEC-07–09 govern transport and warehouse; SPEC-10–15 govern analytical outputs; SPEC-16 governs verification.

Any change to a grain, formula, inclusion criterion, as-of rule, enum, field name, or security boundary requires a documented decision and regeneration of dependent fixtures. Do not silently reinterpret an approved contract inside code.

## Failure handling and edge cases

- Null denominators, zero activity, and insufficient observation windows produce explicit `NULL`/not-estimable values and explanatory flags, never disguised zeros.
- Historical calculations exclude any observation after the stated as-of cutoff, even if the synthetic generator knows the future.
- Duplicate source records, orphan relationships, impossible dates and unreconciled amounts fail validation or enter a traceable quarantine; they are not silently removed.
- Outputs must state unit, currency, grain, period, filters, and whether results represent observations, estimates, or synthetic scenario validation.

## Approval and implementation gate

**Design recommendation:** Accepted under project-owner advance approval dated 2026-10-11. The documentation consistency audit passed; automated integrity, reconciliation and release tests remain unexecuted. **Implementation is not authorised merely by writing this document.**
