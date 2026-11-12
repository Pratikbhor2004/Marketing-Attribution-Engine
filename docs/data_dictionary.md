# Data dictionary

Generated from `attribution_engine/schema.py` (`python -m attribution_engine.schema`). Every exported column is listed; a test fails if a column is added without documentation.

Conventions: timestamps are ISO-8601 `YYYY-MM-DDTHH:MM:SS` (UTC-naive); money is decimal with 2 dp in the row's `currency`; USD conversions use the monthly `FxRate` of the transaction month; IDs are immutable and zero-padded.

## Channel

Marketing channel (master data).

- **Primary key:** `channel_id`
- **Relationships:** Channel 1:N Campaign; Channel 1:N Touchpoint

| Field | Type | Required | Allowed values / units | Unique | Validation |
|---|---|:-:|---|:-:|---|
| `channel_id` | string | yes | CH-001 … CH-008 | yes | PK |
| `channel_name` | string | yes | Paid Search, Paid Social, Email, Organic Search, Affiliate, Display, Events, Partner | yes | enum |
| `channel_group` | string | yes | digital | offline | partner |  | enum |
| `is_paid` | boolean | yes | true/false |  | boolean |
| `default_source_system` | string | yes | ad_platform, esp, web_analytics, affiliate_network, event_app, crm |  | enum |

## FxRate

Monthly synthetic reference rate to USD (reporting currency).

- **Primary key:** `fx_rate_id`
- **Relationships:** reference; joined by (currency, month)

| Field | Type | Required | Allowed values / units | Unique | Validation |
|---|---|:-:|---|:-:|---|
| `fx_rate_id` | string | yes | FX-<CUR>-<YYYY-MM> | yes | PK |
| `currency` | string | yes | INR | USD | EUR |  | enum |
| `rate_month` | string | yes | YYYY-MM |  | (currency, rate_month) unique |
| `rate_to_usd` | decimal | yes | USD per 1 unit, 6 dp |  | > 0; USD = 1 |
| `source` | string | yes | synthetic_reference_rate |  | provenance |

## Campaign

Campaign with budget, channel, market and flight dates.

- **Primary key:** `campaign_id`
- **Relationships:** Campaign N:1 Channel; 1:N Creative; N:M Audience (CampaignAudience); 1:N Touchpoint; 1:N CostRecord

| Field | Type | Required | Allowed values / units | Unique | Validation |
|---|---|:-:|---|:-:|---|
| `campaign_id` | string | yes | CMP-2026-000001 | yes | PK; ^CMP-2026-\d{6}$ |
| `campaign_name` | string | yes | <market> <channel> - <objective> <nnnn> |  | non-empty |
| `channel_id` | string | yes | FK Channel |  | FK |
| `market` | string | yes | IN | US | EU |  | enum |
| `currency` | string | yes | INR | USD | EUR |  | derived from market; contracted billing currency |
| `objective` | string | yes | awareness | lead_generation | conversion |  | enum |
| `budget_amount` | decimal | yes | decimal, 2 dp, in `currency` units |  | >= 0 |
| `start_at` | timestamp (ISO-8601, YYYY-MM-DDTHH:MM:SS, UTC-naive) | yes |  |  | <= end_at |
| `end_at` | timestamp (ISO-8601, YYYY-MM-DDTHH:MM:SS, UTC-naive) | yes |  |  | >= start_at (always-on campaigns end 31 Dec) |
| `is_evergreen` | boolean | yes | true = always-on |  | boolean |
| `status` | string | yes | active | completed |  | derived: completed if end_at < data as-of date |

## Creative

Ad creative / event kit belonging to one campaign.

- **Primary key:** `creative_id`
- **Relationships:** Creative N:1 Campaign; 1:N Touchpoint (optional)

| Field | Type | Required | Allowed values / units | Unique | Validation |
|---|---|:-:|---|:-:|---|
| `creative_id` | string | yes | CRV-2026-000001 | yes | PK |
| `campaign_id` | string | yes | FK Campaign |  | FK |
| `creative_name` | string | yes |  |  | non-empty |
| `format` | string | yes | image | video | text | landing_page | event_kit |  | enum |
| `version` | string | yes | A | B | C |  | enum |
| `launched_at` | timestamp (ISO-8601, YYYY-MM-DDTHH:MM:SS, UTC-naive) | yes |  |  | = campaign start_at |

## Audience

Targeting segment.

- **Primary key:** `audience_id`
- **Relationships:** Audience N:M Campaign via CampaignAudience

| Field | Type | Required | Allowed values / units | Unique | Validation |
|---|---|:-:|---|:-:|---|
| `audience_id` | string | yes | AUD-2026-000001 | yes | PK |
| `audience_name` | string | yes |  |  | non-empty |
| `segment_type` | string | yes | prospect | retargeting | lookalike | customer |  | enum |
| `effective_from` | date | yes | YYYY-MM-DD |  | <= effective_to |
| `effective_to` | date | no | null = open-ended |  | >= effective_from when set |

## CampaignAudience

Bridge table for the Campaign N:M Audience relationship, with effective dates.

- **Primary key:** `campaign_audience_id`
- **Relationships:** bridge; (campaign_id, audience_id) unique

| Field | Type | Required | Allowed values / units | Unique | Validation |
|---|---|:-:|---|:-:|---|
| `campaign_audience_id` | string | yes | CAUD-2026-000001 | yes | PK |
| `campaign_id` | string | yes | FK Campaign |  | FK |
| `audience_id` | string | yes | FK Audience |  | FK; (campaign_id, audience_id) unique |
| `effective_from` | timestamp (ISO-8601, YYYY-MM-DDTHH:MM:SS, UTC-naive) | yes |  |  | within campaign flight |
| `effective_to` | timestamp (ISO-8601, YYYY-MM-DDTHH:MM:SS, UTC-naive) | yes |  |  | >= effective_from |

## Contact

Prospect / customer person (synthetic, no PII).

- **Primary key:** `contact_id`
- **Relationships:** Contact 1:N Touchpoint, WebSession, Lead, Opportunity

| Field | Type | Required | Allowed values / units | Unique | Validation |
|---|---|:-:|---|:-:|---|
| `contact_id` | string | yes | CON-2026-000001 | yes | PK; ^CON-2026-\d{6}$ |
| `created_at` | timestamp (ISO-8601, YYYY-MM-DDTHH:MM:SS, UTC-naive) | yes |  |  | derived: <= first observed touch |
| `country` | string | yes | IN, US, DE, FR, NL |  | ISO-3166 alpha-2 |
| `market` | string | yes | IN | US | EU |  | derived from country |
| `company_size` | string | yes | smb | mid_market | enterprise |  | enum |
| `consent_status` | string | yes | granted | denied | unknown |  | enum |
| `lifecycle_state` | string | yes | prospect | lead | opportunity | customer | inactive |  | derived: customer iff won opp; opportunity iff open opp; inactive iff no activity 180d |

## WebSession

Website visit; may be anonymous.

- **Primary key:** `session_id`
- **Relationships:** WebSession N:0..1 Contact; 1:N Lead (optional)

| Field | Type | Required | Allowed values / units | Unique | Validation |
|---|---|:-:|---|:-:|---|
| `session_id` | string | yes | SES-2026-000001 (chronological) | yes | PK; ^SES-2026-\d{6}$ |
| `contact_id` | string | no | FK Contact; null = anonymous |  | null iff identity_evidence = none |
| `anonymous_id` | string | yes | ANON-<10 digits> cookie id |  | not a key |
| `started_at` | timestamp (ISO-8601, YYYY-MM-DDTHH:MM:SS, UTC-naive) | yes |  |  | >= contact.created_at when identified |
| `ended_at` | timestamp (ISO-8601, YYYY-MM-DDTHH:MM:SS, UTC-naive) | yes |  |  | >= started_at |
| `page_views` | integer | yes | >= 1 |  |  |
| `traffic_channel_id` | string | yes | FK Channel (digital) |  | FK |
| `device` | string | yes | mobile | desktop | tablet |  | enum |
| `identity_evidence` | string | yes | form_submit | login | email_click_id | cookie_match | none |  | enum |

## Lead

Marketing lead; web leads link to a session, offline leads do not.

- **Primary key:** `lead_id`
- **Relationships:** Lead N:1 Contact; N:0..1 WebSession; 1:0..1 Opportunity

| Field | Type | Required | Allowed values / units | Unique | Validation |
|---|---|:-:|---|:-:|---|
| `lead_id` | string | yes | LEAD-2026-000001 (chronological; injected anomalies appended) | yes | PK; ^LEAD-2026-\d{6}$ |
| `contact_id` | string | yes | FK Contact |  | FK; must equal session contact |
| `session_id` | string | no | FK WebSession; null for event_scan / crm_import |  | session must carry identity evidence |
| `created_at` | timestamp (ISO-8601, YYYY-MM-DDTHH:MM:SS, UTC-naive) | yes |  |  | >= session.started_at and contact.created_at |
| `source_system` | string | yes | web_form | event_scan | crm_import |  | enum |
| `identity_link_method` | string | yes | session_form_submit | event_badge_scan | crm_import | probabilistic_device_match |  | audit |
| `status` | string | yes | new | qualified | converted | disqualified |  | derived: converted iff opportunity; disqualified if >120d old; qualified if touched since |

## Opportunity

Sales opportunity (current-state snapshot; history in OpportunityEvent).

- **Primary key:** `opportunity_id`
- **Relationships:** Opportunity N:1 Lead, N:1 Contact; 1:N OpportunityEvent; 1:0..N Order

| Field | Type | Required | Allowed values / units | Unique | Validation |
|---|---|:-:|---|:-:|---|
| `opportunity_id` | string | yes | OPP-2026-000001 (chronological) | yes | PK; ^OPP-2026-\d{6}$ |
| `contact_id` | string | yes | FK Contact |  | = lead.contact_id |
| `lead_id` | string | yes | FK Lead |  | FK; unique |
| `created_at` | timestamp (ISO-8601, YYYY-MM-DDTHH:MM:SS, UTC-naive) | yes |  |  | >= lead.created_at |
| `closed_at` | timestamp (ISO-8601, YYYY-MM-DDTHH:MM:SS, UTC-naive) | no | null while open |  | null iff open stage; >= created_at |
| `stage` | string | yes | prospecting | qualified | proposal | closed_won | closed_lost |  | derived: open if planned close > as-of; won/lost from documented exposure rule |
| `amount` | decimal | yes | decimal, 2 dp, in `currency` units |  | >= 0 |
| `currency` | string | yes | INR | USD | EUR |  | contact market currency |
| `company_size` | string | yes | smb | mid_market | enterprise |  | = contact.company_size |

## OpportunityEvent

Append-only lifecycle history. Current stage = latest event (view opportunity_current_stage).

- **Primary key:** `event_id`
- **Relationships:** OpportunityEvent N:1 Opportunity

| Field | Type | Required | Allowed values / units | Unique | Validation |
|---|---|:-:|---|:-:|---|
| `event_id` | string | yes | OPPEVT-<opportunity_id>-NN | yes | PK |
| `opportunity_id` | string | yes | FK Opportunity |  | FK |
| `sequence` | integer | yes | 1..n |  | (opportunity_id, sequence) unique, chronological |
| `event_type` | string | yes | created | stage_changed | closed |  | first event = created |
| `from_stage` | string | no | null for created |  | allowed transition |
| `to_stage` | string | yes | opportunity stage enum |  | allowed transition from from_stage |
| `occurred_at` | timestamp (ISO-8601, YYYY-MM-DDTHH:MM:SS, UTC-naive) | yes |  |  | >= previous event |
| `actor` | string | yes | crm_sync (generated) | API caller |  | audit |

## Touchpoint

Contact-level marketing interaction (online or offline).

- **Primary key:** `touchpoint_id`
- **Relationships:** Touchpoint N:1 Contact, Campaign, Channel; N:0..1 Creative; N:0..1 Opportunity (platform claim)

| Field | Type | Required | Allowed values / units | Unique | Validation |
|---|---|:-:|---|:-:|---|
| `touchpoint_id` | string | yes | TOUCH-<contact_id>-000001 (per-contact chronological counter) | yes | PK; embeds contact_id |
| `contact_id` | string | yes | FK Contact |  | FK |
| `campaign_id` | string | yes | FK Campaign |  | occurred_at inside campaign flight |
| `channel_id` | string | yes | FK Channel |  | = campaign.channel_id |
| `creative_id` | string | no | FK Creative; null for offline touches |  | FK |
| `occurred_at` | timestamp (ISO-8601, YYYY-MM-DDTHH:MM:SS, UTC-naive) | yes |  |  | >= contact.created_at |
| `touch_type` | string | yes | ad_click, ad_view, video_view, email_open, email_click, organic_visit, affiliate_click, event_attendance, booth_scan, partner_referral, sales_call |  | enum; consistent with channel |
| `is_offline` | boolean | yes |  |  | derived from touch_type |
| `source_system` | string | yes | channel source system |  |  |
| `reported_conversion_id` | string | no | FK Opportunity claimed by ad platform |  | claim must precede close (R-TCH-01) |

## Order

Closed revenue (billing) — the revenue source of truth.

- **Primary key:** `order_id`
- **Relationships:** Order N:1 Opportunity

| Field | Type | Required | Allowed values / units | Unique | Validation |
|---|---|:-:|---|:-:|---|
| `order_id` | string | yes | ORD-2026-000001 | yes | PK |
| `opportunity_id` | string | yes | FK Opportunity (closed_won) |  | FK |
| `external_invoice_id` | string | yes | INV-2026-000001 |  | unique among recognised orders (R-REV-01) |
| `revenue_amount` | decimal | yes | decimal, 2 dp, in `currency` units |  | = opportunity.amount for the recognised order |
| `currency` | string | yes | INR | USD | EUR |  | = opportunity.currency |
| `recognized_at` | timestamp (ISO-8601, YYYY-MM-DDTHH:MM:SS, UTC-naive) | yes |  |  | >= opportunity.closed_at |
| `source_system` | string | yes | billing | billing_reimport | manual_invoice |  |  |

## CostRecord

Monthly campaign spend from the finance ledger.

- **Primary key:** `cost_record_id`
- **Relationships:** CostRecord N:1 Campaign

| Field | Type | Required | Allowed values / units | Unique | Validation |
|---|---|:-:|---|:-:|---|
| `cost_record_id` | string | yes | COST-2026-000001 | yes | PK |
| `campaign_id` | string | yes | FK Campaign |  | FK |
| `period_month` | string | yes | YYYY-MM |  |  |
| `incurred_at` | timestamp (ISO-8601, YYYY-MM-DDTHH:MM:SS, UTC-naive) | yes |  |  | inside campaign flight |
| `amount` | decimal | yes | decimal, 2 dp, in `currency` units |  | >= 0; derived from budget x days x utilisation |
| `currency` | string | yes | INR | USD | EUR |  | must equal campaign.currency (R-CST-01) |
| `cost_type` | string | yes | media_spend, platform_fee, content_production, commission, event_fee, partner_fee, agency_fee, manual_adjustment |  | enum |
| `source_system` | string | yes | finance_ledger | spreadsheet_upload |  |  |

## AttributionRun

One execution of the attribution engine with its parameters (derived).

- **Primary key:** `run_id`
- **Relationships:** AttributionRun 1:N AttributionResult

| Field | Type | Required | Allowed values / units | Unique | Validation |
|---|---|:-:|---|:-:|---|
| `run_id` | string | yes | RUN-BASELINE | RUN-2026-000001 | yes | PK |
| `created_at` | string | yes | timestamp or 'baseline' |  |  |
| `triggered_by` | string | yes |  |  | audit |
| `models` | string | yes | comma list of models |  |  |
| `lookback_days` | integer | yes | >= 1 |  |  |
| `time_decay_half_life_days` | decimal | yes | > 0 |  |  |
| `position_first_weight` | decimal | yes | 0..1 |  | first + last <= 1 |
| `position_last_weight` | decimal | yes | 0..1 |  |  |
| `include_offline` | boolean | yes |  |  |  |
| `result_rows` | integer | yes |  |  |  |
| `attributed_opportunities` | integer | yes |  |  |  |
| `unattributed_opportunities` | integer | yes |  |  |  |
| `attributed_revenue_usd` | decimal | yes | USD |  | first model in run |

## AttributionResult

Credit given to a campaign for one conversion under one model (derived).

- **Primary key:** `attribution_id`
- **Relationships:** AttributionResult N:1 AttributionRun, Opportunity, Campaign

| Field | Type | Required | Allowed values / units | Unique | Validation |
|---|---|:-:|---|:-:|---|
| `attribution_id` | string | yes | ATR-<opp>-<FT|LT|LIN|POS|TD>-<campaign>[-<run>] | yes | PK |
| `run_id` | string | yes | FK AttributionRun |  |  |
| `model` | string | yes | first_touch | last_touch | linear_multi_touch | position_based | time_decay |  | enum |
| `opportunity_id` | string | yes | FK Opportunity |  | FK |
| `contact_id` | string | yes | FK Contact |  | FK |
| `campaign_id` | string | yes | FK Campaign |  | FK |
| `channel_id` | string | yes | FK Channel |  | = campaign.channel_id |
| `conversion_at` | timestamp (ISO-8601, YYYY-MM-DDTHH:MM:SS, UTC-naive) | yes |  |  | = opportunity.closed_at |
| `credit_share` | decimal | yes | 0..1, 6 dp |  | sums to 1 per (run, model, opportunity) |
| `credited_revenue` | decimal | yes | decimal, 2 dp, in `currency` units |  | sums to recognised revenue per opportunity (integer cents) |
| `currency` | string | yes | INR | USD | EUR |  | = order currency |
| `credited_revenue_usd` | decimal | yes | USD at conversion-month FX |  |  |
| `eligible_touch_count` | integer | yes | >= 1 |  |  |
| `campaign_touch_count` | integer | yes | >= 1 |  |  |

## ExceptionLog

Rule-detected data-quality exceptions and the decision applied (derived).

- **Primary key:** `exception_id`
- **Relationships:** references any source record

| Field | Type | Required | Allowed values / units | Unique | Validation |
|---|---|:-:|---|:-:|---|
| `exception_id` | string | yes | EXC-2026-000001 | yes | PK |
| `issue_type` | string | yes | duplicate_revenue | post_conversion_touch | currency_mismatch | campaign_cost_omitted | anonymous_session_without_evidence |  | enum |
| `rule_id` | string | yes | R-REV-01, R-TCH-01, R-CST-01, R-CST-02, R-IDN-01 |  |  |
| `record_type` | string | yes | Order | Touchpoint | CostRecord | Campaign | Lead |  |  |
| `record_id` | string | yes | id of offending record |  |  |
| `related_id` | string | no | original order / claimed opportunity / campaign / session |  |  |
| `detail` | string | yes | human-readable evidence |  |  |
| `decision` | string | yes | exclude_from_recognized_revenue | exclude_from_attribution | quarantine_cost_from_roi | roi_unavailable_missing_cost | reject_identity_link |  |  |

## UnattributedRevenue

Recognised revenue with no eligible touch (kept visible, never dropped).

- **Primary key:** `(run_id, opportunity_id)`

| Field | Type | Required | Allowed values / units | Unique | Validation |
|---|---|:-:|---|:-:|---|
| `run_id` | string | yes |  |  |  |
| `opportunity_id` | string | yes | FK Opportunity |  |  |
| `contact_id` | string | yes |  |  |  |
| `conversion_at` | timestamp (ISO-8601, YYYY-MM-DDTHH:MM:SS, UTC-naive) | yes |  |  |  |
| `revenue` | decimal | yes | decimal, 2 dp, in `currency` units |  |  |
| `currency` | string | yes | INR | USD | EUR |  |  |
| `revenue_usd` | decimal | yes | USD |  |  |
| `reason` | string | yes | no_touchpoints_for_contact | all_touches_outside_lookback_or_ineligible | only_post_conversion_or_ineligible_touches |  |  |

## TouchEligibilityAudit

Per-conversion count of touches by eligibility outcome (derived).

- **Primary key:** `(run_id, opportunity_id)`

| Field | Type | Required | Allowed values / units | Unique | Validation |
|---|---|:-:|---|:-:|---|
| `run_id` | string | yes |  |  |  |
| `opportunity_id` | string | yes |  |  |  |
| `after_conversion` | integer | yes | E2 excluded |  |  |
| `before_previous_conversion` | integer | yes | E4 excluded |  |  |
| `eligible` | integer | yes |  |  |  |
| `outside_lookback` | integer | yes | E3 excluded |  |  |
| `flagged_exception` | integer | yes | E5 excluded |  |  |
| `offline_excluded` | integer | yes | E6 excluded |  |  |

## CampaignROI

Campaign ROI per model, USD (derived).

- **Primary key:** `(campaign_id, model)`

| Field | Type | Required | Allowed values / units | Unique | Validation |
|---|---|:-:|---|:-:|---|
| `campaign_id` | mixed | yes |  |  | see docs/attribution_rules.md |
| `campaign_name` | mixed | yes |  |  | see docs/attribution_rules.md |
| `channel_id` | mixed | yes |  |  | see docs/attribution_rules.md |
| `market` | mixed | yes |  |  | see docs/attribution_rules.md |
| `currency` | mixed | yes |  |  | see docs/attribution_rules.md |
| `budget_amount` | mixed | yes |  |  | see docs/attribution_rules.md |
| `status` | mixed | yes |  |  | see docs/attribution_rules.md |
| `spend` | mixed | yes |  |  | see docs/attribution_rules.md |
| `spend_usd` | mixed | yes |  |  | see docs/attribution_rules.md |
| `cost_records` | mixed | yes |  |  | see docs/attribution_rules.md |
| `quarantined_cost_usd` | mixed | yes |  |  | see docs/attribution_rules.md |
| `quarantined_cost_records` | mixed | yes |  |  | see docs/attribution_rules.md |
| `attributed_revenue_usd` | mixed | yes |  |  | see docs/attribution_rules.md |
| `attributed_conversions` | mixed | yes |  |  | see docs/attribution_rules.md |
| `credited_opportunities` | mixed | yes |  |  | see docs/attribution_rules.md |
| `attributed_revenue_native_same_currency` | mixed | yes |  |  | see docs/attribution_rules.md |
| `roas` | mixed | yes |  |  | see docs/attribution_rules.md |
| `roi_pct` | mixed | yes |  |  | see docs/attribution_rules.md |
| `cost_per_conversion_usd` | mixed | yes |  |  | see docs/attribution_rules.md |
| `roi_status` | mixed | yes |  |  | see docs/attribution_rules.md |
| `model` | mixed | yes |  |  | see docs/attribution_rules.md |

## ChannelROI

Channel roll-up of campaign ROI per model (derived).

- **Primary key:** `(channel_id, model)`

| Field | Type | Required | Allowed values / units | Unique | Validation |
|---|---|:-:|---|:-:|---|
| `channel_id` | mixed | yes |  |  | see docs/attribution_rules.md |
| `channel_name` | mixed | yes |  |  | see docs/attribution_rules.md |
| `channel_group` | mixed | yes |  |  | see docs/attribution_rules.md |
| `is_paid` | mixed | yes |  |  | see docs/attribution_rules.md |
| `spend_usd` | mixed | yes |  |  | see docs/attribution_rules.md |
| `attributed_revenue_usd` | mixed | yes |  |  | see docs/attribution_rules.md |
| `attributed_conversions` | mixed | yes |  |  | see docs/attribution_rules.md |
| `campaigns` | mixed | yes |  |  | see docs/attribution_rules.md |
| `campaigns_missing_cost` | mixed | yes |  |  | see docs/attribution_rules.md |
| `revenue_on_missing_cost_campaigns_usd` | mixed | yes |  |  | see docs/attribution_rules.md |
| `roas` | mixed | yes |  |  | see docs/attribution_rules.md |
| `roi_pct` | mixed | yes |  |  | see docs/attribution_rules.md |
| `revenue_share` | mixed | yes |  |  | see docs/attribution_rules.md |
| `model` | mixed | yes |  |  | see docs/attribution_rules.md |

## Ground truth (separate folder, never an engine input)

`data/ground_truth/ground_truth.csv|json`: `ground_truth_id`, `issue_type`, `record_type`, `record_id`, `related_id`, `expected_decision`, `injection_note`

`data/ground_truth/channel_contribution.csv|json`: `channel_id`, `channel_name`, `true_effect_logit`, `true_credited_revenue_usd`, `true_share`, `unattributable_won_revenue_usd`
