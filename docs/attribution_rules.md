# Attribution, revenue, ROI and exception rules

Every rule here is implemented in code; the right-hand column names the function.

## 1. Conversion and revenue

| Rule | Definition | Code |
|---|---|---|
| Conversion | A closed-won opportunity. `conversion_at = Opportunity.closed_at`. | `attribution.recognized_revenue` |
| Revenue source of truth | `Order` rows (billing). A `closed_won` stage change alone credits nothing. | `attribution.recognized_revenue` |
| Recognised revenue | Sum of the opportunity's orders **minus** orders detected as duplicate revenue (R-REV-01). | `detection.detect_duplicate_revenue` |
| Currency | Credit is kept in the order currency; `credited_revenue_usd` uses the `FxRate` of the conversion month. | `attribution.run_attribution` |

## 2. Touch eligibility (applies to every model)

| ID | Rule | Audit column |
|---|---|---|
| E1 | `touch.contact_id = opportunity.contact_id` (identity resolved through Lead → Contact) | – |
| E2 | `touch.occurred_at <= conversion_at` | `after_conversion` |
| E3 | `touch.occurred_at >= conversion_at − lookback_days` (default 365) | `outside_lookback` |
| E4 | `touch.occurred_at >` the contact's previous conversion, so touches are never credited twice | `before_previous_conversion` |
| E5 | touch is not a detected post-conversion exception (R-TCH-01) | `flagged_exception` |
| E6 | offline touches are included unless `include_offline = false` | `offline_excluded` |

Each conversion's eligibility counts are stored in `TouchEligibilityAudit`. Conversions with no eligible touch are written to `UnattributedRevenue` with a reason. They are never silently dropped.

## 3. Models

Let the eligible touches of one conversion be ordered `t1 … tn` by time (ties broken by `touchpoint_id`).

| Model | Weight of touch *i* |
|---|---|
| `first_touch` | 1 if *i* = 1, else 0 |
| `last_touch` | 1 if *i* = n, else 0 |
| `linear_multi_touch` | 1 for every touch |
| `position_based` (U-shaped) | n = 1 → 1; n = 2 → 0.5 / 0.5; n ≥ 3 → 0.4 first, 0.4 last, 0.2 / (n − 2) for each middle touch (weights are parameters) |
| `time_decay` | 0.5 ^ (days between touch and conversion / half-life), half-life 14 days by default |

Touch weights are summed per campaign. `credit_share = campaign weight / total weight`. Revenue is then split in **integer cents** with the largest-remainder method (ties broken by `campaign_id`). Credited revenue therefore always adds up exactly to recognised revenue for every opportunity and model. All models share the same portfolio total; only the split between campaigns differs.

## 4. Campaign and channel ROI

```
spend_usd        = Σ valid CostRecord.amount × FxRate(incurred month)     (currency-mismatch rows quarantined)
attributed_usd   = Σ AttributionResult.credited_revenue_usd               (for the chosen model and run)
ROAS             = attributed_usd / spend_usd
ROI %            = (attributed_usd − spend_usd) / spend_usd × 100
cost/conversion  = spend_usd / Σ credit_share
```

`roi_status`:

* `calculated`: spend is known and clean.
* `calculated_excluding_quarantined_cost`: some cost rows were quarantined (R-CST-01) and left out.
* `unavailable_missing_cost`: no valid cost (R-CST-02). ROI is **null**, never computed against zero spend. Channel roll-ups exclude these campaigns from ROI but report their revenue separately.

## 5. Exception rules

| Rule | Issue type | Detection logic | Decision applied |
|---|---|---|---|
| R-REV-01 | `duplicate_revenue` | Order repeats an earlier `external_invoice_id`, **or** pushes cumulative order revenue for its opportunity above the opportunity amount | exclude from recognised revenue |
| R-TCH-01 | `post_conversion_touch` | Touch carries a platform conversion claim (`reported_conversion_id`) but occurred after the claimed opportunity closed (or that opportunity is not won) | exclude from attribution |
| R-CST-01 | `currency_mismatch` | `CostRecord.currency ≠ Campaign.currency` | quarantine from ROI; never auto-convert |
| R-CST-02 | `campaign_cost_omitted` | Campaign delivered touchpoints but has no valid cost records | ROI unavailable |
| R-IDN-01 | `anonymous_session_without_evidence` | Lead links to a session with `identity_evidence = none`, no resolved contact, or a different contact | reject the identity link |

Detectors only read input tables. `evaluate_detection` scores them against `data/ground_truth/ground_truth.csv` (precision, recall and F1 per issue type).

## 6. Rule-derived outcomes (how the generator decides)

* Opportunity is **open** if `created_at + sales_cycle > data as-of date` (2026-10-31). Its stage follows elapsed share: below 15 % prospecting, below 60 % qualified, otherwise proposal.
* Closed opportunity **wins** with probability `σ(−1.0 + Σ_channel β_channel × min(touches_before_close, 3) + size_effect)`. The β values (Events 0.45, Partner 0.38, Paid Search 0.30, Organic 0.22, Email 0.15, Paid Social 0.10, Affiliate 0.05, Display 0.03) and size effects (smb 0, mid −0.15, enterprise −0.35) are documented here and exported only to `ground_truth/channel_contribution.csv`.
* True channel contribution of a won deal is `β × min(n, 3)` normalised per deal. This is the yardstick for `distance_to_ground_truth` (total variation distance between a model's channel revenue share and the true share).
* Lead `status`: converted if it has an opportunity; disqualified if older than 120 days; qualified if touched since creation; otherwise new.
* Contact `lifecycle_state`: customer, then opportunity, then inactive (no activity for 180 days), then lead, then prospect.

## 7. Chronological evaluation

The cutoff (`evaluation_train_cutoff`, default 2026-07-01) splits conversions and spend into a **train** period and an **evaluation** period. For each model, ROAS is computed per (channel × market) segment in both periods. The report gives:

* `roas_rank_stability_spearman`: does the train-period ranking predict the evaluation-period ranking?
* `channel_share_shift_tvd`: how much each model's channel mix moves between periods.

## 8. Lifecycle history

`OpportunityEvent` is append-only. Allowed transitions are prospecting → qualified | closed_lost, qualified → proposal | closed_lost, and proposal → closed_won | closed_lost. Terminal stages are final. The API rejects an invalid transition with 409 and an out-of-order event with 422. The live stage comes from the `opportunity_current_stage` view. `Opportunity.stage` is the generation snapshot and is never overwritten.
