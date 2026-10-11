# SPEC-03 — Data Model and Dictionary

**Project:** Baltic Sales Intelligence  
**Business:** Baltic Trade Supplies UAB (entirely fictional)  
**Document status:** Remediated contract 1.0 — cross-specification audit complete  
**Authority:** Approved SPEC-01 charter (scope as established); approved SPEC-02 fictional business model  
**Date:** 2026-10-11  
**Implementation permission:** Not granted

> This is the implementation-ready documentation contract produced by the cross-specification audit. Project implementation remains separately gated. No synthetic names or company records represent actual customers, suppliers or employees.

## 1. Purpose and business rationale

Define the operational synthetic-data entities and planned analytical warehouse interfaces so that data generation, validation, reconciliation and advanced business analysis have one precise, testable vocabulary. The objective is to prevent ambiguous grain, joins, historical attribution, and financial-field definitions before implementation.

## 2. Scope and exclusions

**In scope:** canonical source entity contracts; type conventions; keys and foreign keys; reference taxonomies; valid-date relationships; ownership and attribution history; target grain; geography; suggested warehouse dimensional mapping; testable constraints.

**Out of scope:** actual data generation and numerical distributions (SPEC-05); synthetic commercial scenarios (SPEC-06); accounting formulas, VAT amounts/rates, freight allocation and return sign conventions (SPEC-04); concrete DuckDB physical DDL/marts (SPEC-09); defect and quarantine rules (SPEC-07); operational integrations and real personal information.

**Contract boundary:** The operational source tables below are the proposed canonical **clean truth** business entities. Milestones later produce raw, validated/quarantined, silver, and analytical tables without importing private simulation-control labels into reporting.

## 3. Type, naming and key conventions

| Area | Proposed convention | Invariant |
|---|---|---|
| Column names | lower `snake_case` | No synonyms for a single concept |
| IDs | stable synthetic strings, prefixed by entity (`CUST-`, `PROD-`, `SP-`, etc.) | Unique and immutable within entity; no random UUID required |
| Foreign keys | same name and type as referenced PK | No silently invented orphan links |
| Dates | logical `DATE`, `YYYY-MM-DD` in canonical CSV | Inclusive analysis window unless specific exception |
| Event granularity | `DATE` only for commercial events; half-open `[valid_from, valid_to)` history | NULL end means open-ended; same-day sequencing uses explicit business rules |
| Text | UTF-8 | No uncontrolled null-like strings |
| Decimals | `DECIMAL(18,4)` for monetary unit values and calculation intermediates; `DECIMAL(18,2)` for final rounded monetary amounts | No binary floating point financial arithmetic; SPEC-04 defines rounding boundaries |
| Quantities | `INTEGER` where discrete; larger numeric type only with documented need | Positive sold quantity, positive returned quantity |
| Percentages | `DECIMAL(9,6)` fractions between 0 and 1 | Display percentage conversion only in presentation |
| Boolean | `BOOLEAN` | No ambiguous Y/N strings |
| Enum | case-stable controlled values in reference catalogue | Unknowns remain DQ problems, not silently reclassified |
| Missingness | NULL only for explicitly optional fields | A blank value never means zero |

Identifiers are not business meanings: do not derive country, scenario type, churn status, region or profitability from an ID. Deterministic ID assignment follows SPEC-05.

### 3.1 Applicability codes

In all tables, `R` = mandatory, `O` = optional conditional on rules, `U` = uniqueness requirement, `FK` = foreign key. Stated nullability is contractual. `DATE` is a logical data type, not a recommendation to store timestamps where only a calendar day is known.

### 3.2 Source-system lineage fields

For **all operational source entities**, the pipeline should add lineage outside the business contract (e.g. `source_file`, `ingested_at`, `batch_id`), not fake those as attributes of a fictional ERP. The canonical truth generation can emit dataset-level manifests with seeds, schema versions, row counts and SHA-256 values. Detailed manifests and provenance belong to SPEC-05/08/16.

## 4. Reference entities

### 4.1 `countries` — one row per permitted operating market

**Grain:** One market/country. **Primary key:** `country_code`.

| Field | Type | Rule | Meaning |
|---|---|---|---|
| `country_code` | VARCHAR(2) | R, PK | ISO-style `LT`, `LV`, `EE` |
| `country_name` | VARCHAR | R, U | Lithuania, Latvia, Estonia |
| `reporting_currency` | CHAR(3) | R | `EUR` |

Exactly three records. Do not infer tax treatment from market code.

### 4.2 `regions` — one row per approved geographical reporting region

**Grain:** One region within a country. **Primary key:** `region_id`.

| Field | Type | Rule | Meaning |
|---|---|---|---|
| `region_id` | VARCHAR | R, PK | Stable geographic reporting region ID |
| `country_code` | VARCHAR(2) | R, FK→countries | Parent market |
| `region_name` | VARCHAR | R | Display name |
| `region_scheme` | VARCHAR | R | Named administrative/reporting scheme |

`(country_code, region_name, region_scheme)` is unique. The project uses a deliberately small, frozen locality catalogue. Lithuanian and Estonian parents are administrative counties; Latvian parents are the five official statistical/planning regions in force from 2024-01-01. Latvia's reporting regions are not presented as first-level administrative units. The 2024 Latvian scheme is used consistently for the entire synthetic analysis window to avoid a break in reporting geography; it is a reporting convention, not a claim that the same statistical partition applied before 2024.

| `region_id` | `country_code` | `region_name` | `region_scheme` | Region nature |
|---|---|---|---|---|
| `LT-VL` | LT | Vilnius County | `LT_COUNTY_NUTS3_2018` | Administrative county / NUTS 3 |
| `LT-KU` | LT | Kaunas County | `LT_COUNTY_NUTS3_2018` | Administrative county / NUTS 3 |
| `LT-KL` | LT | Klaipeda County | `LT_COUNTY_NUTS3_2018` | Administrative county / NUTS 3 |
| `LT-SA` | LT | Siauliai County | `LT_COUNTY_NUTS3_2018` | Administrative county / NUTS 3 |
| `LT-PN` | LT | Panevezys County | `LT_COUNTY_NUTS3_2018` | Administrative county / NUTS 3 |
| `LV-RI` | LV | Riga Statistical Region | `LV_STAT_REGION_2024` | Statistical/planning region |
| `LV-LG` | LV | Latgale Statistical Region | `LV_STAT_REGION_2024` | Statistical/planning region |
| `LV-KU` | LV | Kurzeme Statistical Region | `LV_STAT_REGION_2024` | Statistical/planning region |
| `LV-ZE` | LV | Zemgale Statistical Region | `LV_STAT_REGION_2024` | Statistical/planning region |
| `EE-37` | EE | Harju County | `EE_COUNTY_CURRENT` | Administrative county |
| `EE-79` | EE | Tartu County | `EE_COUNTY_CURRENT` | Administrative county |
| `EE-45` | EE | Ida-Viru County | `EE_COUNTY_CURRENT` | Administrative county |
| `EE-67` | EE | Parnu County | `EE_COUNTY_CURRENT` | Administrative county |

The Latin-ASCII display spellings in this contract provide stable identifiers and exports; user-facing presentation may use native diacritics from a separate display label. Evidence used to validate the scheme: Statistics Lithuania's administrative division (counties are NUTS 3), Latvia's Central Statistical Bureau notice defining five statistical regions from 2024-01-01, Eurostat's NUTS/LAU hierarchy, and Statistics Estonia's administrative-unit publications. URLs and evidence dates are recorded in §12.2.

### 4.3 `cities` — one row per modelled city/locality

**Grain:** One locality in one region. **Primary key:** `city_id`.

| Field | Type | Rule | Meaning |
|---|---|---|---|
| `city_id` | VARCHAR | R, PK | Stable locality ID |
| `region_id` | VARCHAR | R, FK→regions | Parent region |
| `country_code` | VARCHAR(2) | R | Redundant validation code; must match region parent |
| `city_name` | VARCHAR | R | Geographical name |

Unique `(region_id, city_name)`; cross-field country consistency is mandatory. This is the complete launch catalogue, not an illustrative list:

| `city_id` | `city_name` | `region_id` | `country_code` |
|---|---|---|---|
| `LT-VILNIUS` | Vilnius | `LT-VL` | LT |
| `LT-KAUNAS` | Kaunas | `LT-KU` | LT |
| `LT-KLAIPEDA` | Klaipeda | `LT-KL` | LT |
| `LT-SIAULIAI` | Siauliai | `LT-SA` | LT |
| `LT-PANEVEZYS` | Panevezys | `LT-PN` | LT |
| `LV-RIGA` | Riga | `LV-RI` | LV |
| `LV-DAUGAVPILS` | Daugavpils | `LV-LG` | LV |
| `LV-LIEPAJA` | Liepaja | `LV-KU` | LV |
| `LV-JELGAVA` | Jelgava | `LV-ZE` | LV |
| `LV-VENTSPILS` | Ventspils | `LV-KU` | LV |
| `EE-TALLINN` | Tallinn | `EE-37` | EE |
| `EE-TARTU` | Tartu | `EE-79` | EE |
| `EE-NARVA` | Narva | `EE-45` | EE |
| `EE-PARNU` | Parnu | `EE-67` | EE |

Adding a locality or changing a parent requires evidence from an official national source or Eurostat LAU correspondence, a reference-data version change and regression of `DATA-T03`; the generator cannot add free-text localities.

### 4.4 Controlled enumeration catalogue

The following values are closed for launch. Unknown values fail validation; they are never mapped to the nearest label.

| Domain | Allowed codes | Contract |
|---|---|---|
| customer account status | `active`, `suspended`, `closed` | Administrative state only; never analytical lifecycle |
| company size band | `micro`, `small`, `medium`, `large` | SCD2 attribute; synthetic internal bands configured by employee-count proxy, not an external legal classification |
| credit tier | `tier_1`, `tier_2`, `tier_3`, NULL | Fictional commercial tier; NULL means not assessed |
| product lifecycle | `active`, `discontinued` | `discontinued` requires `discontinued_date` |
| payment terms | `prepaid`, `net_14`, `net_30`, `net_45` | Commercial terms only |
| order status | `placed`, `fulfilled`, `cancelled` | Date rules in §6.1 |
| return status | `requested`, `accepted`, `rejected` | Financial effect only for `accepted` |
| return disposition | `restockable`, `written_off` | Required only for accepted returns |
| supplier relationship | `primary`, `alternative` | Effective-dated rules in §5.5 |
| target allocation | `salesperson`, `unallocated_country` | Mutually exclusive key rules in §6.4 |
| target metric | `net_revenue_eur`, `gross_profit_eur` | Actuals use SPEC-04 posting definitions |
| VAT treatment | `sim_standard`, `sim_zero` | Illustrative simulation convention, never tax advice |

Return reasons, five customer segments, eleven industries, twelve categories and five channels are also closed enumerations as listed in §§4.5 and 6.3. Supplier country, if populated, uses ISO 3166-1 alpha-2 and is descriptive only.

### 4.5 `customer_segments`, `industries`, `product_categories`, `product_subcategories`, `suppliers`, `sales_channels`

Each is a **separate controlled reference entity**, preventing free-text proliferation.

| Entity | Primary key | Other mandatory fields | Integrity |
|---|---|---|---|
| `customer_segments` | `segment_code` | `segment_name` | Exactly five approved baseline names |
| `industries` | `industry_code` | `industry_name` | Approved initial 11-industry taxonomy |
| `product_categories` | `category_id` | `category_name` | Exactly 12 baseline categories |
| `product_subcategories` | `subcategory_id` | `category_id`, `subcategory_name` | FK to categories; `(category_id, subcategory_name)` unique |
| `suppliers` | `supplier_id` | `supplier_name`, `supplier_country_code` (optional) | All names invented; country may be outside LT/LV/EE if modelled and separately validated |
| `sales_channels` | `channel_code` | `channel_name` | Exactly five approved baseline names |

Reference lists:

- Segments: **Small Business; Mid-Market; Enterprise; Public Sector; Trade / Reseller**.
- Industries: **Manufacturing; Construction; Logistics; Automotive; Facilities Management; Agriculture; Food Processing; Retail; Engineering; Utilities; Public Sector**.
- Categories: **Safety Equipment; Hand Tools; Power Tool Accessories; Electrical; Fasteners; Maintenance Supplies; Workwear; Cleaning & Hygiene; Storage & Handling; Workshop Equipment; Industrial Consumables; Outdoor & Site Equipment**.
- Channels: **Account Manager; Inside Sales; Web; Email / Phone Order; Trade Counter**.

Subcategories and suppliers are generated master-data rows, not free-text enums: their complete rows, codes and counts must be declared in versioned generator configuration and frozen in the build manifest. Baseline configuration uses four subcategories per category and 80 fictional suppliers; changing either count changes the config hash. Do not derive segment mechanically from industry; `Public Sector` is legitimate on both independent axes.

## 5. Core master data

### 5.1 `customers` — stable identity and administrative master

**Grain:** One synthetic customer organisation. **PK:** `customer_id`. **Scale:** approximately 4,000.

| Field | Type | Rule | Meaning |
|---|---|---|---|
| `customer_id` | VARCHAR | R, PK | Immutable synthetic customer business ID |
| `customer_name` | VARCHAR | R | Fictional organisation name |
| `acquisition_date` | DATE | R | Date of relationship/onboarding |
| `account_status` | VARCHAR | R | Administrative CRM state from §4.4 |
| `industry_code` | VARCHAR | R, FK→industries | Stable industry for the study; no historical industry changes |
| `credit_tier` | VARCHAR | O | Fictional internal commercial classification, not an external score |
| `preferred_channel_code` | VARCHAR | O, FK→sales_channels | Declared preference, not observed channel dominance |

**Rule:** Customer attributes required for historical analysis are held in `customer_attribute_history`, not overwritten on this identity record. The analytic states new/active/declining/lapsed/reactivated **must be derived from eligible transaction history** using SPEC-10 thresholds; they are not stored here. Customer geography for any historical event is resolved through the history table. No order precedes acquisition.

### 5.1a `customer_attribute_history` — SCD Type 2 source history

**Grain:** One version of historically tracked customer attributes for an effective interval. **PK:** `customer_version_id`; stable business FK `customer_id`.

| Field | Type | Rule | Meaning |
|---|---|---|---|
| `customer_version_id` | VARCHAR | R, PK | Deterministic version surrogate (separate from customer_id) |
| `customer_id` | VARCHAR | R, FK→customers | Stable customer identifier |
| `valid_from` | DATE | R | Inclusive start of attribute state |
| `valid_to` | DATE | O | Exclusive end; NULL for current version |
| `segment_code` | VARCHAR | R, FK→customer_segments | Segment valid during version |
| `company_size_band` | VARCHAR | R | Size band valid during version; enum in §4.4 |
| `home_city_id` | VARCHAR | R, FK→cities | Home city valid during version |
| `home_region_id` | VARCHAR | R, FK→regions | Home region; must match city parent |
| `home_country_code` | VARCHAR(2) | R, FK→countries | Home country; must match region parent |
| `is_current` | BOOLEAN | R | True iff this version is current, consistent with valid_to |

**Constraints:** Per customer, intervals never overlap; exactly one current version; every date from acquisition through the configured extension resolves to exactly one valid version. A changed segment, company-size band, or home geography creates a new version. Null end is the single current version. Avoid future-state information when reporting as-of earlier dates. A version created by migration preserves `customer_id`. Customer name changes need not create a version. Industry is stable on `customers`. Geography on shipping orders remains independent and immutable for the order.

### 5.2 `salespeople`

**Grain:** One synthetic employee/representative. **PK:** `salesperson_id`. **Nominal scale:** 30.

| Field | Type | Rule | Meaning |
|---|---|---|---|
| `salesperson_id` | VARCHAR | R, PK | Stable salesperson ID |
| `salesperson_name` | VARCHAR | R | Fictional name |
| `primary_country_code` | VARCHAR(2) | R, FK→countries | Primary operating market |
| `primary_region_id` | VARCHAR | O, FK→regions | Primary region, if allocated |
| `hire_date` | DATE | R | Earliest valid credited employment date |
| `termination_date` | DATE | O | Exclusive end of employment eligibility |
| `manager_salesperson_id` | VARCHAR | O, self-FK | Manager, only if hierarchy is modelled |

**Rules:** `hire_date < termination_date` when present; an employee is eligible on `d` when `hire_date <= d < termination_date` (or end is NULL). Primary region, if present, must be within primary market; manager may not equal employee; cycles are prohibited if hierarchy is enabled. The model must not assign credit outside the eligibility interval. Primary market is a current descriptive attribute; historic target country is frozen on each target row and historic order credit is frozen on each order.

### 5.3 `customer_account_assignments` — effective-dated ownership

**Grain:** One uninterrupted interval of primary account ownership for one customer. **PK:** `assignment_id`.

| Field | Type | Rule | Meaning |
|---|---|---|---|
| `assignment_id` | VARCHAR | R, PK | Deterministic interval identity |
| `customer_id` | VARCHAR | R, FK→customers | Owned account |
| `salesperson_id` | VARCHAR | R, FK→salespeople | Historical primary owner |
| `valid_from` | DATE | R | Start inclusive |
| `valid_to` | DATE | O | End exclusive; NULL if open-ended |

**Constraints:** At most one primary account owner for a customer on a given date; gaps permitted and interpreted as unassigned. No owner before acquisition or employment; end intervals on or before termination boundary as governed by the date-level employment convention. Effective-date join returns zero or one account owner. Historical ownership **does not automatically determine** order credit. Transfer treatment must be deterministic and separately tested.

### 5.4 `products` — immutable catalogue identity and stable classifications

**Grain:** One catalogue SKU. **PK:** `product_id`. **Scale:** approximately 750.

| Field | Type | Rule | Meaning |
|---|---|---|---|
| `product_id` | VARCHAR | R, PK | Stable product identity |
| `sku` | VARCHAR | R, U | Stable fictional SKU |
| `product_name` | VARCHAR | R | Fictional descriptive name |
| `subcategory_id` | VARCHAR | R, FK→product_subcategories | Subcategory and thus category are historically stable |
| `launch_date` | DATE | R | First eligible sale date |
| `discontinued_date` | DATE | O | Exclusive end of sale eligibility, once policy finalised |
| `lifecycle_status` | VARCHAR | R | Administrative catalogue status from §4.4 |
| `reference_list_unit_price_eur` | DECIMAL(18,4) | R | Current catalogue reference, not historical sale price |
| `reference_standard_unit_cost_eur` | DECIMAL(18,4) | R | Current standard cost, not historical actual unit cost |

Product category/subcategory are fixed for the synthetic study (no product SCD Type 2). Catalogue price/cost may change but must never rewrite historical order-line snapshots. The temporal catalogue reference-price mechanism is owned by SPEC-05, and transaction financial semantics by SPEC-04.

### 5.5 `product_supplier_relationships` — effective-dated approved sourcing

**Grain:** One approved product–supplier relationship over a half-open interval. **PK:** `product_supplier_relationship_id`.

| Field | Type | Rule | Meaning |
|---|---|---|---|
| `product_supplier_relationship_id` | VARCHAR | R, PK | Stable relationship-version key |
| `product_id` | VARCHAR | R, FK→products | Catalogue product |
| `supplier_id` | VARCHAR | R, FK→suppliers | Approved fictional supplier |
| `relationship_type` | VARCHAR | R | `primary` or `alternative` |
| `valid_from` | DATE | R | Inclusive sourcing approval start |
| `valid_to` | DATE | O | Exclusive end; NULL if current |

At each relevant product-active date there is **exactly one primary supplier**, with zero or more approved alternatives. A supplier may not have overlapping relationship intervals for the same product, irrespective of relationship type; no duplicate current associations. Supplier switching may occur over time. A sourcing relationship alone is **not proof of which supplier supplied an individual sale**. Historical sale-to-supplier attribution needs explicit transaction provenance if modelled, and must otherwise be marked unknown. No purchase-order, inventory or procurement-optimisation model is introduced.

## 6. Operational commercial events

### 6.1 `orders`

**Grain:** One commercial order header. **PK:** `order_id`. **Scale:** approximately 120,000.

| Field | Type | Rule | Meaning |
|---|---|---|---|
| `order_id` | VARCHAR | R, PK | Immutable order ID |
| `customer_id` | VARCHAR | R, FK→customers | Purchasing customer |
| `order_date` | DATE | R | Date placed (order-intake date) |
| `fulfilled_date` | DATE | O | Required iff fulfilled; financial recognition date subject to SPEC-04 |
| `cancelled_date` | DATE | O | Required iff cancelled |
| `channel_code` | VARCHAR | R, FK→sales_channels | Channel of order placement |
| `order_status` | VARCHAR | R | One of `placed`, `fulfilled`, `cancelled` |
| `ship_to_city_id` | VARCHAR | R, FK→cities | Fixed order-level shipping location |
| `credited_salesperson_id` | VARCHAR | O, FK→salespeople | Order sales credit; does NOT default to account owner |
| `payment_terms_code` | VARCHAR | O | Controlled terms from §4.4 |
| `outbound_freight_eur` | DECIMAL(18,2) | R | Nonnegative fulfilled-order freight cost; zero for placed/cancelled orders |

**Status/date constraint:** `placed`: no fulfilled/cancelled dates; `fulfilled`: `fulfilled_date` populated and `cancelled_date` NULL; `cancelled`: `cancelled_date` populated and `fulfilled_date` NULL. Event date >= order date; same-day events are allowed. Cancelled orders cannot become fulfilled without an explicitly auditable source correction. A source row represents the final known state with milestone dates; historical as-of reporting gates finance by `fulfilled_date`, never by order date or current owner. Reporting order intake uses `order_date`; recognised sales use `fulfilled_date`. Order date cannot precede customer acquisition. Credited salesperson employment eligibility is evaluated on `fulfilled_date` for fulfilled orders and `order_date` otherwise. Shipping may be cross-border and is not derived from home location. No partial fulfilment or backorder logic. A fulfilled order has at least one line; cancelled/placed orders may retain one or more contracted lines but have zero recognised postings.

### 6.2 `order_lines`

**Grain:** One product line on an order. **PK:** `order_line_id`.

| Field | Type | Rule | Meaning |
|---|---|---|---|
| `order_line_id` | VARCHAR | R, PK | Stable order line ID |
| `order_id` | VARCHAR | R, FK→orders | Parent order |
| `line_number` | INTEGER | R | Positive order-local sequence; unique with order_id |
| `product_id` | VARCHAR | R, FK→products | Product SKU |
| `quantity_ordered` | INTEGER | R | Positive whole units; no partial-fulfilment quantities |
| `list_unit_price_eur` | DECIMAL(18,4) | R | Historical list unit price snapshot |
| `discount_rate` | DECIMAL(9,6) | R | Fraction in [0,1]; zero allowed; the only launch discount input |
| `realised_unit_price_eur` | DECIMAL(18,4) | R | Historical ex-VAT net unit price after discount |
| `unit_cost_eur` | DECIMAL(18,4) | R | Historical ex-VAT direct product cost per fulfilled unit |
| `posted_list_value_eur` | DECIMAL(18,2) | R | Rounded line list value for fulfilled orders; otherwise zero |
| `posted_discount_eur` | DECIMAL(18,2) | R | Rounded discount for fulfilled orders; otherwise zero |
| `posted_net_sales_eur` | DECIMAL(18,2) | R | Fulfilled ex-VAT revenue posting; otherwise zero |
| `posted_cogs_eur` | DECIMAL(18,2) | R | Fulfilled direct-cost posting; otherwise zero |
| `tax_jurisdiction_code` | VARCHAR(2) | R | Explicit simulation jurisdiction (`LT`, `LV`, `EE`); never inferred during reporting |
| `vat_treatment_code` | VARCHAR | R | `sim_standard` or `sim_zero` |
| `vat_rate_fraction` | DECIMAL(9,6) | R | Historical rate snapshot matching the treatment table |
| `posted_taxable_base_eur` | DECIMAL(18,2) | R | Equals posted net sales at launch |
| `posted_vat_amount_eur` | DECIMAL(18,2) | R | VAT posting rounded per SPEC-04 |
| `posted_invoice_total_eur` | DECIMAL(18,2) | R | Taxable base plus VAT |

All snapshots are immutable except through auditable correction. `realised_unit_price_eur = list_unit_price_eur × (1 − discount_rate)` to four decimals under the generator quantisation rule; the posted fields are authoritative after half-even cent rounding and must satisfy SPEC-04. There is no fixed discount amount input. Product eligibility must hold at order date and fulfilment date. Posted finance fields are zero for placed/cancelled orders.

### 6.3 `returns`

**Grain:** One return event against exactly one eligible fulfilled order line; repeated partial returns allowed. **PK:** `return_id`.

| Field | Type | Rule | Meaning |
|---|---|---|---|
| `return_id` | VARCHAR | R, PK | Deterministic return event ID |
| `order_line_id` | VARCHAR | R, FK→order_lines | Referenced fulfilled sale line |
| `return_requested_date` | DATE | R | Request date, on/after original fulfilment |
| `return_decision_date` | DATE | O | Required for accepted/rejected; NULL while requested; recognition date when accepted |
| `requested_quantity` | INTEGER | R | Positive units requested |
| `accepted_quantity` | INTEGER | O | Positive and ≤ requested quantity iff accepted; otherwise NULL |
| `return_status` | VARCHAR | R | `requested`, `accepted`, or `rejected` |
| `return_reason_code` | VARCHAR | R | Controlled reason |
| `restocking_outcome_code` | VARCHAR | O | Required iff accepted: `restockable` or `written_off` |
| `refund_unit_ex_vat_eur` | DECIMAL(18,4) | O | Required iff accepted; historical per-unit refund basis |
| `posted_refund_ex_vat_eur` | DECIMAL(18,2) | R | Accepted refund posting; zero otherwise |
| `posted_recovered_cogs_eur` | DECIMAL(18,2) | R | Accepted cost recovery; zero unless restockable |
| `posted_vat_reversal_eur` | DECIMAL(18,2) | R | Accepted VAT reversal at original rate; zero otherwise |
| `returns_freight_eur` | DECIMAL(18,2) | R | Nonnegative return-period freight cost; zero if none |

Allowed reason codes: `defective`, `wrong_item`, `damaged_in_transit`, `ordered_in_error`, `quality_issue`, `not_as_expected`, `duplicate_order`. Decision date is on/after request date and fulfilment date. Cumulative `accepted_quantity` per line cannot exceed sold quantity; requested and rejected quantities do not consume the cap. Refund is capped at `accepted_quantity ×` original realised unit price. Recovered COGS equals original unit cost times accepted quantity only when restockable, subject to posted-cent rounding. Post-cutoff events can exist through the configured extension but are excluded from reporting at an earlier cutoff.

### 6.4 `sales_targets` — canonical allocation-level plan

**Grain:** One target month × metric × allocation key; company and country totals are *derived*, never simultaneously stored as additive facts. **PK:** `target_id`.

| Field | Type | Rule | Meaning |
|---|---|---|---|
| `target_id` | VARCHAR | R, PK | Stable allocation target ID |
| `target_month` | DATE | R | First calendar day of month |
| `metric_code` | VARCHAR | R | Target KPI, defined by SPEC-04 |
| `allocation_type` | VARCHAR | R | `salesperson` or `unallocated_country` |
| `salesperson_id` | VARCHAR | O, FK→salespeople | Required iff allocation_type=`salesperson` |
| `country_code` | VARCHAR(2) | R, FK→countries | Target's **historical** allocated country, also for unallocated bucket |
| `target_value` | DECIMAL(18,2) | R | Allocated monetary target; other target units need explicit typed policy in SPEC-04 |

A salesperson has at most one target per month and metric; `unallocated_country` is unique by month, country and metric. For salesperson rows, `salesperson_id` is required; for unallocated rows it is NULL. Country totals aggregate all leaf rows carrying that country, including its unallocated bucket. Company total is the sum of country totals, equivalently all leaf rows once: `company_total = Σ salesperson allocations + Σ unallocated_country allocations`. No company or separate country-total fact rows exist. `country_code` is a historical allocation snapshot and is never overwritten. Allowed metrics are `net_revenue_eur` and `gross_profit_eur`. A salesperson is eligible for a monthly target when employed for at least one calendar day in the month; transfers use one frozen country selected by the country effective on the first eligible day. No proration is required; any unassigned country plan remains in its unallocated bucket. Salesperson actual is credited postings only; country/company actual includes credited and uncredited postings using customer-home country at posting date.

## 7. Relationships and event-time integrity

```text
countries ─< regions ─< cities
                         ├─< customer_attribute_history (home_city_id)
                         └─< orders (ship_to_city_id)
customers ─< customer_attribute_history >─ customer_segments
customers ─< customer_account_assignments >─ salespeople
customers ─< orders >─ sales_channels
orders ─< order_lines >─ products >─ product_subcategories >─ product_categories
order_lines ─< returns
products ─< product_supplier_relationships >─ suppliers
salespeople ─< orders (optional credit)
salespeople ─< sales_targets (optional in unallocated allocations)
```

`─<` represents one-to-many; all declared FKs must resolve. `customer_attribute_history` as-of interval joins and account ownership as-of joins must yield one and at most one historical version, respectively. Never join facts directly through a multi-valued supplier or returns bridge without controlled aggregation. Customer home geography comes from the SCD version at the analysis date; ship-to geography is fixed on the order; credited salesperson and territory are distinct business roles.

**Temporal tests:** no orders before acquisition; customer history covers eligible dates without overlap; ownership and supplier primary assignment use half-open intervals; product available by fulfilment; return follows fulfilment; salesperson employment and credit align under SPEC-04; order status date rules hold; targets are month-start; post-cutoff events are excluded from as-of metrics. Final-date comparison rules must be time-aware and avoid use of future state.

## 8. Reporting calendar / date dimension inputs

**Official reporting window:** 2022-01-01 through 2026-09-30 inclusive. **Planned `dim_date` grain:** one calendar day covering the reporting window **plus an explicitly configured extension** as needed for legitimate post-cutoff events (SPEC-05). Extended dates exist as calendar references only; they do not automatically extend financial reporting.

Candidate `dim_date` fields: `date_key` (YYYYMMDD integer), `full_date` (DATE), `calendar_year`, `calendar_quarter`, `calendar_month`, `month_start_date`, `iso_week`, `iso_week_year`, `iso_weekday`, `day_of_month`, `day_of_year`, `is_weekend`, `is_within_reporting_window`. `country_code` belongs only in a separate market-calendar bridge if local holidays are approved. ISO week-year must be separate from calendar year. Targets join by the first calendar day of the month. SPEC-05 defines the extension length and allowed post-period event types; SPEC-04 and SPEC-09 define financial cutoff rules.

## 9. Analytics warehouse mapping (logical contract, not physical DDL)

| Source or reference | Proposed warehouse role | Grain / historical resolution |
|---|---|---|
| Countries, regions, cities | Conformed geography dimension | One referenced locality and role-specific home/ship/territory keys |
| Customers + customer_attribute_history | `dim_customer` SCD Type 2 | One effective customer attribute version per customer and date |
| customer_account_assignments | Effective-dated account-owner bridge | Optional owner as of event date; no overwriting history |
| Products, stable categories/subcategories | `dim_product` | One stable product per SKU; catalogue price changes not historical fact |
| Product-supplier relationships | Supplier dimension and sourcing-approval bridge | One primary + zero/many alternatives by date; no assumed actual source |
| Salespeople and sales channels | Conformed dimensions | Sales credit and placement channel separate |
| Orders + order_lines | `fact_sales_order_line` | One eligible fulfilled order line, valued at fulfilment date |
| Returns | `fact_return_event` | One return event per order line, recognised per SPEC-04 |
| Canonical sales_targets | `fact_sales_target_allocation` | Month × measure × salesperson/unallocated bucket; rollups derived |
| Calendar | `dim_date` | One day including explicit extension, flagged for reporting eligibility |

The operational IDs are business/natural keys; warehouse SCD versions use separate surrogate keys and resolve to the version effective on the relevant event date. Physical star schema, surrogate key implementation and conformance rules belong to SPEC-09. Warehouse mappings are explicit, never implicit equivalence of operational and analytical rows.

### 9.1 Grain and join-safety rules

- Do not join return events to order headers and sum line sales without collapsing return grain or using controlled separate facts.
- Do not multiply product sales by joining multi-supplier relationships without filtering by time and relationship role.
- Customer attributes must join via business ID **and** effective date; account ownership uses its own independent interval join.
- Company/country targets aggregate canonical allocation rows; no duplicated hierarchy-level facts.
- Customer-home, shipment, and territory geography are role-playing dimensions, not additive revenue measures.
- Use financial date, order-intake date and return date according to SPEC-04; never use latest order status to infer past revenue without the actual fulfilment date.
- Generator-hidden simulation labels are excluded at every public layer.

## 10. Data inputs, outputs, interfaces and ownership

| Interface | Expected output | Authority |
|---|---|---|
| SPEC-03 → SPEC-05 | Entity contracts and uniqueness/referential rules | This spec after approval |
| SPEC-04 → SPEC-03 amendment | Final monetary fields, statuses, posting and refunds | SPEC-04 overrides provisional financial semantics by change control |
| SPEC-05 → SPEC-07 | Truth datasets and deterministic raw copies | SPEC-05, SPEC-07 |
| SPEC-07 → SPEC-09 | Validated silver records and DQ audit | SPEC-07, SPEC-08 |
| SPEC-09 → analytics | Conformed dimensions and correctly grained facts | SPEC-09–SPEC-14 |
| SPEC-06 → scenario validation only | Separated private planted-story truth | SPEC-06, SPEC-16 |

Generation truth/control datasets must not enter silver, dimensional warehouse or dashboard datasets unless the field is an ordinary, independently observable commercial fact. Explicit allowed columns should be whitelisted at stage boundaries.

## 11. Data integrity and test catalogue

| Test ID | Required automated verification |
|---|---|
| DATA-T01 | Primary keys non-null/unique and deterministic format for all entities |
| DATA-T02 | Mandatory fields, decimal precision, positive integer quantities, controlled enums |
| DATA-T03 | Exactly the frozen launch catalogue and valid region–city–country relationships in §§4.1–4.3 |
| DATA-T04 | Customer SCD versions non-overlapping, exactly one current, temporal coverage and correct historic geo/segment/size |
| DATA-T05 | Customer ownership intervals non-overlapping and valid only within acquisition/employment dates |
| DATA-T06 | Supplier sourcing relations have exactly one time-valid primary and optional distinct alternatives; no overlaps for product/supplier |
| DATA-T07 | Stable products/category taxonomies; product launch/discontinuation eligibility verified |
| DATA-T08 | Orders have valid placement/status dates; fulfilled/cancelled/placed null constraints and no pre-acquisition sales |
| DATA-T09 | Nullable independent salesperson credit; credit/owner/ship-to/home market do not collapse into single fields |
| DATA-T10 | Order lines belong to valid orders/products, unique (order_id,line_number), whole positive quantities |
| DATA-T11 | Historical unit prices/discounts/cost snapshots cannot be rewritten by current catalogue changes; monetary types decimal |
| DATA-T12 | Multiple partial return events supported; only accepted quantities count to cumulative cap; returns after fulfilment |
| DATA-T13 | Canonical salesperson and unallocated-country monthly allocation targets unique and roll up to company/country without duplication |
| DATA-T14 | Date dimension covers configured extension, ISO week-year correct and cutoff is respected in as-of queries |
| DATA-T15 | Fact joins via SCD, ownership and supplier bridges do not unintentionally multiply financial rows |
| DATA-T16 | No hidden story states or generator labels in public source, silver, warehouse or analytics |
| DATA-T17 | Cross-border shipping and uncredited orders remain supported and reportable |
| DATA-T18 | Source schema versions, manifests, row counts and content hashes match the declared outputs |

Automated fixtures must include mid-month segment migration, end-boundary assignment, same-day fulfilment, post-cutoff return, unallocated-country targets and duplicated alternative suppliers. SPEC-16 governs test execution, reliability thresholds and full acceptance gates.

## 12. Consolidated owner-approved modelling decisions

The following choices were individually approved in conversation for inclusion in SPEC-03; **their implementation details and this revised document have NOT yet received final approval**.

| ID | Approved principle | Contract location |
|---|---|---|
| DEC-DATA-13 | Effective-dated primary customer ownership; independent order credit | §§5.3, 6.1 |
| DEC-DATA-14 | Credited salesperson may be NULL; attribution policy SPEC-04 | §6.1 |
| DEC-DATA-15 | Customer home, shipping and sales territory independent | §§5, 6, 9 |
| DEC-DATA-16 | Historical order-line prices, discounts and costs preserved | §6.2 |
| DEC-DATA-17 | Multiple partial returns, cumulative *accepted* unit cap | §6.3 |
| DEC-DATA-18 | Canonical monthly salesperson allocations with explicit unallocated country buckets | §6.4 |
| DEC-DATA-19 | Exactly one time-effective primary product supplier; optional alternatives; no procurement system | §5.5 |
| DEC-DATA-20 | Administrative customer account status separate from analytical lifecycle | §5.1 |
| DEC-DATA-21 | Customer SCD Type 2 for segment and company size | §5.1a |
| DEC-DATA-22 | Stable product category/subcategory over synthetic period | §5.4 |
| DEC-DATA-23 | DATE events; half-open effective intervals; month-start targets | §§3, 8 |
| DEC-DATA-24 | Order statuses `placed`, `fulfilled`, `cancelled` | §6.1 |
| DEC-DATA-25 | Separate `order_date`, `fulfilled_date`, `cancelled_date` | §6.1 |
| DEC-DATA-26 | Historical customer country/region/city in customer SCD; fixed ship-to on order | §5.1a |
| DEC-DATA-27 | Fixed-precision decimals, integer units and DATE; detailed rounding SPEC-04 | §§3, 6 |
| DEC-DATA-28 | Deterministic readable operational IDs and distinct SCD surrogate keys | §§3, 5.1a |
| DEC-DATA-29 | Configurable date extension; historical financial reporting ends 2026-09-30 | §8 |
| DEC-DATA-30 | Source-data contracts distinct from DuckDB dimensional contracts with explicit lineage | §§2, 9 |

**Original Draft 0.1 proposals DEC-DATA-01 through DEC-DATA-12:** These are superseded by the consolidated contract where overlapping; non-conflicting elements remain as proposed source-field choices until this full revised spec is approved. They are not separately certified as 12 individual approvals.

### 12.1 Closed implementation-readiness dependencies

| Dependency | Resolution |
|---|---|
| GEO-01 | Frozen, source-evidenced region/city catalogue in §§4.2–4.3; reporting versus administrative nature explicit |
| DATA-ENUM-01 | Closed enumeration catalogue in §4.4 |
| DATA-OWN-01 | Ownership permits gaps; half-open employment; fulfilment-date sales credit; target-month rule in §6.4 |
| DATA-SUP-01 | Effective-dated primary/alternative contract in §5.5; individual sale source remains explicitly unknown |
| DATA-FIN-01 | Historical inputs and posted sale/return/VAT/freight fields aligned to SPEC-04 §§2–6 |
| DATA-TARGET-01 | Two metric codes, leaf-only aggregation, eligibility and actual attribution fixed in §6.4 and SPEC-04 §7 |
| DATA-DATE-01 | Pre-window master dates permitted; operational extension ends 2026-12-31; default finance cutoff remains 2026-09-30 |
| DATA-CUST-01 | Stable industry; SCD2 segment/size/home geography; administrative enums in §4.4 |

These closures are specification decisions under the project owner's advance approval of recommended designs. They are not implementation evidence or permission to code.

### 12.2 Geographic evidence record

Evidence was checked on 2026-10-11. The contract deliberately freezes a subset of localities; it does not claim to enumerate every national region or city.

| Evidence | Contract use | Source |
|---|---|---|
| Lithuania has ten administrative counties and uses counties at NUTS 3 | Lithuanian city parents | [Statistics Lithuania — Administrative territorial division](https://osp.stat.gov.lt/lietuvos-regionai-2023/lietuvos-suskirstymas) |
| Latvia has five statistical regions from 2024-01-01, coincident with planning regions | Latvian reporting parents and scheme caveat | [Central Statistical Bureau of Latvia — Statistical regions](https://stat.gov.lv/en/statistics-themes/environment/nature-resources/publications-and-infographics/21408-statistical) |
| The official 2024 crosswalk places Liepaja/Ventspils in Kurzeme, Jelgava in Zemgale and Daugavpils in Latgale | Latvian city-to-reporting-region rows | [Central Statistical Bureau of Latvia — 2024 region crosswalk](https://stat.gov.lv/sites/default/files/infographic/2024-01/Regions.pdf) |
| NUTS is hierarchical and LAU correspondence is the locality mapping authority | Cross-country terminology and change control | [Eurostat GISCO — Territorial units for statistics](https://ec.europa.eu/eurostat/web/gisco/geodata/statistical-units/territorial-units-statistics) |
| Tallinn/Harju, Narva/Ida-Viru, Tartu/Tartu and Parnu/Parnu are published administrative groupings | Estonian city parents | [Statistics Estonia — administrative-unit table](https://www.stat.ee/en/find-statistics/statistics-theme/economy/foreign-trade/imports-services) |

No unverified external fact is required to implement the frozen mapping. If the catalogue expands, the new relationship remains blocked until equivalent evidence is recorded.

## 13. Edge cases and failure handling

- Customer without an account owner: valid only when ownership rules permit an unassigned interval.
- Web order with no credited salesperson: valid if credit policy permits; must not inherit owner by default.
- Cross-border customer shipment: valid; role-specific geography retained.
- Transferred customer: historic orders retain correct past owner without rewriting old activity.
- Account owner fired or hired during period: effective-dated rows must not exceed eligibility windows.
- Product launch mid-history: no preceding sales; discontinued product handling requires policy.
- Order with no lines: forbidden when fulfilled; placed/cancelled orders may retain contracted lines, but any retained finance postings are zero.
- Order with multiple lines for the same SKU: allowed if unique `line_number`, unless business policy later forbids.
- Multiple partial returns: permitted, with cumulative **accepted** returned quantity capped at eligible fulfilled quantity.
- Return spanning reporting periods: preserve original order/fulfilment dates and return request/decision dates; recognition follows SPEC-04.
- Target allocations use salesperson or explicit unallocated-country buckets; company/country rollups are derived once, not duplicated.
- Unexpected text/reference enum: validation error, not silent fallback.
- Negative unit prices/costs are forbidden. Zero list/net price is allowed only as a configured full-discount fixture; zero cost is allowed only with an explicit generator rule and is never silently imputed.
- Real-world-looking identity: fictional generation only; do not copy real customer/employee lists.

## 14. Measurable acceptance criteria

The remediation audit verified the following documentation criteria; project-owner approval and implementation verification remain separate:

- [x] Every entity has explicit grain, primary key and mandatory/optional fields.
- [x] Every foreign key has a named referenced entity, permitted nullability and integrity rule.
- [x] The five segments, eleven industries, twelve product categories and five channels are preserved exactly.
- [x] Geography roles, ownership, credit and order-handling channel cannot be conflated.
- [x] Temporal joins, partial returns and target hierarchies have defined grains and checks.
- [x] Logical warehouse mapping remains distinct from physical DDL.
- [x] Private generator labels are barred from reporting tables.
- [x] Implementation-blocking contract details are closed or explicitly evidence-gated.
- [x] SPEC-04, SPEC-05, SPEC-07, SPEC-09 and SPEC-16 have identified interfaces.
- [x] Approved decisions DEC-DATA-13 through DEC-DATA-30 remain incorporated.
- [x] Markdown tables/fences and cross-document identifiers pass the static audit.
- [x] The register records the advance-approved recommendation status; the auditor did not create or alter an approval record.

**Important:** Documentation reconciliation is complete, but operational implementation remains blocked until project-owner review and the applicable SPEC-16 implementation gates pass.

## 15. Dependencies, assumptions and change control

**Upstream:** SPEC-01 (approved scope); SPEC-02 (approved business semantics, including DEC-BIZ-01 through DEC-BIZ-06). The old repository's truncated dictionary is **not** an executable authority.

**Downstream:** SPEC-04 (finance/KPIs); SPEC-05 (synthetic generation and reference-population weights); SPEC-06 (private story truth); SPEC-07 (quality); SPEC-08 (pipeline); SPEC-09 (dimensional implementation); SPEC-10–SPEC-15 (analytical uses); SPEC-16 (tests, reproducibility and release).

Changes to entity grain, key or amount semantics after approval require a documented impact analysis, changes to dependent specs and renewed tests. No downstream code or document can redefine a business metric by convenience.

## 16. Approval and change history

- **Draft 0.1 — 2026-10-11:** First proposed data model based on approved SPEC-02.
- **Draft 0.2 — 2026-10-11:** Incorporated 18 individually approved design principles. Replaced conflicting source contracts and expanded integrity checks. Pending document-wide approval and open-decision closure; no implementation permission.
- **Approval of revised document:** Approved in principle by project owner on 2026-10-11 under advance approval of recommended design decisions; documentation contract gate passed.
- **Cross-specification consistency audit:** Documentation PASS on 2026-10-11; implementation tests not executed.
- **Implementation authorised:** No.
