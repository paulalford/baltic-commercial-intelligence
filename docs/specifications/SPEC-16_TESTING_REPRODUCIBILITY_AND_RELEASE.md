# SPEC-16 — Testing, Reproducibility and Release

**Status:** Design-approved recommendation; documentation consistency gate passed; implementation gates unexecuted  
**Version:** 1.0 · 2026-10-11  
**Scope:** Fictional Baltic Trade Supplies UAB; synthetic data only

## Purpose and release definition
Prove the project is reproducible, financially coherent, well tested, honest about simulation and credible as professional consulting portfolio evidence. A run that merely completes is insufficient.

## Test pyramid and gates
| Gate | Coverage | Blocking condition |
|---|---|---|
| G01 | Markdown specifications/decision register | broken reference, fence/table, missing mandatory sections, unresolved contract mismatch |
| G02 | Ruff, package import, typing basics | lint/parse failure |
| G03 | Generator unit/seed tests | non-deterministic records or wrong reference counts |
| G04 | Geography/SCD/FK/property tests | invalid relationship, overlap or nonunique as-of join |
| G05 | Financial property + gold fixtures | failure to reconcile to cent |
| G06 | Defect injection detection tests | silently repaired corrupt history, missing quarantine, truth mutation |
| G07 | Silver/warehouse integration | grain change, orphan keys, leakage, mismatch |
| G08 | Analytics mathematical fixtures | PVM/ABC/XYZ/RFM/cohort/basket/baseline error |
| G09 | Statistical/forecast time-split tests | future leakage, misleading uncertainty, no baseline |
| G10 | Insight rules, app smoke tests | unsupported claims, broken filters, inconsistent KPI totals |
| G11 | Full clean rebuild twice | output hash drift or undocumented environmental variance |
| G12 | Portfolio disclosure and release audit | inaccurate source claims, synthetic story leakage, committed data |

## Reproducible environment
- Python 3.12, DuckDB, pandas, pyarrow, pandera, scipy, statsmodels, scikit-learn, plotly, streamlit, pytest and Ruff. Use `pyproject.toml` as authoritative dependencies, a separately generated pinned requirements lock including transitive versions and provenance, editable install for local development.
- `pip check`, `pytest`, `ruff check .`, schema-doc lint, deterministic seed tests and full pipeline command recorded in README; pin exact version compatibility and support a fresh virtualenv rebuild, not only pre-installed dependency satisfaction. Linux Mint supported baseline; record OS/architecture/Python and DuckDB versions.
- Hash files using stable CSV/Parquet writer configuration and canonical sorted rows. Where Parquet metadata makes byte identity platform-specific, compare typed canonical row-content digests as a second reproducibility measure; disclose limitation.

## Numerical and analytical QA
- Gold fixtures for cancellation before fulfilment, fulfilment across year boundary, requested/rejected/accepted partial returns and an accepted return after cutoff, zero GP, SCD boundary change, customer movement LT city-to-city, cross-border shipment, uncredited salesperson and unallocated-country targets. Expected numbers are frozen in `docs/audits/CONTRACT_TEST_MATRIX.md`.
- Independent recomputation of revenue/COGS/GP/returns/VAT/discounts; company totals by country role reconcile without summing different geographic views; PVM bridge residual documented.
- Forecast regression tests use expanding-origin holdouts; classification performance evaluated on future windows; use warnings for sparsity and future censoring. Private scenario-validation scores separated from consumer analytics.

## Release packaging
- Public GitHub repository with meaningful milestones, readable SQL, full specifications and decisions, transparent synthetic case study, reproducible instructions, architecture illustration, selected screenshots (without overstating live business results) and real test summary.
- Keep data/truth/raw/silver/gold/database, secrets, `.venv`, local logs, private fixture truths and generated artifacts out of Git unless explicitly approved **small public demo synthetic sample** with no hidden labels.
- CI checks docs, lint, unit tests, miniature integration and short seeded generator; full model build reproducibly documented locally with manifest and timing. Release approval requires all gates green and report documenting exceptions.

## Final acceptance checklist
- Fresh clone → install → seeded generate → defects → validate → DuckDB → marts → models → Streamlit succeeds.
- Each original story validated independently and discovered using ordinary analytical evidence; no dashboard private labels.
- Technical reviewers can find field types, formulas, SQL transformations, tests, limits, calibration and modelling decisions in the repository.
- Portfolio says 'fictional simulated case study' prominently, never 'client delivered' or 'actual outcome'.

## Governance, dependencies, and change control

**Project contract:** All identities and commercial events are synthetic. Analysis covers LT/LV/EE in EUR, reporting 2022-01-01 through 2026-09-30, without implying an actual client engagement. SPEC-01 and SPEC-02 define intent; SPEC-03 governs source grain and identities; SPEC-04 governs economics; SPEC-05/06 govern generated distributions and private truth; SPEC-07–09 govern transport and warehouse; SPEC-10–15 govern analytical outputs; SPEC-16 governs verification.

Any change to a grain, formula, inclusion criterion, as-of rule, enum, field name, or security boundary requires a documented decision and regeneration of dependent fixtures. Do not silently reinterpret an approved contract inside code.

## Failure handling and edge cases

- Null denominators, zero activity, and insufficient observation windows produce explicit `NULL`/not-estimable values and explanatory flags, never disguised zeros.
- Historical calculations exclude any observation after the stated as-of cutoff, even if the synthetic generator knows the future.
- Duplicate source records, orphan relationships, impossible dates and unreconciled amounts fail validation or enter a traceable quarantine; they are not silently removed.
- Outputs must state unit, currency, grain, period, filters, and whether results represent observations, estimates, or synthetic scenario validation.

## Approval and implementation gate

**Design recommendation:** Accepted under project-owner advance approval dated 2026-10-11. The documentation consistency audit passed; G02–G12 implementation/release gates remain unexecuted. **Implementation is not authorised merely by writing this document.**
