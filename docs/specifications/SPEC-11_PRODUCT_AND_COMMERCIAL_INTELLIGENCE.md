# SPEC-11 — Product and Commercial Intelligence

**Status:** Design-approved recommendation; documentation consistency gate passed  
**Version:** 1.0 · 2026-10-11  
**Scope:** Fictional Baltic Trade Supplies UAB; synthetic data only

## Purpose
Diagnose product economics, mix, discounts, pricing, customer baskets, sales execution and geographic drivers using pre-agreed finance grain and window conventions.

## Required analytical modules
- **Product profitability:** by SKU, category, country and period; report net sales, net COGS, GP, gross margin and return-adjusted contribution, revenue/GP concentration, confidence where sparse. Posted-return period vs original-sale cohort must be stated.
- **ABC:** rank products by trailing-12-month net revenue or gross profit (declare choice; default GP), classify cumulative 0–80% A, next 15% B, final 5% C; handle negative GP, zero totals and ties deterministically. Default exclude nonpositive metrics from share denominator but report them as separate `nonpositive` class.
- **XYZ:** coefficient of variation of **monthly unit demand** over 12 complete months; X ≤0.5, Y ≤1.0, Z >1.0, and `insufficient_history` if under 6 eligible complete months. Preserve zero-demand months after launch; avoid CV for near-zero mean. Combine ABC-XYZ with meaningful managerial actions, not arbitrary labels.
- **Basket affinity:** baskets = single fulfilled order; item group at subcategory/category; support, confidence, lift = P(A,B)/(P(A)P(B)); require minimum independent baskets (default 30 joint baskets and ≥1% support) and avoid duplicate SKU lines counted twice. Compare against popularity baseline and segment/geography confounding; association ≠ causation.
- **Discount effectiveness:** compare realised discount and product contribution by market, mix, segment, tenure and period; report descriptive conditional associations; no blanket claim discounts caused growth. Use regression/robust controls (SPEC-12) for driver investigation.
- **PVM bridge:** use fulfilled ex-VAT sales before later return postings for comparable SKUs. For previous/current units `q0,q1` and weighted realised prices `p0,p1`, `volume = (q1−q0)×p0`, `price = q1×(p1−p0)`; their sum is matched fulfilled-sales change. Split volume into `pure_volume = (Q1−Q0)×Σ(s0×p0)` and `mix = Σ((q1−Q1s0)×p0)`, `s0=q0/Q0`. Introductions, exits, accepted-refund timing and cent-rounding residual are explicit separate bridge rows; they are never assigned zero price.
- **Revenue bridge:** prior net revenue + matched PVM + new/discontinued SKUs + returns-timing effect + any disclosed adjustment = current net revenue, cent-level tolerance after posted rounding residual. Show unmatched effect explicitly.
- **Cost bridge:** split unit-cost effect `Σ q1(c1−c0)` and changes in volume/mix; quantify gross-profit erosion and account for discount effects without claiming causal isolation.
- **Salesperson balanced scorecard:** credited net revenue, GP, GP%, new buyers, retention/returns, discount rates and target attainment; uncredited business separate. Never use current account owner as historic credit proxy.
- **Geographic analysis:** home market, destination and sales territory perspectives are independently labelled; only one perspective at a time for totals.

## Data outputs
`mart_product_abc_xyz`, `mart_basket_rules`, `mart_discount_diagnostics`, `mart_revenue_bridge`, `mart_pvm`, `mart_cost_bridge`, `mart_salesperson_scorecard`, `mart_geo_performance` each versioned and as-of dated.

## Tests and acceptance
- PVM identities reconcile exactly on synthetic numeric fixtures for matched SKUs, with newcomers/leavers, returns and zero-baseline disclosed; never silently classify all volume change as pure volume.
- Basket rules tested on simple 2×2 synthetic baskets; time windows aligned; co-occurrence denominator is orders not line rows.
- SKU margins and revenue roll up to company finance exactly with same date/cutoff; report ineligible/insufficient-history group counts.

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
