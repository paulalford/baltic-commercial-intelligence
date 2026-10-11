# SPEC-02 — Fictional Business Model

**Project:** Baltic Sales Intelligence  
**Business:** Baltic Trade Supplies UAB (**fictional**)  
**Document status:** Owner-approved — cross-specification documentation audit passed  
**Revision:** 1.0  
**Date:** 2026-10-10  
**Authority:** SPEC-01 Project Charter and Scope (user approved); legacy `PROJECT_SPEC.md` (requirements reference only)  
**Implementation permission:** Not granted

> **Portfolio disclosure:** Baltic Trade Supplies UAB, its staff, customers, suppliers, transactions and commercial performance are synthetic. This document describes a deliberately modelled business, not an operating company or real client engagement.

## 1. Purpose and business rationale

Define an internally coherent, decision-useful commercial operating model for a Lithuanian-headquartered business-to-business distributor serving Lithuania, Latvia and Estonia. The model must supply plausible business context for data generation, a dimensional warehouse and senior-management analytics. In particular, it must explain *why* customers buy, how products and channels differ, how salespeople manage accounts, and what factors can affect revenue, gross profit, retention and returns.

SPEC-02 describes **commercial entities and behaviour**, not executable schemas, precise financial formulas or synthetic probability distributions. Those are owned by SPEC-03, SPEC-04 and SPEC-05 respectively.

## 2. Scope and explicit exclusions

**In scope:** fictional commercial context; markets and geographic hierarchy; customer segments and industries; portfolio structure; supplier relationships; sales organisation; channels; order-to-return business events; management targets; behavioural realism; management decisions and business constraints.

**Out of scope:** incorporation/legal assertions about an actual company; real employee or business identities; real market-share statistics; VAT rates and cross-border tax computations; accounting policy; precise prices, costs, margins or probability weights; warehouse field types; customer lifetime value formulas; embedded story triggers; data-quality defects; code; operational integrations or production ERP processes.

No document may imply that portfolio outputs are actual client results.

## 3. Fictional business at a glance

| Characteristic | Agreed baseline | Detail governed elsewhere |
|---|---|---|
| Operating name | Baltic Trade Supplies UAB (fictional) | SPEC-01 |
| Commercial model | Multi-category B2B distribution, not manufacturing | This specification |
| Headquarters | Lithuania; **city intentionally unspecified** | Only decide if later needed |
| Markets served | Lithuania (LT), Latvia (LV), Estonia (EE) | SPEC-03 geographic codes |
| Reporting currency | EUR | SPEC-04 financial semantics |
| Analysis window | 2022-01-01 to 2026-09-30 inclusive | SPEC-05 date generation |
| Scale | ~4,000 customers; ~750 products; 25–35 salespeople (30 nominal) | SPEC-01, SPEC-05 |
| Transaction scale | ~120,000 orders; 250,000–400,000 order lines; 8,000–15,000 return records | SPEC-01, SPEC-05 |
| Product families | 12 indicative categories listed in §6, within approved 10–15 target | SPEC-03, SPEC-05 |
| Primary user groups | Executive, commercial, finance, regional sales, category management and customer operations | SPEC-15 |

The business is portrayed as sourcing products from suppliers and reselling them to commercial and public-sector buyers through account-managed and direct channels. It maintains customer ownership/territory structures, catalogue economics, transaction records and monthly commercial targets. These are **simulation abstractions**; they do not imply an actual procurement, payroll, inventory or tax system.

## 4. Functional business requirements

| ID | Requirement | Evidence expected in later specifications |
|---|---|---|
| BIZ-01 | Every customer belongs to exactly one home market (LT/LV/EE) and a geographically valid city/region. | SPEC-03 fields and integrity tests |
| BIZ-02 | Customer segment, industry and company size are separate attributes, with non-identical purchasing patterns. | SPEC-03 definitions; SPEC-05 distributions |
| BIZ-03 | Every order has an identifiable customer, date, channel, status and commercial geography. | SPEC-03 order contract |
| BIZ-04 | Each order line identifies a product, quantity, selling-price basis and cost basis suitable for reconciliation. | SPEC-03, SPEC-04 |
| BIZ-05 | The product catalogue supports category/subcategory, supplier, list price, standard cost, launch and lifecycle attributes. | SPEC-03, SPEC-05 |
| BIZ-06 | Product families differ meaningfully in demand frequency, value, seasonality, discount sensitivity, returns and margin characteristics. | SPEC-05, SPEC-06 |
| BIZ-07 | Salespeople have plausible primary home market/territory, hire tenure, account ownership and performance context. | SPEC-03, SPEC-05 |
| BIZ-08 | Channel mix and order economics differ across customer types and purchases. | SPEC-05 and later analytics |
| BIZ-09 | Returns reference legitimate prior order lines and occur in a temporally valid sequence. | SPEC-03, SPEC-04, SPEC-05 |
| BIZ-10 | Monthly targets are available at company, country and salesperson level without inconsistent double counting. | SPEC-03, SPEC-04, SPEC-05 |
| BIZ-11 | The model permits substantive analysis of retention, commercial growth quality, concentration, profitability and execution. | SPEC-09–SPEC-14 |
| BIZ-12 | Analytics infer commercial patterns from observable activity; generator-only controls never reach reporting layers. | SPEC-06–SPEC-09, SPEC-16 |
| BIZ-13 | The observed behaviour is non-uniform across time, market, segment, account, product and salesperson. | SPEC-05 distribution tests |
| BIZ-14 | All generated business names and people are fictional; no source dataset of real business customers or employees is used. | SPEC-05 test/review requirements |

## 5. Geographic and territory model

### 5.1 Market hierarchy

Use the reporting hierarchy **country → region → city**. Countries are the three approved Baltic markets only. Cities must map to their valid country and region; market, customer-home and order-shipping geography must be distinguishable rather than silently conflated.

Indicative commercial centres carried forward from the legacy requirements:

| Country | City examples (not exhaustive) |
|---|---|
| Lithuania | Vilnius, Kaunas, Klaipėda, Šiauliai, Panevėžys |
| Latvia | Riga, Daugavpils, Liepāja, Jelgava, Ventspils |
| Estonia | Tallinn, Tartu, Narva, Pärnu |

**Business expectation:** larger urban and industrial centres typically account for more customer locations and sales activity than smaller centres. Exact regional definitions, geographic memberships and relative weights must be specified in SPEC-03 and SPEC-05. This document does **not** authorise fabricated city-to-region mappings.

### 5.2 Commercial responsibility

Each salesperson has a primary market and territory. Customers may have an assigned account owner. An order's credited salesperson must be governed by a documented attribution rule, especially for direct channels; **account ownership, order-handling channel and sales credit must remain distinct concepts**.

A cross-border shipment or an order handled by a different salesperson is not automatically invalid. SPEC-03 and SPEC-04 must define permitted relationships and attribution precisely. Territory boundaries must not be inferred from synthetic scenario labels.

## 6. Customers and purchasing behaviour

### 6.1 Segments

Approved indicative segments (distinct from industry):

| Segment | Commercial profile for synthetic modelling |
|---|---|
| Small Business | Smaller baskets; heterogeneous frequency and channel preference |
| Mid-Market | Recurring purchasing with a mixture of direct and managed orders |
| Enterprise | Potentially high volume, multiple categories and negotiated pricing |
| Public Sector | Budget-cycle and procurement-influenced purchasing, without claims about actual public institutions |
| Trade / Reseller | Repeat bulk orders and pronounced price/mix sensitivity |

These are **qualitative hypotheses for modelling**, not required numerical effects for every customer. Individuals within a segment must vary materially.

### 6.2 Industries

Use the legacy industry examples as the initial allowed taxonomy: Manufacturing, Construction, Logistics, Automotive, Facilities Management, Agriculture, Food Processing, Retail, Engineering, Utilities and Public Sector. A customer's industry and segment must not be confused: for example, Public Sector can be an industry label and a buyer-segment label, but the meanings are distinct.

### 6.3 Customer lifecycle

Each customer has a synthetic acquisition date, account status and commercial history. The model must support newly acquired, active, declining, lapsed and potentially reactivated purchasing patterns **without** equating a generator's hidden churn state to an observable CRM status. Observed active-customer, churn and retention definitions belong to SPEC-04 and SPEC-10.

Useful customer descriptors include preferred channel, company-size band, credit tier and account owner. They must be plausible, governed values, never proxies for real identities or unsupported credit-risk claims.

### 6.4 Customer decisions and commercial questions

The model must allow management to investigate concentration risk, segment profitability, account migration, cross-sell coverage, excessive discounting, declining frequency and customer reactivation. It must also permit false first impressions: revenue growth may coexist with weaker gross profit; high-volume accounts need not be the most profitable.

## 7. Product catalogue and supply model

### 7.1 Approved indicative categories

The following 12 category names come from the original project requirements:

1. Safety Equipment
2. Hand Tools
3. Power Tool Accessories
4. Electrical
5. Fasteners
6. Maintenance Supplies
7. Workwear
8. Cleaning & Hygiene
9. Storage & Handling
10. Workshop Equipment
11. Industrial Consumables
12. Outdoor & Site Equipment

SPEC-03 will define stable category identifiers and subcategory relationships. SPEC-05 will define the exact catalogue count, mix and demand-generating characteristics. This business model does not prescribe SKU-level prices or numeric category margin bands.

### 7.2 Product attributes and lifecycle

Products have stable synthetic identity, SKU, name, category, subcategory, supplier, standard cost, list price, launch date and lifecycle status. Supplier entities are fictional; supplier count and contractual characteristics require explicit specification if modelled. Product lifecycle and launch dates must support sensible availability: no sales before a product becomes available, except if a clearly defined test case requires otherwise.

Standard cost and list price describe catalogue references, **not necessarily historical transaction cost or realised selling price**. Temporal price/cost treatment, freight, VAT, discounts and gross-profit recognition are reserved for SPEC-04 and SPEC-05.

### 7.3 Economic and behavioural variation

Product categories should differ in purchase cadence, product mix, average quantities, seasonal exposure, return propensity, cost pressures and discount response. One family may generate revenue growth and increasing quality-related returns; another may face supplier cost inflation. The detailed mechanism and truth-side planted scenarios belong to SPEC-06, and these labels must never appear in ordinary analytical reference records.

## 8. Sales organisation and commercial channels

### 8.1 Sales organisation

Use roughly 30 synthetic representatives, distributed non-uniformly by market and territory. Representatives have stable identifiers, fictional names, hire dates, a primary territory, reporting relationship where needed, and assigned customer portfolios. Inactive or not-yet-hired employees must not receive impossible historical assignments unless formally modelled as account transfers.

Performance must support balanced assessment: revenue, gross profit/margin, retention, customer acquisition, discounts, returns and target attainment. The project must be able to represent high-revenue/low-margin and lower-revenue/high-retention salespeople without classifying them by hidden generator labels in downstream analysis.

### 8.2 Sales channels

The initial channel taxonomy is:

| Channel | Description |
|---|---|
| Account Manager | Order sourced or handled through a named account representative |
| Inside Sales | Central commercial team receiving/placing orders |
| Web | Customer-facing digital ordering channel (simulated, not integrated) |
| Email / Phone Order | Direct assisted order received remotely |
| Trade Counter | In-person purchase channel (simulated, no real shop) |

Different channels may have different baskets, repeat frequency, discounting and customer composition. Channel labels must be stable and clearly distinguish **how an order was placed** from **who earns sales credit**.

## 9. Commercial events and management planning

### 9.1 Order event

An order belongs to one customer, has an order date and channel, and contains one or more product lines. Orders carry status, attributed salesperson (according to a later rule), shipping geography and payment-terms information. Line items carry quantity, reference and realised unit prices, discounts and cost values. Their mathematical meaning and posted/voided treatment are *not* determined here.

### 9.2 Return event

A return references a previously sold order line. It has a return date, non-negative returned quantity, reason, refund basis and restocking outcome. Indicative reasons from the original requirements: Defective, Wrong Item, Damaged in Transit, Ordered in Error, Quality Issue, Not as Expected and Duplicate Order. Returns cannot exceed permitted sold/previously unreturned quantities under the financial/event model eventually approved in SPEC-04. The correct handling of partial, repeated or late returns is deferred.

### 9.3 Targets

The business plans monthly commercial targets at company, country and salesperson levels. Targets are goals, not facts of realised sales. SPEC-04 must define target measures and reconciliation/aggregation policy; SPEC-05 must generate plausible target dynamics without mechanically using realised sales to guarantee attainment.

### 9.4 Analytical reporting expectations

This event model must support at least monthly and annual comparison, market and territory analysis, customer cohorts, product profitability, price-volume-mix analysis, retention, returns, forecast evaluation and commercial opportunity detection. Reporting uses calculated facts; no management insight may be determined directly by hidden simulation settings.

## 10. Data inputs, outputs and interfaces

This specification is a **semantic contract**. It produces requirements for later artefacts, not data files itself.

| Business concept / output | Next formal owner | Required dependency |
|---|---|---|
| Geographic dimension and market codes | SPEC-03 | BIZ-01 and §5 |
| Customer entity, taxonomies, ownership history if needed | SPEC-03 | BIZ-02 and §6 |
| Product, category, supplier and lifecycle entities | SPEC-03 | BIZ-05 and §7 |
| Salesperson and channel entities | SPEC-03 | BIZ-07 and §8 |
| Orders, lines, returns, target grains and foreign keys | SPEC-03 | BIZ-03, BIZ-04, BIZ-09, BIZ-10 |
| VAT, price, discount, freight, returns and margin formulas | SPEC-04 | §7 and §9 |
| Population sizes, proportions, seasonality and behaviours | SPEC-05 | BIZ-06, BIZ-08, BIZ-13 |
| Planted commercial stories and hidden validation truth | SPEC-06 | BIZ-12 |
| Quality and lineage checks | SPEC-07, SPEC-08 | §11 and §12 |
| Warehouse, marts and decision outputs | SPEC-09 through SPEC-15 | BIZ-11 |

**High-level entity relationships:** country/region/city describe customer geography; customer and salesperson ownership feed orders; orders contain order lines linked to products; returns reference order lines; monthly targets reference declared management-grain dimensions. Exact cardinalities, optionality, slowly changing history and types must be decided in SPEC-03.

## 11. Business rules and separation of responsibilities

1. **Market integrity:** only LT, LV and EE belong to the operating footprint; city/region membership is validated against a documented reference table.
2. **Business-to-business character:** customers are organisations, not consumer household accounts; names and associated people are synthetic.
3. **Independent classification axes:** customer segment, industry, size band, credit tier and geography represent separate concepts.
4. **Commercial variance:** orders and products must not all have the same size, cadence, profitability or return propensity.
5. **Financial authority:** currency is EUR, but accounting inclusion rules and every percentage denominator are owned by SPEC-04.
6. **Temporal plausibility:** acquisition, hiring and product-availability dates precede relevant operational events; returns follow legitimate purchases.
7. **Multiple geographies:** home market, territory and shipping destination are different attributes even where they commonly agree.
8. **Non-equivalence of ownership and attribution:** an assigned account owner does not automatically imply they handled or earned credit for every order.
9. **Target independence:** targets are planning records and must never be silently summed across overlapping organisational hierarchy grains.
10. **Truth separation:** synthetic scenario labels or private driver controls are not allowable features in warehouse, dashboards or management evidence.
11. **Synthetic integrity:** no real suppliers, customers, employees or identifiable client lists are copied into the generator.
12. **Change control:** alterations to these business concepts must be reconciled with the SPEC-01 charter and recorded as documented decisions.

## 12. Assumptions and proposed design decisions

### 12.1 Established requirements (not new decisions)

- Fictional Lithuanian-headquartered B2B distributor serving Lithuania, Latvia and Estonia.
- EUR reporting, approved historical window and approximate population/transaction scale.
- Customer segments, industries, 12 indicative product categories and five channels listed above.
- Account-managed plus direct sales, returns, targets and varied commercial behaviour.
- Strict separation of planted-story truth from downstream analytics.

### 12.2 Proposed clarifications for approval with this document

| ID | Proposal | Rationale |
|---|---|---|
| DEC-BIZ-01 | Describe the operating model as procurement and resale, without modelling manufacturing or inventory valuation. | Keeps the commercial case study focused. |
| DEC-BIZ-02 | Treat **home customer geography**, **shipping geography** and **sales territory** as separate business concepts. | Avoids erroneous market allocations. |
| DEC-BIZ-03 | Treat **order channel**, **account owner** and **salesperson credited** as distinct concepts. | Supports balanced sales effectiveness analysis. |
| DEC-BIZ-04 | Keep headquarters city and exact physical office/warehouse footprint unspecified unless later required. | Avoids invented logistics claims. |
| DEC-BIZ-05 | Use the 12 original product categories as the initial catalogue taxonomy, subject to explicit SPEC-03 keys and versioning. | Preserves original scope. |
| DEC-BIZ-06 | Keep gross-margin bands, exact segment/channel probabilities and cost/price behaviours outside this document. | Prevents premature and arbitrary numbers. |

These proposals become approved business semantics only when the project owner approves SPEC-02. They are *not* a substitute for the still-required decisions in SPEC-03–SPEC-06.

## 13. Edge cases and failure handling

- **Account transferred after purchase:** historical credit or ownership must be resolved under a declared effective-dating rule, not today's owner by default.
- **Web order without a named salesperson:** must have a defined treatment; do not fabricate attribution silently.
- **Cross-market fulfilment:** separate market-of-customer and ship-to reporting; SPEC-03 must specify permitted cases.
- **Product discontinued or introduced during history:** sales availability must respect lifecycle policy; exceptions require auditability.
- **Customer acquired or salesperson hired near period end:** do not invent pre-relationship purchases or unsupported time at risk.
- **Returns spanning periods:** transaction timing and net-sales impact are decided in SPEC-04, not guessed in a dashboard.
- **Public-sector overlap:** segment and industry can share names without becoming duplicate fields or forced one-to-one mapping.
- **All products in one category or customers in one region:** fail realism expectations; SPEC-05 must provide minimum coverage and distribution tests.
- **Insufficient observations in a segment:** analytics must disclose the limitation, not force a model estimate.
- **Invalid or unmapped reference code:** mark as a data-quality issue; no silent reassignment to a plausible category.

## 14. Test and verification requirements

These are **requirements for future automated tests**, not claims of already-running tests.

| Test ID | Required verification | Owner |
|---|---|---|
| BIZ-T01 | All generated customers map to an approved country and valid region/city. | SPEC-03, SPEC-05, SPEC-07 |
| BIZ-T02 | All five customer segments and all 12 baseline product categories are representable and appropriately populated. | SPEC-03, SPEC-05 |
| BIZ-T03 | Industry, segment, company size and channel do not collapse into duplicate or perfectly correlated classifications by construction. | SPEC-05 |
| BIZ-T04 | Every order, line, return and target obeys its declared referential/temporal contract. | SPEC-03, SPEC-05, SPEC-07 |
| BIZ-T05 | Expected non-uniformity exists by major market, product category, segment, channel and salesperson. | SPEC-05 |
| BIZ-T06 | Historical sales attribution and account ownership behave consistently with effective-dating policy. | SPEC-03, SPEC-04 |
| BIZ-T07 | Financial totals, returns, discounts and targets reconcile under approved calculation rules. | SPEC-04, SPEC-09 |
| BIZ-T08 | Hidden generator stories and private parameters are absent from silver/warehouse/analytics outputs. | SPEC-06–SPEC-09, SPEC-16 |
| BIZ-T09 | Names and records are sourced from synthetic generation, without ingesting real client or employee information. | SPEC-05, SPEC-16 |
| BIZ-T10 | Business model supports the charter's commercial decision questions with at least one declared evidence path each. | SPEC-09–SPEC-15 |

Numerical thresholds, distribution tolerances, statistical reproducibility and pass/fail automation belong to SPEC-05 and SPEC-16, not this business charter.

## 15. Measurable acceptance criteria for SPEC-02

The specification is eligible for **user approval** only when:

- [x] Its operating model, markets, time range, and scale agree with SPEC-01.
- [x] The five customer segments, example industries, 12 indicative categories and five channels are documented without conflating their meanings.
- [x] Every important commercial event (customer acquisition, purchase, line, return, target) has a clearly described business role.
- [x] Commercial geography, salesperson ownership, order credit and channel distinctions are explicit.
- [x] Financial, schema, probability and scenario decisions are owned by and resolved in downstream specifications.
- [x] Every business requirement BIZ-01 through BIZ-14 has a named downstream specification owner.
- [x] Decisions DEC-BIZ-01 through DEC-BIZ-06 are approved.
- [x] Edge cases and future verification requirements are documented.
- [x] All document sections, tables and code fences are structurally complete.
- [x] Project-owner approval is recorded in `docs/SPECIFICATION_REGISTER.md` and the unchanged approval audit record.

**Current approval status:** Owner-approved on 2026-10-11; documentation audit passed; no implementation permission.

## 16. Dependencies and open questions

**Upstream:** SPEC-01 (approved user charter); the original archived `PROJECT_SPEC.md` is a requirements source, not an automatically valid code-level contract.

**Downstream:** SPEC-03 Data Model and Dictionary; SPEC-04 Financial and KPI Definitions; SPEC-05 Synthetic Data Generation; SPEC-06 Embedded Commercial Scenarios; SPEC-07 Data Quality Engineering; SPEC-08 Data Pipeline and Architecture; SPEC-09–SPEC-15 analytics and presentation; SPEC-16 tests and release.

**No unresolved question blocks this operating model.** Items originally deferred here are now closed by their owners: geography/enums/relationships in SPEC-03; recognition, VAT, returns, cancellations, freight and targets in SPEC-04; distributions and reproducibility in SPEC-05; and private planted-scenario mechanisms in SPEC-06. Runtime validation remains a later SPEC-16 gate.

## 17. Source and change history

- **Draft 0.1 (2026-10-10):** Prepared for the clean-restart specification programme, using the original archived `PROJECT_SPEC.md` and approved SPEC-01. Preserves the legacy taxonomies without treating the physically truncated legacy data dictionary or generation specification as authority.
- **Design traceability:** BIZ requirements and DEC-BIZ proposals in this file are new organising devices; they do not purport to quote an original undocumented business contract.

## Project Owner Approval Record

- **Decision:** Approved
- **Approval date:** 2026-10-11
- **Approved decisions:** DEC-BIZ-01 through DEC-BIZ-06
- **Final cross-specification documentation audit:** Passed 2026-10-11
- **Implementation authorised:** No
