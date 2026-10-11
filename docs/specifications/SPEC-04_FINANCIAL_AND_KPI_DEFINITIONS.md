# SPEC-04 — Financial and KPI Definitions

**Status:** Remediated contract 1.1 — cross-specification audit complete  
**Version:** 1.1 · 2026-10-11  
**Scope:** Fictional Baltic Trade Supplies UAB; synthetic management analytics only  
**Authority boundary:** SPEC-03 owns fields and grains; this specification owns recognition, arithmetic, denominators and posting dates.

## 1. Purpose and non-accounting boundary

Establish an auditable, VAT-aware operational management P&L and exact commercial KPI definitions. This is not statutory accounting, a VAT filing model, legal advice or a claim about a real client. Every report must state currency, cutoff, posting basis, geography role and whether a value is observed or estimated.

## 2. Decimal, rounding and sign contract

- Currency is EUR. Source unit money uses `DECIMAL(18,4)`, posted amounts use `DECIMAL(18,2)`, rates use `DECIMAL(9,6)`, and calculation intermediates use at least `DECIMAL(38,8)`. Binary floating point is prohibited for financial posting.
- Apply round-half-even to each posted line or return-event amount. Aggregate already-posted cents; do not round an aggregate reconstructed from unit fields.
- Source quantities are positive integers. Sale posting fields are nonnegative. Return posting fields are stored as nonnegative magnitudes in `fact_return_event`; only the canonical posting view applies negative revenue/COGS/VAT signs.
- A difference between a four-decimal descriptive unit snapshot and posted cents is acceptable only when explained by quantisation and no greater than €0.01 for that posting. Posted fields remain authoritative.

## 3. Fulfilment sale posting

For fulfilled line `i`, with quantity `q`, historical ex-VAT list price `l`, discount fraction `d`, unit cost `c`, and original VAT rate `v`:

```text
unrounded_list_i       = q × l
posted_list_i          = round_half_even(unrounded_list_i, 2)
posted_discount_i      = round_half_even(unrounded_list_i × d, 2)
posted_net_sales_i     = posted_list_i − posted_discount_i
posted_cogs_i          = round_half_even(q × c, 2)
posted_gross_profit_i  = posted_net_sales_i − posted_cogs_i
posted_taxable_base_i  = posted_net_sales_i
posted_vat_i           = round_half_even(posted_taxable_base_i × v, 2)
posted_invoice_total_i = posted_taxable_base_i + posted_vat_i
```

`list_unit_price_eur` is ex-VAT before discount; `discount_rate` is the sole launch discount input in `[0,1]`; `realised_unit_price_eur` is the ex-VAT after-discount unit snapshot; `unit_cost_eur` is the historical direct product acquisition cost. Current product catalogue values can never rewrite these fields. Gross profit means product contribution before freight and operating expenses, not EBITDA.

Placed and cancelled orders may preserve contracted line snapshots, but every `posted_*` sale field is zero. Fulfilled sale and direct cost are recognised on `fulfilled_date`. No partial fulfilment exists. Order intake uses `order_date` and must never be labelled revenue.

## 4. Order state, cancellation and salesperson attribution

| Status | Required dates | Financial effect |
|---|---|---|
| `placed` | order date only | Order-intake count/value only; zero recognised revenue, COGS and VAT |
| `fulfilled` | fulfilment date, no cancellation date | Sale posting on fulfilment date |
| `cancelled` | cancellation date, no fulfilment date | Cancellation count; zero recognised revenue, COGS and VAT |

Dates can be same-day but transition date cannot precede order date. Historical as-of reporting includes a fulfilment only when `fulfilled_date <= cutoff`; a later final status cannot create an earlier posting.

Credited salesperson is nullable. For fulfilled orders, any credited salesperson must be employed on `fulfilled_date`. Account owner, order channel and sales credit are distinct; no missing credit is imputed from ownership. `credited actual + uncredited actual = total actual` at every supported aggregation.

## 5. Return lifecycle, accepted quantity and recognition

| Return status | Quantity contract | Date contract | Financial effect |
|---|---|---|---|
| `requested` | `requested_quantity > 0`; accepted quantity NULL | request date set; decision date NULL | None |
| `rejected` | `requested_quantity > 0`; accepted quantity NULL | decision date on/after request and fulfilment | None |
| `accepted` | `0 < accepted_quantity <= requested_quantity` | decision date on/after request and fulfilment | Refund/recovery/VAT reversal on decision date |

Across all accepted events for one order line, cumulative accepted quantity cannot exceed fulfilled sold quantity. Requested and rejected quantities do not consume this cap. An accepted event cannot later mutate to another state; a correction is a separately audited source correction.

For accepted return `r`:

```text
posted_refund_r = round_half_even(accepted_quantity × refund_unit_ex_vat_eur, 2)
refund_cap_r     = round_half_even(accepted_quantity × original_realised_unit_price_eur, 2)
posted_refund_r <= refund_cap_r

posted_recovered_cogs_r =
  round_half_even(accepted_quantity × original_unit_cost_eur, 2) when restockable
  0.00 when written_off

posted_vat_reversal_r = round_half_even(posted_refund_r × original_vat_rate_fraction, 2)
```

Goodwill credits above original net price, exchanges and inventory valuation are out of scope. A return can post in a later month than its sale. Events after the report cutoff are not projected backward. At a cutoff:

```text
fulfilled_sales = Σ posted_net_sales on eligible fulfilment dates
accepted_refunds = Σ posted_refund on eligible accepted decision dates
net_revenue = fulfilled_sales − accepted_refunds

fulfilled_cogs = Σ posted_cogs on eligible fulfilment dates
recovered_cogs = Σ posted_recovered_cogs on eligible accepted decision dates
net_cogs = fulfilled_cogs − recovered_cogs
net_gross_profit = net_revenue − net_cogs
```

## 6. Illustrative VAT and freight

VAT is separate from management revenue and profit. The launch reference is explicitly synthetic:

| `vat_treatment_code` | `vat_rate_fraction` | Applicability |
|---|---:|---|
| `sim_standard` | 0.200000 | Illustrative standard treatment in any LT/LV/EE simulation jurisdiction |
| `sim_zero` | 0.000000 | Illustrative zero treatment only when explicitly stored on the line |

No engine may infer VAT treatment from home country, shipping country or cross-border status. A return reverses VAT at the original stored line rate. These rates are simulation conventions, not assertions of historic or current Baltic tax law.

Outbound freight is a nonnegative order cost recognised on fulfilment date and allocated to lines pro rata by posted net sales; the final line receives the cent remainder. If order net sales are zero, allocate by quantity, then final-line remainder. Return freight is recognised on the accepted return's decision date and is not recovered COGS. `contribution_after_freight = net_gross_profit − outbound_freight − returns_freight`.

## 7. Canonical target and actual contract

The only target fact grain is month × metric × leaf allocation (`salesperson` or `unallocated_country`). Launch metrics are `net_revenue_eur` and `gross_profit_eur`; values are nonnegative. There are no stored company or country-total facts.

- Country target = all leaf rows for that frozen country, including its unallocated bucket.
- Company target = all leaf rows once = sum of country targets.
- Salesperson actual = recognised postings carrying that credited salesperson.
- Country/company actual = all recognised postings, including uncredited activity, assigned to customer-home country as of the posting date. Returns use the original sale's customer and the customer SCD version at return posting date; shipping and territory views are separate analyses.
- Unallocated target has no salesperson attainment. Show `country actual − sum salesperson actuals` as uncredited actual; do not compare that amount exclusively with the unallocated target or imply causal ownership.
- An employed salesperson may receive one unprorated monthly target per metric if eligible for at least one day. Country snapshot follows the first eligible day in the month. Target attainment is NULL when target is zero.

## 8. Metric catalogue

| ID | Metric | Exact numerator / formula | Denominator and boundary |
|---|---|---|---|
| FIN-K01 | Fulfilled sales | Sum posted net sales | Fulfilment date, ex-VAT |
| FIN-K02 | Accepted refunds | Sum posted refunds | Accepted decision date, ex-VAT |
| FIN-K03 | Net revenue | FIN-K01 − FIN-K02 | Posting dates through cutoff |
| FIN-K04 | Net COGS | Fulfilled COGS − recovered COGS | Posting dates through cutoff |
| FIN-K05 | Net gross profit | FIN-K03 − FIN-K04 | Before freight |
| FIN-K06 | Gross margin % | FIN-K05 / FIN-K03 | NULL when net revenue <= 0 |
| FIN-K07 | Discount % | Sum posted discount | Sum posted list value of fulfilled lines; NULL when denominator = 0 |
| FIN-K08 | Return unit rate | Accepted units | Mature fulfilled units in the declared original-sale cohort; event-period alternative must be labelled and exposes timing mismatch |
| FIN-K09 | Average order value | Fulfilled sales | Distinct fulfilled orders; before later-period returns; NULL when no orders |
| FIN-K10 | Active buyers | Distinct customers with at least one fulfilled order in trailing 12 months | Explicit as-of date |
| FIN-K11 | Target attainment % | Actual metric | Same-grain target; NULL when target = 0 |
| FIN-K12 | Revenue YoY % | Current comparable net revenue / prior comparable net revenue − 1 | Same complete calendar window; NULL when prior <= 0 |
| FIN-K13 | Revenue concentration | Top-N positive customer net revenue | Total positive customer net revenue in declared scope; NULL when denominator <= 0 |
| FIN-K14 | Product net revenue | Fulfilled product sales − accepted refunds mapped to original line product | Posting-date basis through cutoff |
| FIN-K15 | Contribution after freight | FIN-K05 − outbound freight − return freight | Posting-date basis through cutoff |

## 9. Cutoff and comparison rules

The reporting window is 2022-01-01 through 2026-09-30 inclusive. The default as-of cutoff is 2026-09-30. Extended calendar dates through 2026-12-31 support legitimate late operational events but never enter default historical results. A complete month ends on or before cutoff. YoY, retention, XYZ and forecasts use comparable complete windows; partial-period comparisons require an explicit like-for-like flag.

Customer SCD joins use the business date appropriate to the question: order date for order-intake attributes, fulfilment date for sale posting, and return decision date for return posting. Shipping geography stays fixed on the order. Account ownership is joined independently and never changes credited salesperson.

## 10. Reconciliation and verification requirements

1. Posted sale formulas reconcile exactly to cents for each fulfilled line; nonfulfilled postings are zero.
2. Invoice total equals taxable base plus VAT; VAT is excluded from revenue, COGS and GP.
3. Fulfilled sales minus accepted refunds equals net revenue at every cutoff; fulfilled COGS minus recovered COGS equals net COGS.
4. Requested/rejected returns and post-cutoff events have zero effect; accepted-quantity caps hold cumulatively.
5. Attributed plus uncredited actual equals total actual; freight allocation sums to the order amount.
6. All target leaf rows sum once to country/company; no hierarchy duplication.
7. Negative period net revenue or GP is preserved; zero/negative denominators yield NULL.

Numeric fixtures and expected results are specified in `docs/audits/CONTRACT_TEST_MATRIX.md`; planned automation belongs under `tests/contracts/` and is not reported as executed until implementation exists.

## 11. Governance and acceptance

All fields named here exist in SPEC-03 and all warehouse/analytical specs must reuse these formulas without synonyms. A change to field name, grain, inclusion rule, rounding, sign, cutoff or denominator requires a decision-register entry, impact review and fixture update.

This document is specification-complete when cross-document static checks pass and every formula maps to a planned contract test. Implementation and release remain governed by SPEC-16; this documentation audit does not assert that numerical tests have run.
