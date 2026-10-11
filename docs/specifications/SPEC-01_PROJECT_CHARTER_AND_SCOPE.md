# SPEC-01 — Project Charter and Scope

**Project:** Baltic Sales Intelligence  
**Public subtitle:** Advanced Commercial Analytics & Decision Support Platform  
**Document status:** User approved — final cross-specification documentation audit passed  
**Revision:** 0.1  
**Scope profile:** Balanced professional consulting platform

## 1. Purpose and business rationale

Build a portfolio-grade, end-to-end commercial intelligence case study for **Baltic Trade Supplies UAB**, an explicitly fictional Lithuanian-headquartered B2B distributor. Demonstrate balanced expertise across **data engineering, advanced analytics, commercial diagnosis, and executive communication**.

The finished project must show not just *what happened*, but where, why, how reliably it can be established, what might happen next, and which actions management should investigate. It must be presented as independently developed synthetic portfolio work, **never** as a real client engagement.

## 2. Intended audience and decisions

**Primary audience:** prospective employers, contracting clients, and technical reviewers evaluating analytical consulting capability.  
**Simulated decision-makers:** managing director, commercial director, finance lead, sales managers, category managers, and customer operations lead.

The platform should support decisions about revenue growth quality, margin protection, customer retention, product portfolio, sales performance, returns, forecast planning, and commercial opportunities. Recommendations are framed as evidence-backed proposals for investigation—not guaranteed financial outcomes.

## 3. Fictional operating context and baseline scale

| Dimension | Baseline requirement |
|---|---|
| Company | Baltic Trade Supplies UAB (fictional) |
| Business | B2B distribution of industrial, workplace, safety, maintenance and technical products |
| Markets | Lithuania, Latvia, Estonia |
| Monetary reporting | EUR; tax treatment to be fully specified in SPEC-04 |
| Period | 2022-01-01 through 2026-09-30, inclusive |
| Customers | Approximately 4,000 |
| Products | Approximately 750 |
| Salespeople | 25–35, nominal planning value 30 |
| Product categories | 10–15 |
| Orders | Approximately 120,000 |
| Order lines | Approximately 250,000–400,000 |
| Return records | Approximately 8,000–15,000 |

These are **scale targets**, not validated row counts or hard-coded generator parameters. Final exact generation sizes, distributions, and consistency constraints belong to SPEC-05. The dataset must run on a practical local workstation using DuckDB and Python.

## 4. Scope and required capabilities

### 4.1 Data and analytics engineering

- Configurable, deterministic synthetic business generation with explicit seeds and documented assumptions.
- Layered flow: pristine generation truth → deliberate raw defect injection → independent validation/repair/quarantine → curated silver → DuckDB warehouse → SQL marts → analytical outputs → management insights → Streamlit presentation.
- Schema contracts, lineage, quality reporting, error handling, reproducible builds, automated unit/integration/property-oriented checks where appropriate, and visible substantive SQL.
- Separation between synthetic generator-only scenario labels and production analytical inputs.

### 4.2 Commercial analysis

- Executive KPIs and comparable growth measures.
- Revenue bridge and price-volume-mix analysis.
- Gross profit, gross margin, costs, discount effectiveness, growth quality.
- Customer cohorts, RFM, retention/attrition, historical CLV, concentration, migration.
- Product profitability, ABC/XYZ, basket associations, portfolio performance.
- Salesperson, geography, channel, targets, and returns/quality analyses.
- Driver investigation, statistical uncertainty, interpretable regression, forecasting against baselines, anomaly detection, and ranked explainable commercial opportunities.
- Deterministic management insight rules with numerical evidence and recommended follow-up.

### 4.3 Delivery and communication

- An executive-ready multi-page Streamlit application with coherent filters and an explicit analytical hierarchy.
- Transparent metric definitions, caveats, methodology, and drill paths from summary to evidence.
- Public documentation explaining architecture, reproducibility, assumptions, validation results, and fictional-case-study disclosure.
- A portfolio case study illustrating defensible findings and possible decisions without invented real-world impact.

## 5. Explicit exclusions

- Real customer, employee, or commercially confidential records.
- Claims of paid client work, deployed customer outcomes, measured ROI, or real business access.
- Live ERP/CRM integrations, payments, e-commerce transactions, external data ingestion, or production multi-tenant SaaS operations.
- Using hidden generator story flags, defect registries, or scenario control variables as model features or evidence in downstream analytical outputs.
- Unsupported causal attribution, guaranteed opportunity value, or forecasting claims without out-of-sample evaluation.
- Dashboard development before validated warehouse/marts and core metrics.

## 6. System boundaries and interfaces

The project is a **local, reproducible analytical case study**, not an operational business system. Contract details remain governed by subsequent specifications:

| Interface or contract | Owning specification |
|---|---|
| Commercial entities and fields | SPEC-02, SPEC-03 |
| Financial and KPI semantics | SPEC-04 |
| Synthetic generation and seeds | SPEC-05 |
| Embedded scenarios and truth separation | SPEC-06 |
| Validation, repair, quarantine | SPEC-07 |
| Pipeline boundaries and orchestration | SPEC-08 |
| Warehouse, SQL, marts | SPEC-09 |
| Customer, product, statistical, forecast, and insight methods | SPEC-10 through SPEC-14 |
| Presentation, testing, packaging | SPEC-15, SPEC-16 |

No downstream specification may silently redefine the scope, financial basis, grains, identifiers, or source-of-truth conventions.

## 7. Quality and professional standards

1. **Traceability:** each key output maps to its data sources, metric contract, calculation, and test.
2. **Reconciliation:** financial totals reconcile across generation, curated layers, warehouse, and reporting at explicitly declared grains.
3. **Reproducibility:** identical code/configuration/seed/environment produces repeatable logical outputs; byte-for-byte claims require actual hash verification.
4. **Analytical independence:** planted scenarios are evaluated via independent truth-side acceptance tests; production analytics cannot access scenario labels.
5. **Statistical discipline:** methods specify assumptions, uncertainty, sample sizes where relevant, and limits on causal inference.
6. **Business relevance:** every substantive analysis answers a decision question, not merely produces a chart.
7. **Operational clarity:** failures report actionable diagnostics; invalid records do not disappear silently.
8. **Honest representation:** all portfolio disclosures unmistakably state the company's and dataset's fictional nature.

## 8. Dependencies and implementation sequencing

SPEC-01 establishes the **scope boundary** only. Detailed modelling must wait for the full specification set and a cross-specification consistency audit. No source-code implementation may start while the specification register records implementation permission as denied.

A later implementation plan will sequence data contracts and financial semantics first, then generation, defect injection, quality, warehouse, analytical methods, insight engine, and dashboard. Changes to approved scope require a recorded decision with impact assessment.

## 9. Assumptions and design decisions

- **Approved direction:** balanced professional consulting platform, rather than prioritising modelling or engineering exclusively.
- **Preserved source:** the archived legacy `PROJECT_SPEC.md` and agreed new specification register determine scope, not the incomplete legacy secondary specs.
- **Chosen boundary:** offline synthetic case study, with no real client claims.
- **Deferred decisions:** exact schemas, VAT treatment, returns sign conventions, discounts, distribution parameters, specific model selection and forecast horizons are **not decided by this charter**.

## 10. Edge cases, risks and failure handling

- **Contradictory documents:** block approval until recorded and resolved; no silent assumptions.
- **Generator produces plausible but inconsistent data:** block release until financial, temporal, and referential reconciliation passes.
- **Synthetic patterns leak into analytics:** block release when truth-only fields reach silver, warehouse, marts, statistical features, or dashboards.
- **Ambiguous growth or margin claim:** label as undefined or preliminary until SPEC-04 supplies the exact basis.
- **Model instability or insufficient signal:** show limitations or withhold conclusion rather than manufacture precision.
- **Performance exceeds local resources:** optimise queries, formats, or partitioning before reducing core analytical scope.

## 11. Test and review requirements

The specification audit must verify:

- All agreed baseline markets, time boundaries, scale targets, and capability groups appear in this charter.
- The charter has no claims of a real customer or historical real-world outcomes.
- Every required analytical family maps to a downstream specification owner.
- All unresolved metric/formula/schema decisions are clearly deferred, not presented as settled requirements.
- No conflict exists between the approved specification register and this charter.

Later system verification must include complete pipeline runs, quality audits, cross-layer reconciliation, statistical checks, baseline forecast comparisons, deterministic insight checks, Streamlit smoke tests, Ruff, and pytest. Precise thresholds and CI gates belong to SPEC-16.

## 12. Measurable acceptance criteria

SPEC-01 is approved when all the following are satisfied:

- [x] User approves balanced engineering/analysis/business communication positioning.
- [x] Company, fictional disclosure, Baltic markets, period, and baseline scale match agreed scope.
- [x] Engineering, commercial analysis, and executive delivery requirements are explicitly represented.
- [x] Exclusions and boundaries prevent feature expansion and misleading portfolio claims.
- [x] The 16 downstream specification owners cover every major capability.
- [x] Definitions originally deferred by this charter are resolved in their owning specifications.
- [x] No substantive implementation-blocking documentation contradictions remain after review.
- [x] Approval is recorded in `docs/SPECIFICATION_REGISTER.md`.

**Approval status:** User approved on 2026-10-10; cross-specification documentation audit passed on 2026-10-11. Implementation verification remains pending.

## 13. Open questions and unresolved issues

No scope-blocking question is currently identified. Detailed schema, finance, simulation, statistical, and deployment choices are intentionally deferred to their owning specifications; each must be resolved before the overall Specification Gate is granted.

## 14. Source and change history

- Draft 0.1 — reconstructed for the clean restart from the agreed new project register, the user's confirmed balanced positioning, and the archived legacy `PROJECT_SPEC.md`.
- This document deliberately **does not** reuse the physically truncated legacy data dictionary or generation specification as authority.
