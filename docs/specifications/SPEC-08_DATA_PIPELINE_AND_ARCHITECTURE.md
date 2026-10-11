# SPEC-08 — Data Pipeline and Architecture

**Status:** Design-approved recommendation; documentation consistency gate passed  
**Version:** 1.0 · 2026-10-11  
**Scope:** Fictional Baltic Trade Supplies UAB; synthetic data only

## Purpose and stack
Local-first, reproducible Python 3.12 pipeline using Pandas/PyArrow, DuckDB SQL, Pandera, SciPy, statsmodels, scikit-learn and Streamlit. No external production integration or cloud dependency required for basic demo.

## Pipeline DAG and persistence
```text
versioned YAML config + seed
  → immutable synthetic truth + restricted story registry
  → raw exports / deterministic defect injection
  → schema checks → repair/quarantine (DQ report)
  → validated silver Parquet
  → DuckDB staging → dimensions/facts → SQL marts
  → statistical/model outputs + insight rules
  → Streamlit read-only presentation
```
- Truth private controls never move with source data to silver. Pipeline stages have explicit source schemas, output schemas, per-batch manifests and content hashes.
- Recommended repository packages: `generation`, `quality`, `ingestion`, `transforms`, `metrics`, `customer`, `product`, `profitability`, `statistics`, `forecasting`, `anomalies`, `opportunities`, `insights`, `utils`; `sql/staging`, `dimensions`, `facts`, `marts`, `analysis`, `tests`.
- Data directories `data/truth` (private control ignored by Git), `data/raw`, `data/quarantine`, `data/silver`, `data/database`, `data/gold`; all generated files ignored, `.gitkeep` tracked. Small intentional test fixtures are versioned under tests.

## Execution contract
- CLI `python -m baltic_sales_intelligence.pipeline --config config/ --stage <stage>` or six named scripts as thin wrappers; `run_pipeline` orchestrates strict order; `--force` and `--dry-run` behaviour documented. Stages must not change global CWD or rely on untracked input files.
- Inputs: config, version, seed and upstream manifest hashes. Outputs: typed tables plus manifest with source path(s), row count, SHA256, schema ID/version, config hash, package versions, stage duration and quality status.
- Idempotence: same config/inputs means byte-identical published datasets where specified; rebuild from clean workspace must succeed. Use temp file + atomic rename to prevent partial output; stage fails closed on critical validation. For DuckDB build use new staging database and swap only on full success.
- Logs structured with stage, dataset, batch, action, counts, failure category; no sensitive business data (all synthetic, but avoid full row dumps). Exit nonzero on failed gate.

## Interfaces and failure recovery
- Generation (SPEC-05) feeds raw (SPEC-07), silver contract (SPEC-03/04) feeds warehouse (SPEC-09); SQL marts feed analytics (SPEC-10–14), insight ranking and app (SPEC-15).
- On invalid YAML, unmatched schema, missing input, failure in reconciliation, bad seed or unavailable DuckDB: abort stage with actionable error and preserve last good outputs. No downstream modules run if upstream status failed.
- Pipeline manifests enable targeted rebuild only when inputs/config/code version change; cache is optional, never substitute old output silently.
- Application has **read-only warehouse access**, no generation during user interactions; precomputed heavy modelling results refreshed through CLI.

## Acceptance criteria
- One documented command builds all accepted layers from empty generated-data directories without network calls.
- Each stage has integration tests, input and output counts, freshness/version checks, failure rollback and deterministic re-run checks.
- A private planted-story label intentionally inserted into source is rejected by explicit schema whitelist.

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
