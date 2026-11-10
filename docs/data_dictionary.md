# Data dictionary and schema contract

All records are synthetic. Timestamps are ISO-8601 local-naive strings (`YYYY-MM-DDTHH:MM:SS`); a production integration should use UTC with explicit offsets. Monetary values are decimal currency units (not minor units). IDs are immutable strings. `required` means non-null in baseline data unless a row is explicitly described as an allowed anomaly.

## Entity field definitions

| Entity.field | Type | Required? | Allowed values / units | Unique? | Validation |
|---|---|---:|---|---:|---|
| Channel.channel_id | string | Yes | `CH-` plus zero-padded number | PK | Non-null, unique |
| Channel.channel_name | string | Yes | Paid Search, Paid Social, Email, Organic Search, Affiliate, Display, Events, Partner | Yes | Must be in channel enum |
| Channel.channel_group | string | Yes | digital, offline, partner | No | Controlled group |
| Channel.is_paid | boolean | Yes | true/false | No | Boolean |
| Campaign.campaign_id | string | Yes | `CMP-2026-` plus 6 digits | PK | Non-null, unique |
| Campaign.campaign_name | string | Yes | Synthetic display name | No | Non-empty |
| Campaign.channel_id | string | Yes | Channel FK | No | Must exist in Channel |
| Campaign.creative_id | string | Yes | Creative FK | No | Must exist in Creative |
| Campaign.audience_id | string | Yes | Audience FK | No | Must exist in Audience |
| Campaign.budget_amount | decimal | Yes | Currency units | No | Must be >= 0 |
| Campaign.currency | string | Yes | INR, USD, EUR | No | Currency enum; no cross-currency arithmetic |
| Campaign.start_at | timestamp | Yes | ISO-8601 | No | Must be <= end_at |
| Campaign.end_at | timestamp | Yes | ISO-8601 | No | Must be >= start_at |
| Campaign.status | string | Yes | active, completed, paused | No | Status enum |
| Creative.creative_id | string | Yes | `CR-` plus 6 digits | PK | Non-null, unique |
| Creative.creative_name | string | Yes | Synthetic display name | No | Non-empty |
| Creative.format | string | Yes | image, video, text, landing_page | No | Format enum |
| Creative.version | string | Yes | A, B, C | No | Version enum |
| Audience.audience_id | string | Yes | `AUD-` plus 4 digits | PK | Non-null, unique |
| Audience.audience_name | string | Yes | Synthetic segment name | No | Non-empty |
| Audience.segment_type | string | Yes | prospect, retargeting, lookalike, customer | No | Segment enum |
| Audience.effective_from | date | Yes | ISO date | No | Must be <= effective_to |
| Audience.effective_to | date | Yes | ISO date | No | Must be >= effective_from |
| Contact.contact_id | string | Yes | `CON-` plus 7 digits | PK | Non-null, unique |
| Contact.created_at | timestamp | Yes | ISO-8601 | No | Valid timestamp |
| Contact.country | string | Yes | ISO 3166-1 alpha-2 sample values: IN, US, GB, DE, SG | No | Two-character country code |
| Contact.consent_status | string | Yes | granted, denied, unknown | No | Consent enum |
| Contact.lifecycle_state | string | Yes | prospect, customer, inactive | No | Lifecycle enum |
| WebSession.session_id | string | Yes | `SES-2026-` plus 6 digits | PK | Non-null, unique |
| WebSession.contact_id | string | No | Contact FK or null for anonymous | No | Null allowed only for unresolved anonymous sessions |
| WebSession.session_started_at | timestamp | Yes | ISO-8601 | No | Must be >= contact.created_at when identified |
| WebSession.source | string | Yes | Channel name enum | No | Must match Channel.channel_name |
| WebSession.device | string | Yes | mobile, desktop, tablet | No | Device enum |
| WebSession.identity_evidence | string | Yes | authenticated_or_cookie_consent, none | No | `none` requires null contact unless labelled anomaly downstream |
| Lead.lead_id | string | Yes | `LEAD-2026-` plus 6 digits | PK | Non-null, unique |
| Lead.contact_id | string | Yes | Contact FK | No | Must match identified session contact except labelled anomaly |
| Lead.session_id | string | Yes | WebSession FK | No | Must exist; session must precede lead creation |
| Lead.created_at | timestamp | Yes | ISO-8601 | No | Must be >= session_started_at |
| Lead.status | string | Yes | new, qualified, converted, disqualified | No | Lead status enum |
| Lead.source_system | string | Yes | web_form, crm_import, event_scan, synthetic_anomaly | No | Controlled source label |
| Opportunity.opportunity_id | string | Yes | `OPP-2026-` plus 6 digits | PK | Non-null, unique |
| Opportunity.contact_id | string | Yes | Contact FK | No | Must equal referenced Lead.contact_id |
| Opportunity.lead_id | string | Yes | Lead FK | No | Must exist |
| Opportunity.created_at | timestamp | Yes | ISO-8601 | No | Must be >= Lead.created_at |
| Opportunity.closed_at | timestamp | Conditional | ISO-8601 or null for open | No | Closed stages require date >= created_at; open requires null |
| Opportunity.stage | string | Yes | closed_won, closed_lost, open | No | Stage enum |
| Opportunity.amount | decimal | Yes | Currency units | No | Must be >= 0 |
| Opportunity.currency | string | Yes | INR, USD, EUR | No | Currency enum |
| OpportunityEvent.event_id | string | Yes | `OPPEVT-` plus opportunity ID and sequence | PK | Non-null, unique |
| OpportunityEvent.opportunity_id | string | Yes | Opportunity FK | No | Must exist |
| OpportunityEvent.from_stage | string | No | null, open, new, qualified, closed_won, closed_lost | No | Null only for creation event |
| OpportunityEvent.to_stage | string | Yes | new, qualified, closed_won, closed_lost | No | Controlled lifecycle state |
| OpportunityEvent.occurred_at | timestamp | Yes | ISO-8601 | No | Monotonic per opportunity |
| OpportunityEvent.event_type | string | Yes | created, stage_changed | No | Event enum |
| Touchpoint.touchpoint_id | string | Yes | `TOUCH-<contact-id>-` plus 6 digits | PK | Non-null, unique; sequence per contact |
| Touchpoint.contact_id | string | Yes | Contact FK | No | Must exist |
| Touchpoint.campaign_id | string | Yes | Campaign FK | No | Must exist |
| Touchpoint.occurred_at | timestamp | Yes | ISO-8601 | No | Eligible only at/before conversion |
| Touchpoint.touch_type | string | Yes | ad_click, email_open, web_visit, event_attendance, sales_call | No | Touch enum |
| Touchpoint.channel_id | string | Yes | Channel FK | No | Must match referenced Campaign.channel_id |
| Touchpoint.is_offline | boolean | Yes | true/false | No | Boolean |
| Touchpoint.issue_type | string | No | null, post_conversion_touch | No | Non-null only for labelled exception |
| Order.order_id | string | Yes | `ORD-` plus 7 digits | PK | Non-null, unique |
| Order.opportunity_id | string | Yes | Opportunity FK | No | Must reference closed_won for valid revenue |
| Order.external_invoice_id | string | Yes | Synthetic invoice identifier | Normally yes | Duplicate is retained only as labelled anomaly and excluded from revenue |
| Order.revenue_amount | decimal | Yes | Currency units | No | Must equal opportunity amount for generated single-order case |
| Order.currency | string | Yes | INR, USD, EUR | No | Must match opportunity currency for valid order |
| Order.recognized_at | timestamp | Yes | ISO-8601 | No | Must align to close date |
| Order.issue_type | string | No | null, duplicate_revenue | No | Duplicate anomaly quarantined |
| CostRecord.cost_record_id | string | Yes | `COST-` plus 7 digits | PK | Non-null, unique |
| CostRecord.campaign_id | string | Yes | Campaign FK | No | Must exist |
| CostRecord.amount | decimal | Yes | Currency units | No | Must be >= 0 |
| CostRecord.currency | string | Yes | INR, USD, EUR | No | Must match campaign currency or be labelled mismatch |
| CostRecord.incurred_at | timestamp | Yes | ISO-8601 | No | Valid timestamp |
| CostRecord.cost_type | string | Yes | media_spend, manual_adjustment | No | Cost type enum |
| CostRecord.issue_type | string | No | null, currency_mismatch | No | Mismatch quarantined from ROI |
| CampaignAudience.campaign_id | string | Yes | Campaign FK | Composite key | Must exist |
| CampaignAudience.audience_id | string | Yes | Audience FK | Composite key | Must exist |
| CampaignAudience.effective_from | timestamp | Yes | ISO-8601 | No | Must be <= effective_to |
| CampaignAudience.effective_to | timestamp | Yes | ISO-8601 | No | Must be >= effective_from |
| AttributionResult.attribution_id | string | Yes | Deterministic attribution key | PK | Non-null, unique |
| AttributionResult.opportunity_id | string | Yes | Opportunity FK | No | Must have valid recognized Order |
| AttributionResult.campaign_id | string | Yes | Campaign FK | No | Must exist |
| AttributionResult.model | string | Yes | first_touch, last_touch, linear_multi_touch | No | Model enum |
| AttributionResult.credited_revenue | decimal | Yes | Currency units | No | Non-negative; per-opportunity/model sum reconciles to valid order |
| AttributionResult.currency | string | Yes | INR, USD, EUR | No | Must match recognized Order currency |
| AttributionResult.eligible_touch_count | integer | Yes | Count of eligible events | No | Must be >= 1 |

## Cardinality and optionality

- Channel 1:N Campaign; Creative 1:N Campaign; Audience 1:N Campaign assignment.
- Campaign N:M Audience conceptually, represented by `CampaignAudience` with effective dates.
- Contact 1:N Touchpoint, WebSession, Lead and Opportunity.
- WebSession 0:1 Contact; anonymous sessions have null contact until evidence exists.
- Contact 1:N Lead; Lead 1:N Opportunity.
- Opportunity 0:N Order; duplicate invoice rows remain traceable but are excluded from recognized revenue.
- Opportunity 1:N OpportunityEvent; event history is append-only.
- Campaign 1:N CostRecord and 1:N AttributionResult; Opportunity 1:N AttributionResult.

## Data provenance

The generator uses no external datasets, scraping, personal information or confidential data. All names, IDs, dates, amounts and interactions are synthetic. `GroundTruth` is exported separately and is not written into the analytical SQLite input database.
