"""Machine-readable data dictionary.

Each field: (type, required, allowed values / units, unique, validation rule).
Rendered to docs/data_dictionary.md and docs/data_dictionary.json by
`python -m attribution_engine.schema`; tests assert every exported column is documented.
"""
from __future__ import annotations

import json
from pathlib import Path

TS = "timestamp (ISO-8601, YYYY-MM-DDTHH:MM:SS, UTC-naive)"
CUR = "INR | USD | EUR"
MONEY = "decimal, 2 dp, in `currency` units"

ENTITIES: dict[str, dict] = {
    "Channel": {
        "description": "Marketing channel (master data).", "pk": "channel_id", "cardinality": "Channel 1:N Campaign; Channel 1:N Touchpoint",
        "fields": {
            "channel_id": ("string", True, "CH-001 … CH-008", True, "PK"),
            "channel_name": ("string", True, "Paid Search, Paid Social, Email, Organic Search, Affiliate, Display, Events, Partner", True, "enum"),
            "channel_group": ("string", True, "digital | offline | partner", False, "enum"),
            "is_paid": ("boolean", True, "true/false", False, "boolean"),
            "default_source_system": ("string", True, "ad_platform, esp, web_analytics, affiliate_network, event_app, crm", False, "enum"),
        }},
    "FxRate": {
        "description": "Monthly synthetic reference rate to USD (reporting currency).", "pk": "fx_rate_id", "cardinality": "reference; joined by (currency, month)",
        "fields": {
            "fx_rate_id": ("string", True, "FX-<CUR>-<YYYY-MM>", True, "PK"),
            "currency": ("string", True, CUR, False, "enum"),
            "rate_month": ("string", True, "YYYY-MM", False, "(currency, rate_month) unique"),
            "rate_to_usd": ("decimal", True, "USD per 1 unit, 6 dp", False, "> 0; USD = 1"),
            "source": ("string", True, "synthetic_reference_rate", False, "provenance"),
        }},
    "Campaign": {
        "description": "Campaign with budget, channel, market and flight dates.", "pk": "campaign_id",
        "cardinality": "Campaign N:1 Channel; 1:N Creative; N:M Audience (CampaignAudience); 1:N Touchpoint; 1:N CostRecord",
        "fields": {
            "campaign_id": ("string", True, "CMP-2026-000001", True, "PK; ^CMP-2026-\\d{6}$"),
            "campaign_name": ("string", True, "<market> <channel> - <objective> <nnnn>", False, "non-empty"),
            "channel_id": ("string", True, "FK Channel", False, "FK"),
            "market": ("string", True, "IN | US | EU", False, "enum"),
            "currency": ("string", True, CUR, False, "derived from market; contracted billing currency"),
            "objective": ("string", True, "awareness | lead_generation | conversion", False, "enum"),
            "budget_amount": ("decimal", True, MONEY, False, ">= 0"),
            "start_at": (TS, True, "", False, "<= end_at"),
            "end_at": (TS, True, "", False, ">= start_at (always-on campaigns end 31 Dec)"),
            "is_evergreen": ("boolean", True, "true = always-on", False, "boolean"),
            "status": ("string", True, "active | completed", False, "derived: completed if end_at < data as-of date"),
        }},
    "Creative": {
        "description": "Ad creative / event kit belonging to one campaign.", "pk": "creative_id", "cardinality": "Creative N:1 Campaign; 1:N Touchpoint (optional)",
        "fields": {
            "creative_id": ("string", True, "CRV-2026-000001", True, "PK"),
            "campaign_id": ("string", True, "FK Campaign", False, "FK"),
            "creative_name": ("string", True, "", False, "non-empty"),
            "format": ("string", True, "image | video | text | landing_page | event_kit", False, "enum"),
            "version": ("string", True, "A | B | C", False, "enum"),
            "launched_at": (TS, True, "", False, "= campaign start_at"),
        }},
    "Audience": {
        "description": "Targeting segment.", "pk": "audience_id", "cardinality": "Audience N:M Campaign via CampaignAudience",
        "fields": {
            "audience_id": ("string", True, "AUD-2026-000001", True, "PK"),
            "audience_name": ("string", True, "", False, "non-empty"),
            "segment_type": ("string", True, "prospect | retargeting | lookalike | customer", False, "enum"),
            "effective_from": ("date", True, "YYYY-MM-DD", False, "<= effective_to"),
            "effective_to": ("date", False, "null = open-ended", False, ">= effective_from when set"),
        }},
    "CampaignAudience": {
        "description": "Bridge table for the Campaign N:M Audience relationship, with effective dates.", "pk": "campaign_audience_id",
        "cardinality": "bridge; (campaign_id, audience_id) unique",
        "fields": {
            "campaign_audience_id": ("string", True, "CAUD-2026-000001", True, "PK"),
            "campaign_id": ("string", True, "FK Campaign", False, "FK"),
            "audience_id": ("string", True, "FK Audience", False, "FK; (campaign_id, audience_id) unique"),
            "effective_from": (TS, True, "", False, "within campaign flight"),
            "effective_to": (TS, True, "", False, ">= effective_from"),
        }},
    "Contact": {
        "description": "Prospect / customer person (synthetic, no PII).", "pk": "contact_id",
        "cardinality": "Contact 1:N Touchpoint, WebSession, Lead, Opportunity",
        "fields": {
            "contact_id": ("string", True, "CON-2026-000001", True, "PK; ^CON-2026-\\d{6}$"),
            "created_at": (TS, True, "", False, "derived: <= first observed touch"),
            "country": ("string", True, "IN, US, DE, FR, NL", False, "ISO-3166 alpha-2"),
            "market": ("string", True, "IN | US | EU", False, "derived from country"),
            "company_size": ("string", True, "smb | mid_market | enterprise", False, "enum"),
            "consent_status": ("string", True, "granted | denied | unknown", False, "enum"),
            "lifecycle_state": ("string", True, "prospect | lead | opportunity | customer | inactive", False,
                                "derived: customer iff won opp; opportunity iff open opp; inactive iff no activity 180d"),
        }},
    "WebSession": {
        "description": "Website visit; may be anonymous.", "pk": "session_id", "cardinality": "WebSession N:0..1 Contact; 1:N Lead (optional)",
        "fields": {
            "session_id": ("string", True, "SES-2026-000001 (chronological)", True, "PK; ^SES-2026-\\d{6}$"),
            "contact_id": ("string", False, "FK Contact; null = anonymous", False, "null iff identity_evidence = none"),
            "anonymous_id": ("string", True, "ANON-<10 digits> cookie id", False, "not a key"),
            "started_at": (TS, True, "", False, ">= contact.created_at when identified"),
            "ended_at": (TS, True, "", False, ">= started_at"),
            "page_views": ("integer", True, ">= 1", False, ""),
            "traffic_channel_id": ("string", True, "FK Channel (digital)", False, "FK"),
            "device": ("string", True, "mobile | desktop | tablet", False, "enum"),
            "identity_evidence": ("string", True, "form_submit | login | email_click_id | cookie_match | none", False, "enum"),
        }},
    "Lead": {
        "description": "Marketing lead; web leads link to a session, offline leads do not.", "pk": "lead_id",
        "cardinality": "Lead N:1 Contact; N:0..1 WebSession; 1:0..1 Opportunity",
        "fields": {
            "lead_id": ("string", True, "LEAD-2026-000001 (chronological; injected anomalies appended)", True, "PK; ^LEAD-2026-\\d{6}$"),
            "contact_id": ("string", True, "FK Contact", False, "FK; must equal session contact"),
            "session_id": ("string", False, "FK WebSession; null for event_scan / crm_import", False, "session must carry identity evidence"),
            "created_at": (TS, True, "", False, ">= session.started_at and contact.created_at"),
            "source_system": ("string", True, "web_form | event_scan | crm_import", False, "enum"),
            "identity_link_method": ("string", True, "session_form_submit | event_badge_scan | crm_import | probabilistic_device_match", False, "audit"),
            "status": ("string", True, "new | qualified | converted | disqualified", False,
                       "derived: converted iff opportunity; disqualified if >120d old; qualified if touched since"),
        }},
    "Opportunity": {
        "description": "Sales opportunity (current-state snapshot; history in OpportunityEvent).", "pk": "opportunity_id",
        "cardinality": "Opportunity N:1 Lead, N:1 Contact; 1:N OpportunityEvent; 1:0..N Order",
        "fields": {
            "opportunity_id": ("string", True, "OPP-2026-000001 (chronological)", True, "PK; ^OPP-2026-\\d{6}$"),
            "contact_id": ("string", True, "FK Contact", False, "= lead.contact_id"),
            "lead_id": ("string", True, "FK Lead", False, "FK; unique"),
            "created_at": (TS, True, "", False, ">= lead.created_at"),
            "closed_at": (TS, False, "null while open", False, "null iff open stage; >= created_at"),
            "stage": ("string", True, "prospecting | qualified | proposal | closed_won | closed_lost", False,
                      "derived: open if planned close > as-of; won/lost from documented exposure rule"),
            "amount": ("decimal", True, MONEY, False, ">= 0"),
            "currency": ("string", True, CUR, False, "contact market currency"),
            "company_size": ("string", True, "smb | mid_market | enterprise", False, "= contact.company_size"),
        }},
    "OpportunityEvent": {
        "description": "Append-only lifecycle history. Current stage = latest event (view opportunity_current_stage).", "pk": "event_id",
        "cardinality": "OpportunityEvent N:1 Opportunity",
        "fields": {
            "event_id": ("string", True, "OPPEVT-<opportunity_id>-NN", True, "PK"),
            "opportunity_id": ("string", True, "FK Opportunity", False, "FK"),
            "sequence": ("integer", True, "1..n", False, "(opportunity_id, sequence) unique, chronological"),
            "event_type": ("string", True, "created | stage_changed | closed", False, "first event = created"),
            "from_stage": ("string", False, "null for created", False, "allowed transition"),
            "to_stage": ("string", True, "opportunity stage enum", False, "allowed transition from from_stage"),
            "occurred_at": (TS, True, "", False, ">= previous event"),
            "actor": ("string", True, "crm_sync (generated) | API caller", False, "audit"),
        }},
    "Touchpoint": {
        "description": "Contact-level marketing interaction (online or offline).", "pk": "touchpoint_id",
        "cardinality": "Touchpoint N:1 Contact, Campaign, Channel; N:0..1 Creative; N:0..1 Opportunity (platform claim)",
        "fields": {
            "touchpoint_id": ("string", True, "TOUCH-<contact_id>-000001 (per-contact chronological counter)", True, "PK; embeds contact_id"),
            "contact_id": ("string", True, "FK Contact", False, "FK"),
            "campaign_id": ("string", True, "FK Campaign", False, "occurred_at inside campaign flight"),
            "channel_id": ("string", True, "FK Channel", False, "= campaign.channel_id"),
            "creative_id": ("string", False, "FK Creative; null for offline touches", False, "FK"),
            "occurred_at": (TS, True, "", False, ">= contact.created_at"),
            "touch_type": ("string", True, "ad_click, ad_view, video_view, email_open, email_click, organic_visit, affiliate_click, "
                                           "event_attendance, booth_scan, partner_referral, sales_call", False, "enum; consistent with channel"),
            "is_offline": ("boolean", True, "", False, "derived from touch_type"),
            "source_system": ("string", True, "channel source system", False, ""),
            "reported_conversion_id": ("string", False, "FK Opportunity claimed by ad platform", False, "claim must precede close (R-TCH-01)"),
        }},
    "Order": {
        "description": "Closed revenue (billing) — the revenue source of truth.", "pk": "order_id", "cardinality": "Order N:1 Opportunity",
        "fields": {
            "order_id": ("string", True, "ORD-2026-000001", True, "PK"),
            "opportunity_id": ("string", True, "FK Opportunity (closed_won)", False, "FK"),
            "external_invoice_id": ("string", True, "INV-2026-000001", False, "unique among recognised orders (R-REV-01)"),
            "revenue_amount": ("decimal", True, MONEY, False, "= opportunity.amount for the recognised order"),
            "currency": ("string", True, CUR, False, "= opportunity.currency"),
            "recognized_at": (TS, True, "", False, ">= opportunity.closed_at"),
            "source_system": ("string", True, "billing | billing_reimport | manual_invoice", False, ""),
        }},
    "CostRecord": {
        "description": "Monthly campaign spend from the finance ledger.", "pk": "cost_record_id", "cardinality": "CostRecord N:1 Campaign",
        "fields": {
            "cost_record_id": ("string", True, "COST-2026-000001", True, "PK"),
            "campaign_id": ("string", True, "FK Campaign", False, "FK"),
            "period_month": ("string", True, "YYYY-MM", False, ""),
            "incurred_at": (TS, True, "", False, "inside campaign flight"),
            "amount": ("decimal", True, MONEY, False, ">= 0; derived from budget x days x utilisation"),
            "currency": ("string", True, CUR, False, "must equal campaign.currency (R-CST-01)"),
            "cost_type": ("string", True, "media_spend, platform_fee, content_production, commission, event_fee, partner_fee, agency_fee, manual_adjustment", False, "enum"),
            "source_system": ("string", True, "finance_ledger | spreadsheet_upload", False, ""),
        }},
    "AttributionRun": {
        "description": "One execution of the attribution engine with its parameters (derived).", "pk": "run_id", "cardinality": "AttributionRun 1:N AttributionResult",
        "fields": {
            "run_id": ("string", True, "RUN-BASELINE | RUN-2026-000001", True, "PK"),
            "created_at": ("string", True, "timestamp or 'baseline'", False, ""),
            "triggered_by": ("string", True, "", False, "audit"),
            "models": ("string", True, "comma list of models", False, ""),
            "lookback_days": ("integer", True, ">= 1", False, ""),
            "time_decay_half_life_days": ("decimal", True, "> 0", False, ""),
            "position_first_weight": ("decimal", True, "0..1", False, "first + last <= 1"),
            "position_last_weight": ("decimal", True, "0..1", False, ""),
            "include_offline": ("boolean", True, "", False, ""),
            "result_rows": ("integer", True, "", False, ""),
            "attributed_opportunities": ("integer", True, "", False, ""),
            "unattributed_opportunities": ("integer", True, "", False, ""),
            "attributed_revenue_usd": ("decimal", True, "USD", False, "first model in run"),
        }},
    "AttributionResult": {
        "description": "Credit given to a campaign for one conversion under one model (derived).", "pk": "attribution_id",
        "cardinality": "AttributionResult N:1 AttributionRun, Opportunity, Campaign",
        "fields": {
            "attribution_id": ("string", True, "ATR-<opp>-<FT|LT|LIN|POS|TD>-<campaign>[-<run>]", True, "PK"),
            "run_id": ("string", True, "FK AttributionRun", False, ""),
            "model": ("string", True, "first_touch | last_touch | linear_multi_touch | position_based | time_decay", False, "enum"),
            "opportunity_id": ("string", True, "FK Opportunity", False, "FK"),
            "contact_id": ("string", True, "FK Contact", False, "FK"),
            "campaign_id": ("string", True, "FK Campaign", False, "FK"),
            "channel_id": ("string", True, "FK Channel", False, "= campaign.channel_id"),
            "conversion_at": (TS, True, "", False, "= opportunity.closed_at"),
            "credit_share": ("decimal", True, "0..1, 6 dp", False, "sums to 1 per (run, model, opportunity)"),
            "credited_revenue": ("decimal", True, MONEY, False, "sums to recognised revenue per opportunity (integer cents)"),
            "currency": ("string", True, CUR, False, "= order currency"),
            "credited_revenue_usd": ("decimal", True, "USD at conversion-month FX", False, ""),
            "eligible_touch_count": ("integer", True, ">= 1", False, ""),
            "campaign_touch_count": ("integer", True, ">= 1", False, ""),
        }},
    "ExceptionLog": {
        "description": "Rule-detected data-quality exceptions and the decision applied (derived).", "pk": "exception_id", "cardinality": "references any source record",
        "fields": {
            "exception_id": ("string", True, "EXC-2026-000001", True, "PK"),
            "issue_type": ("string", True, "duplicate_revenue | post_conversion_touch | currency_mismatch | campaign_cost_omitted | anonymous_session_without_evidence", False, "enum"),
            "rule_id": ("string", True, "R-REV-01, R-TCH-01, R-CST-01, R-CST-02, R-IDN-01", False, ""),
            "record_type": ("string", True, "Order | Touchpoint | CostRecord | Campaign | Lead", False, ""),
            "record_id": ("string", True, "id of offending record", False, ""),
            "related_id": ("string", False, "original order / claimed opportunity / campaign / session", False, ""),
            "detail": ("string", True, "human-readable evidence", False, ""),
            "decision": ("string", True, "exclude_from_recognized_revenue | exclude_from_attribution | quarantine_cost_from_roi | roi_unavailable_missing_cost | reject_identity_link", False, ""),
        }},
    "UnattributedRevenue": {
        "description": "Recognised revenue with no eligible touch (kept visible, never dropped).", "pk": "(run_id, opportunity_id)", "cardinality": "",
        "fields": {"run_id": ("string", True, "", False, ""), "opportunity_id": ("string", True, "FK Opportunity", False, ""),
                   "contact_id": ("string", True, "", False, ""), "conversion_at": (TS, True, "", False, ""),
                   "revenue": ("decimal", True, MONEY, False, ""), "currency": ("string", True, CUR, False, ""),
                   "revenue_usd": ("decimal", True, "USD", False, ""),
                   "reason": ("string", True, "no_touchpoints_for_contact | all_touches_outside_lookback_or_ineligible | only_post_conversion_or_ineligible_touches", False, "")}},
    "TouchEligibilityAudit": {
        "description": "Per-conversion count of touches by eligibility outcome (derived).", "pk": "(run_id, opportunity_id)", "cardinality": "",
        "fields": {"run_id": ("string", True, "", False, ""), "opportunity_id": ("string", True, "", False, ""),
                   "after_conversion": ("integer", True, "E2 excluded", False, ""), "before_previous_conversion": ("integer", True, "E4 excluded", False, ""),
                   "eligible": ("integer", True, "", False, ""), "outside_lookback": ("integer", True, "E3 excluded", False, ""),
                   "flagged_exception": ("integer", True, "E5 excluded", False, ""), "offline_excluded": ("integer", True, "E6 excluded", False, "")}},
    "CampaignROI": {
        "description": "Campaign ROI per model, USD (derived).", "pk": "(campaign_id, model)", "cardinality": "",
        "fields": {c: ("mixed", True, "", False, "see docs/attribution_rules.md") for c in [
            "campaign_id", "campaign_name", "channel_id", "market", "currency", "budget_amount", "status", "spend", "spend_usd", "cost_records",
            "quarantined_cost_usd", "quarantined_cost_records", "attributed_revenue_usd", "attributed_conversions", "credited_opportunities",
            "attributed_revenue_native_same_currency", "roas", "roi_pct", "cost_per_conversion_usd", "roi_status", "model"]}},
    "ChannelROI": {
        "description": "Channel roll-up of campaign ROI per model (derived).", "pk": "(channel_id, model)", "cardinality": "",
        "fields": {c: ("mixed", True, "", False, "see docs/attribution_rules.md") for c in [
            "channel_id", "channel_name", "channel_group", "is_paid", "spend_usd", "attributed_revenue_usd", "attributed_conversions", "campaigns",
            "campaigns_missing_cost", "revenue_on_missing_cost_campaigns_usd", "roas", "roi_pct", "revenue_share", "model"]}},
}

GROUND_TRUTH = {
    "ground_truth": ["ground_truth_id", "issue_type", "record_type", "record_id", "related_id", "expected_decision", "injection_note"],
    "channel_contribution": ["channel_id", "channel_name", "true_effect_logit", "true_credited_revenue_usd", "true_share",
                             "unattributable_won_revenue_usd"],
}


def render_markdown() -> str:
    out = ["# Data dictionary", "",
           "Generated from `attribution_engine/schema.py` (`python -m attribution_engine.schema`). Every exported column is listed; "
           "a test fails if a column is added without documentation.", "",
           "Conventions: timestamps are ISO-8601 `YYYY-MM-DDTHH:MM:SS` (UTC-naive); money is decimal with 2 dp in the row's `currency`; "
           "USD conversions use the monthly `FxRate` of the transaction month; IDs are immutable and zero-padded.", ""]
    for name, e in ENTITIES.items():
        out += [f"## {name}", "", e["description"], "", f"- **Primary key:** `{e['pk']}`"]
        if e["cardinality"]:
            out.append(f"- **Relationships:** {e['cardinality']}")
        out += ["", "| Field | Type | Required | Allowed values / units | Unique | Validation |", "|---|---|:-:|---|:-:|---|"]
        for f, (typ, req, allowed, uniq, rule) in e["fields"].items():
            out.append(f"| `{f}` | {typ} | {'yes' if req else 'no'} | {allowed} | {'yes' if uniq else ''} | {rule} |")
        out.append("")
    out += ["## Ground truth (separate folder, never an engine input)", "",
            "`data/ground_truth/ground_truth.csv|json`: " + ", ".join(f"`{c}`" for c in GROUND_TRUTH["ground_truth"]), "",
            "`data/ground_truth/channel_contribution.csv|json`: " + ", ".join(f"`{c}`" for c in GROUND_TRUTH["channel_contribution"]), ""]
    return "\n".join(out)


def main() -> None:
    Path("docs").mkdir(exist_ok=True)
    Path("docs/data_dictionary.md").write_text(render_markdown(), encoding="utf-8")
    js = {n: {**{k: v for k, v in e.items() if k != "fields"},
              "fields": {f: dict(zip(["type", "required", "allowed", "unique", "validation"], spec)) for f, spec in e["fields"].items()}}
          for n, e in ENTITIES.items()}
    Path("docs/data_dictionary.json").write_text(json.dumps(js, indent=2), encoding="utf-8")
    print("wrote docs/data_dictionary.md and docs/data_dictionary.json")


if __name__ == "__main__":
    main()
