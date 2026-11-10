# Attribution, revenue recognition and evaluation rules

## Attribution eligibility

For each closed-won opportunity with a recognized, valid Order, eligible touchpoints must:
1. belong to the same `contact_id` as the opportunity;
2. have `occurred_at` less than or equal to the order's `recognized_at` timestamp; and
3. not carry a labelled `post_conversion_touch` issue.

All historical eligible touches are used in this synthetic baseline; no arbitrary lookback window is imposed. Offline touches are included. Touches after conversion remain in the source table for auditability but are excluded from all attribution models.

## Models

- **First touch:** 100% of recognized order revenue to the earliest eligible touch's campaign.
- **Last touch:** 100% of recognized order revenue to the latest eligible touch's campaign.
- **Linear multi-touch:** split recognized order revenue in proportion to the number of eligible touch events per campaign. Allocation is performed in integer cents, with deterministic remainder assignment, so the sum reconciles exactly to the source order amount.

Models are expected to have equal portfolio revenue totals when applied to the same eligible orders. Campaign-level allocations should differ where journeys contain multiple campaigns.

## Revenue recognition

`Order` is the revenue source of truth. Only rows without an `issue_type` and with a unique `external_invoice_id` are recognized. Duplicate invoice rows are quarantined and excluded. Valid orders must belong to closed-won opportunities, match opportunity amount and currency, and have a recognition timestamp consistent with the close date.

## Cost and ROI

Only cost rows without an `issue_type` and whose currency matches the campaign reporting currency are included. No foreign-exchange rates are fabricated. Attributed revenue in another currency is reported separately as `unconverted_attributed_revenue` and excluded from the campaign's reporting-currency ROI.

`ROI % = ((same-currency attributed revenue - eligible spend) / eligible spend) * 100`

Missing or invalid costs produce an unavailable ROI, never assumed zero spend. A campaign may have a calculated ROI that explicitly excludes unconverted revenue.

## Chronological evaluation

Opportunity `created_at` is split using `DatasetConfig.evaluation_train_cutoff`: rows earlier than the cutoff are training/development; rows on or after it are evaluation. The cutoff is configurable and is written into the evaluation summary and source manifest.

## Lifecycle history

`OpportunityEvent` is append-only history. Each opportunity has a creation event; closed opportunities additionally have a qualification event and a terminal transition to `closed_won` or `closed_lost`. The current `Opportunity.stage` is the latest state, not a replacement for the event history.
