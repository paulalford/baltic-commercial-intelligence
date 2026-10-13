# Milestone 10 product intelligence

This publication uses the validated Warehouse and Gold releases for source batch
`8e33878d4c2719cc`. The default reporting cutoff is 2026-09-30. Monetary values
are EUR posted cents. The published DuckDB and manifest live under `data/product/`.

| Table | Grain | Basis |
| --- | --- | --- |
| `mart_product_profitability` | product | All historical sale and accepted return postings, including products with zero postings |
| `mart_product_month_country` | month, customer home country at posting, product | Gold commercial posting totals |
| `mart_product_abc_xyz` | product | Trailing 12 complete months through cutoff |
| `mart_basket_rules` | unordered subcategory pair | Distinct fulfilled orders in reporting window |
| `mart_basket_context` | home country, fulfilment segment, unordered pair | Geographic and segment context for globally eligible pairs |
| `mart_product_return_event` | return event requested by cutoff | Return state as of cutoff and original sale |
| `mart_product_return_quality` | product, status, reason, disposition | Grouped as-of return events |

Finance comes from the canonical historical posting view. Product contribution after
freight uses its already allocated outbound freight and posted return freight. The
supplier shown is the approved primary relationship at cutoff, not the supplier
proven to have sourced a sale. Product categories and subcategories are stable.

ABC ranks positive trailing-12-month net gross profit. Equal-GP groups receive the
same class based on cumulative positive GP **before** the group: below 80% is A,
below 95% is B, and the remainder is C. A boundary-crossing tie stays together,
so a class can exceed its nominal share. Zero or negative GP is `nonpositive` and
excluded from the positive denominator. `abc_rank` breaks financial ties by
`product_id` solely for display.

XYZ uses population standard deviation divided by mean fulfilled units across up
to 12 complete calendar months. No-sale months after launch are zeros. A month is
eligible only when the product was launched by its first day and was not
discontinued before its last day. Returns do not subtract from demand. Fewer than
six eligible months produce `insufficient_history`; zero mean produces `zero_mean`.
X has CV <= 0.5, Y <= 1.0, and Z > 1.0.

A basket is a fulfilled order; a subcategory appears once per order. The table
contains unordered pairs with at least 30 joint orders and at least 1% pair
support. Item and pair support divide by all qualifying orders. Confidence A to B
is joint orders divided by A orders, and the reverse is also published. Lift is
joint orders times basket count divided by A orders times B orders. Item support
is the popularity baseline. Association is descriptive; the publication does not
make a causal or cross-sell recommendation. The context table reports the same
association metrics by customer home country and segment at fulfilment. Context
rows with fewer than 30 joint orders or under 1% support are marked sparse;
differences across contexts are descriptive and are not a causal adjustment.

Return event status is projected to the cutoff: an event requested by the cutoff
but decided later appears as requested, with no accepted quantity, decision date,
refund, recovery or disposition. Repeated accepted partial returns count once per
event; accepted units are capped against the original line. `defective` and
`quality_issue` are product-quality reasons; `wrong_item` and
`damaged_in_transit` are fulfilment or transit reasons; the other codes are buyer
or expectation reasons. A mature-cohort return rate is NULL because SPEC-04 and
SPEC-11 give its denominator but no maturity duration. The event numerator and
original-sale fields are available to implement an approved rule later.

The builder validates manifest and database checksums, batch and Silver lineage,
object schemas, keys, row counts, private-field exclusion, posted finance,
classification consistency, basket formulas, return attribution and cutoff. It
builds in a temporary directory and swaps the publication only after validation.
A dry run leaves the existing publication in place.

```bash
.venv/bin/python -m baltic_sales_intelligence.pipeline --config config --stage product --dry-run
.venv/bin/python -m baltic_sales_intelligence.pipeline --config config --stage product
.venv/bin/python -m baltic_sales_intelligence.pipeline --config config --stage product --force
```

Run the publish command only after reviewing the dry-run result. Use `--force`
when replacing an existing product publication. Warehouse and Gold are read-only
inputs; the customer publication is untouched.
