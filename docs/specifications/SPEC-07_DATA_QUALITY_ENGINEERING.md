# SPEC-07 — Data Quality Engineering

**Status:** Design-approved recommendation; documentation consistency gate passed  
**Version:** 1.0 · 2026-10-11  
**Scope:** Fictional Baltic Trade Supplies UAB; synthetic data only

## Purpose and boundary
Simulate an imperfect upstream source while retaining immutable valid truth. Use `truth → raw(copy + deterministic corruption) → validation/repair/quarantine → silver`; never corrupt canonical truth in place. Pandera governs row and schema checks, relational SQL governs FK/aggregate invariants.

## Configurable defect catalogue
| Code | Defect | Repair policy |
|---|---|---|
| DQ01 | exact duplicate records | dedupe if full payload identical; audit |
| DQ02 | duplicate business key, different payload | quarantine conflicting group |
| DQ03 | mixed LT/LV/EE country labels | map documented aliases only |
| DQ04 | whitespace/case variations | trim/normalise controlled codes only |
| DQ05 | missing mandatory region | repair only if unique city→region crosswalk |
| DQ06 | invalid/malformed date | quarantine; no guess |
| DQ07 | negative/zero sold quantity | quarantine |
| DQ08 | impossible discount (>100%, negative) | quarantine |
| DQ09 | missing category / product cost | quarantine unless FK reference supplies unambiguous nonhistorical category; never backfill historical costs from current catalog |
| DQ10 | orphan customer/product/salesperson FK | quarantine dependent row, cascading impact logged |
| DQ11 | malformed amount | quarantine; no float-based repair |
| DQ12 | accepted quantity invalid or cumulative accepted quantity > fulfilled quantity | quarantine offending later accepted event ordered by decision date then return ID; requests/rejections do not consume cap |
| DQ13 | overlapping SCD/ownership/supplier intervals | quarantine interval conflict; preserve source evidence |
| DQ14 | duplicate target allocation | quarantine conflicting target; exact duplicates dedupe |

## Injection and audit
- Defect plan records seed, dataset, defect ID, target row ID, field, original value, corrupted value, expected action. Private injection registry is for QA only, absent from silver and reporting.
- Per-file default injection fractions 0.1%–1.5% (configure by defect and eligible population); use stable row selection with per-type seeds, avoid injecting changes to reference IDs that make all tables unusable. A deliberately corrupted raw batch may have cascading errors; log dependency impact.
- Every input row assigned source file and row number in ingestion metadata. DQ decision log includes `batch_id`, `dataset`, `source_row`, `rule_id`, `severity`, `action`, `original_hash`, `output_hash`, `reason` and optionally repair details (no private generator labels).
- Count conservation: `received = accepted_unchanged + repaired + quarantined + discarded_exact_duplicates`, with disjoint categories. Exactly identical duplicates may be identified as extra duplicate instances, not as a missing record. Parent invalidity quarantine propagation is explicit.

## Silver contract
- Silver is typed, normalised, referentially sound and financially reconcilable. Strict NULL handling; no silent substituting zero, null string, median dates or current cost for missing historical values.
- Publish atomically only after critical checks pass; quarantine has source payload, reason and provenance. Data-quality dashboard shows defect rates and effects on missing revenue without treating quarantined bad amounts as trustworthy values.
- Repair policy is deterministic and idempotent; reprocessing identical raw batch yields same silver content and quality metrics.

## Test matrix and acceptance
- For every defect type, inject positive fixture and negative clean fixture; assert detection **independent** of injection registry, correct repair/quarantine and preserved original truth checksum.
- Verify FK chain, historical validity, line financial reconciliation, target summation and return quantity caps on silver.
- Fail if high-severity violations enter silver or if row-conservation identity fails. Report DQ coverage, accuracy on planted defects, repair count, quarantine count and downstream financial difference.

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
