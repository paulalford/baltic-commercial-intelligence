# Baltic Sales Intelligence — Specification Register

**Version:** 3.0 · 2026-10-11
**Owner direction:** Balanced consulting platform; all recommended design options approved in advance.
**Gate status:** Documentation readiness PASS. Cross-contract remediation and static documentation checks completed; implementation, runtime tests and release gates remain unexecuted. No application code is authorised by this audit alone.

## Spec inventory

| ID | Document | Owner review | Cross-contract gate |
|---|---|---|---|
| SPEC-01 | [SPEC-01_PROJECT_CHARTER_AND_SCOPE](specifications/SPEC-01_PROJECT_CHARTER_AND_SCOPE.md) | Owner-approved | Documentation verified |
| SPEC-02 | [SPEC-02_FICTIONAL_BUSINESS_MODEL](specifications/SPEC-02_FICTIONAL_BUSINESS_MODEL.md) | Owner-approved | Documentation verified |
| SPEC-03 | [SPEC-03_DATA_MODEL_AND_DICTIONARY](specifications/SPEC-03_DATA_MODEL_AND_DICTIONARY.md) | Advance-approved recommendations | Remediated; documentation verified |
| SPEC-04 | [SPEC-04_FINANCIAL_AND_KPI_DEFINITIONS](specifications/SPEC-04_FINANCIAL_AND_KPI_DEFINITIONS.md) | Advance-approved recommendations | Remediated; documentation verified |
| SPEC-05 | [SPEC-05_SYNTHETIC_DATA_GENERATION](specifications/SPEC-05_SYNTHETIC_DATA_GENERATION.md) | Advance-approved recommendations | Documentation verified |
| SPEC-06 | [SPEC-06_EMBEDDED_COMMERCIAL_SCENARIOS](specifications/SPEC-06_EMBEDDED_COMMERCIAL_SCENARIOS.md) | Advance-approved recommendations | Documentation verified |
| SPEC-07 | [SPEC-07_DATA_QUALITY_ENGINEERING](specifications/SPEC-07_DATA_QUALITY_ENGINEERING.md) | Advance-approved recommendations | Documentation verified |
| SPEC-08 | [SPEC-08_DATA_PIPELINE_AND_ARCHITECTURE](specifications/SPEC-08_DATA_PIPELINE_AND_ARCHITECTURE.md) | Advance-approved recommendations | Documentation verified |
| SPEC-09 | [SPEC-09_DUCKDB_WAREHOUSE_AND_SQL](specifications/SPEC-09_DUCKDB_WAREHOUSE_AND_SQL.md) | Advance-approved recommendations | Documentation verified |
| SPEC-10 | [SPEC-10_CUSTOMER_INTELLIGENCE](specifications/SPEC-10_CUSTOMER_INTELLIGENCE.md) | Advance-approved recommendations | Documentation verified |
| SPEC-11 | [SPEC-11_PRODUCT_AND_COMMERCIAL_INTELLIGENCE](specifications/SPEC-11_PRODUCT_AND_COMMERCIAL_INTELLIGENCE.md) | Advance-approved recommendations | Documentation verified |
| SPEC-12 | [SPEC-12_STATISTICAL_ANALYSIS](specifications/SPEC-12_STATISTICAL_ANALYSIS.md) | Advance-approved recommendations | Documentation verified |
| SPEC-13 | [SPEC-13_FORECASTING_AND_ANOMALIES](specifications/SPEC-13_FORECASTING_AND_ANOMALIES.md) | Advance-approved recommendations | Documentation verified |
| SPEC-14 | [SPEC-14_OPPORTUNITY_AND_INSIGHT_ENGINES](specifications/SPEC-14_OPPORTUNITY_AND_INSIGHT_ENGINES.md) | Advance-approved recommendations | Documentation verified |
| SPEC-15 | [SPEC-15_STREAMLIT_APPLICATION](specifications/SPEC-15_STREAMLIT_APPLICATION.md) | Advance-approved recommendations | Documentation verified |
| SPEC-16 | [SPEC-16_TESTING_REPRODUCIBILITY_AND_RELEASE](specifications/SPEC-16_TESTING_REPRODUCIBILITY_AND_RELEASE.md) | Advance-approved recommendations | Documentation verified; runtime gates pending |

## Approval policy

The project owner approved SPEC-01 and SPEC-02 explicitly and approved all recommended design choices for the remaining specifications in advance on 2026-10-11. This is design authority, **not evidence of completed implementation, test execution, or verified specification consistency**.

The documentation portion of the implementation gate is satisfied: zero known implementation-blocking specification contradictions, aligned schema/finance contracts, explicit controlled vocabularies, sourced geography and complete test definitions. Implementation remains blocked until the planned tests exist and applicable SPEC-16 runtime gates pass.

## Audit artefacts

- [Contract Test Matrix](audits/CONTRACT_TEST_MATRIX.md) — planned fixtures, exact expected values and automation locations; not executed.
- [Requirements Traceability Matrix](audits/REQUIREMENTS_TRACEABILITY_MATRIX.md) — business requirement to field/transformation/output/test mapping.
- [Final Specification Remediation Audit](audits/FINAL_SPECIFICATION_REMEDIATION_AUDIT.md) — contradictions, decisions, checks actually run and readiness assessment.
- Existing SPEC-01 and SPEC-02 approval records remain unchanged.

## Source priority

1. Current owner-approved decisions (including 18 SPEC-03 modelling decisions).
2. Approved SPEC-01 and SPEC-02.
3. SPEC-03 source contracts as amended by SPEC-04 and these specs.
4. Legacy PROJECT_SPEC for scope, embedded stories, analytics catalogue only, not executable schemas.

## Change control

Any breaking field/grain/metric change triggers impact evaluation on dependent specs and fixtures; approval log and version updated.
