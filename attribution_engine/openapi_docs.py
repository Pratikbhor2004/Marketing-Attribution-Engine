"""Swagger / OpenAPI documentation for every API operation.

FastAPI generates the base spec (paths, parameters, schemas) from the code. This module enriches it with:

* a long-form Markdown description for every operation (purpose, actor, rules, how to use),
* documented error responses (404 / 409 / 422) with example bodies,
* a description and example value for every path and query parameter,
* named request-body examples for every POST,
* real example responses captured from the starter dataset (``openapi_examples.json``, written by
  ``python -m scripts.export_openapi``).

``install(app)`` replaces ``app.openapi`` so the live Swagger UI at ``/docs`` and ReDoc at ``/redoc`` show all of it.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

EXAMPLES_FILE = Path(__file__).with_name("openapi_examples.json")

NO_DATASET = "Dataset not found. Run `python -m attribution_engine.cli generate --scale starter` or POST /api/v1/pipeline/generate first."

# --------------------------------------------------------------------------------------------- parameters
# name -> (description, example)
PARAMS: dict[str, tuple[str, object]] = {
    "campaign_id": ("Campaign ID, format `CMP-2026-000001`.", "CMP-2026-000002"),
    "audience_id": ("Audience ID, format `AUD-2026-000001`.", "AUD-2026-000001"),
    "contact_id": ("Contact ID, format `CON-2026-000001`.", "CON-2026-000002"),
    "session_id": ("Web session ID, format `SES-2026-000001`.", "SES-2026-000001"),
    "lead_id": ("Lead ID, format `LEAD-2026-000001`.", "LEAD-2026-000001"),
    "opportunity_id": ("Opportunity ID, format `OPP-2026-000001`.", "OPP-2026-000001"),
    "touchpoint_id": ("Touchpoint ID, format `TOUCH-<contact-id>-000001` (sequence per contact).", "TOUCH-CON-2026-000001-000001"),
    "run_id": ("Attribution run. `RUN-BASELINE` is the run produced at generation time; custom runs are `RUN-2026-000001`, ... "
               "(see POST /api/v1/attribution/runs).", "RUN-BASELINE"),
    "exception_id": ("Exception ID, format `EXC-2026-000001`.", "EXC-2026-000001"),
    "job_id": ("Background job ID returned by POST /pipeline/generate or /pipeline/reproducibility.", "JOB-3f2a9c1d7e4b"),
    "channel_id": ("Channel: `CH-001` Paid Search, `CH-002` Paid Social, `CH-003` Email, `CH-004` Organic Search, "
                   "`CH-005` Affiliate, `CH-006` Display, `CH-007` Events, `CH-008` Partner.", "CH-001"),
    "market": ("Market: `IN` (INR), `US` (USD) or `EU` (EUR).", "IN"),
    "status": ("Status filter. Campaigns: `active` | `completed`. Leads: `new` | `qualified` | `converted` | `disqualified`.", None),
    "currency": ("ISO currency: `INR`, `USD` or `EUR`.", "INR"),
    "limit": ("Page size (1-5000). Paged responses return `total`, `limit`, `offset`, `items`.", 100),
    "offset": ("Number of rows to skip (for paging).", 0),
    "format": ("Creative format: `image`, `video`, `text`, `landing_page`, `event_kit`.", "video"),
    "month": ("FX month, `YYYY-MM`.", "2026-03"),
    "lifecycle_state": ("Contact lifecycle (rule-derived): `prospect`, `lead`, `opportunity`, `customer`, `inactive`.", "customer"),
    "company_size": ("Company size: `smb`, `mid_market`, `enterprise`.", "smb"),
    "identity_evidence": ("How the session was tied to a person: `form_submit`, `login`, `email_click_id`, `cookie_match`, `none`.", "none"),
    "anonymous": ("`true` = only sessions with no resolved contact; `false` = only identified sessions.", True),
    "source_system": ("Lead source: `web_form`, `event_scan` (offline), `crm_import` (offline).", "web_form"),
    "has_session": ("`false` = leads with no web session ID (offline / CRM sources); `true` = leads captured in a session.", False),
    "stage": ("Live stage from the append-only event history: `prospecting`, `qualified`, `proposal`, `closed_won`, `closed_lost`.", "closed_won"),
    "created_from": ("Created on or after (ISO date).", "2026-01-01"),
    "created_to": ("Created before (ISO date, exclusive).", "2026-07-01"),
    "is_offline": ("`true` = offline touches only (event attendance, booth scan, sales call).", True),
    "has_conversion_claim": ("`true` = touches where an ad platform reported a conversion (`reported_conversion_id`).", True),
    "period_month": ("Cost month, `YYYY-MM`.", "2026-03"),
    "model": ("Attribution model: `first_touch`, `last_touch`, `linear_multi_touch`, `position_based`, `time_decay`.", "linear_multi_touch"),
    "roi_status": ("`calculated`, `calculated_excluding_quarantined_cost` or `unavailable_missing_cost`.", "unavailable_missing_cost"),
    "sort_by": ("Sort column.", "roi_pct"),
    "descending": ("Sort descending (nulls always last).", True),
    "issue_type": ("Exception type: `duplicate_revenue`, `post_conversion_touch`, `currency_mismatch`, `campaign_cost_omitted`, "
                   "`anonymous_session_without_evidence`.", "duplicate_revenue"),
    "record_type": ("Type of the offending record: `Order`, `Touchpoint`, `CostRecord`, `Campaign`, `Lead`.", "Order"),
    "cutoff": ("Train / evaluation boundary (ISO date). Conversions and spend before it are *train*, after it *evaluation*.", "2026-07-01"),
    "only_failed": ("`true` = drop the per-check list and return only the totals and failed checks.", True),
}

# ---------------------------------------------------------------------------------------- request bodies
BODY_EXAMPLES: dict[str, dict] = {
    "POST /api/v1/pipeline/generate": {
        "starter": {"summary": "Starter scale (seconds)", "value": {"scale": "starter", "seed": 20261022}},
        "full": {"summary": "Full scale: 500 campaigns / 50k contacts / 200k touchpoints / 10k opportunities",
                 "value": {"scale": "full", "seed": 20261022}},
    },
    "POST /api/v1/pipeline/reproducibility": {
        "starter": {"summary": "Prove the starter dataset is reproducible", "value": {"scale": "starter", "seed": 20261022}},
    },
    "POST /api/v1/opportunities/{opportunity_id}/events": {
        "advance": {"summary": "qualified -> proposal", "value": {"to_stage": "proposal", "actor": "sales_ops", "note": "Proposal sent"}},
        "win": {"summary": "proposal -> closed_won (back-dated)",
                "value": {"to_stage": "closed_won", "occurred_at": "2026-11-05T10:30:00", "actor": "sales_ops", "note": "Contract signed"}},
        "invalid": {"summary": "Invalid jump (returns 409)", "value": {"to_stage": "prospecting", "actor": "sales_ops"}},
    },
    "POST /api/v1/attribution/runs": {
        "short_lookback": {"summary": "90-day lookback, online touches only",
                           "value": {"models": ["first_touch", "last_touch", "linear_multi_touch", "position_based", "time_decay"],
                                     "lookback_days": 90, "time_decay_half_life_days": 7, "position_first_weight": 0.4,
                                     "position_last_weight": 0.4, "include_offline": False, "triggered_by": "analyst"}},
        "defaults": {"summary": "All defaults (same as baseline)", "value": {}},
        "invalid": {"summary": "Invalid weights (returns 422)", "value": {"position_first_weight": 0.7, "position_last_weight": 0.6}},
    },
}

# -------------------------------------------------------------------------------------- error examples
def _err(desc: str, detail) -> dict:
    return {"description": desc, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"},
                                                                   "example": {"detail": detail}}}}


E404_DATA = _err("Dataset not generated yet.", NO_DATASET)


def E404(what: str, example_id: str) -> dict:
    return _err(f"{what} not found (or dataset not generated).", f"{what} {example_id} not found")


E404_RUN = E404("Run", "RUN-2026-999999")
E404_GT = _err("Ground-truth files not found.", "Ground truth not found. Generate the dataset first.")

# ------------------------------------------------------------------------------------------ operations
# "METHOD path" -> {"description": markdown, "responses": {status: response-object}}
OPS: dict[str, dict] = {
    # ----------------------------------------------------------------------------------------- System
    "GET /health": {"description": """
**Who:** system administrator, load balancer, Docker `HEALTHCHECK`.

Always returns 200 while the process is up. `dataset_available` says whether `attribution.db` exists in
`ATTRIBUTION_DATA_DIR`; if it is `false`, generate data with the CLI or `POST /api/v1/pipeline/generate`.
"""},
    "GET /api/v1/meta/manifest": {"description": """
**Who:** administrator, auditor.

The source manifest written by every export: seed, scale, full generator configuration, provenance statement
(synthetic, no PII), row counts and a SHA-256 checksum per table. Two exports with the same seed have identical checksums.
""", "responses": {"404": _err("Manifest not found.", "source_manifest.json not found. Generate the dataset first.")}},
    "GET /api/v1/meta/config": {"description": """
**Who:** analyst, administrator.

Default generator volumes (full scale), starter volumes, default attribution parameters (lookback 365 days,
half-life 14 days, position weights 0.4 / 0.4, offline included), model names and the allowed opportunity stage transitions.
Works without a dataset.
"""},
    "GET /api/v1/meta/schema": {"description": """
**Who:** analyst, integrator.

Introspects the live SQLite store: every table with its columns, types, primary key, foreign keys and current row count.
For field meanings see `docs/data_dictionary.md`.
""", "responses": {"404": E404_DATA}},
    # --------------------------------------------------------------------------------------- Pipeline
    "POST /api/v1/pipeline/generate": {"description": """
**Who:** system administrator. **Why:** rebuild the dataset at either scale from one generator.

Starts a background job that runs the whole pipeline:
`generate -> detect exceptions -> attribute (RUN-BASELINE) -> ROI -> validate -> export (CSV / JSON / SQLite / SQL / manifest)`.

**How to use:** send the body, keep the returned `job_id`, then poll `GET /api/v1/pipeline/jobs/{job_id}` until
`status` is `succeeded` (or `failed`, with `error`). Starter takes seconds, full about a minute.

> This **replaces** the current dataset, including custom runs and API-appended events.
""", "responses": {"202": {"description": "Job accepted (status `queued`)."}}},
    "POST /api/v1/pipeline/reproducibility": {"description": """
**Who:** system administrator, auditor. **Why:** prove that the same seed produces exactly the same data.

Generates the dataset twice in memory (does not touch the stored dataset) and compares the row count and SHA-256
checksum of every source, derived and ground-truth table. The job result has `reproducible: true` and an empty
`mismatched_tables` list on success. Poll `GET /api/v1/pipeline/jobs/{job_id}`.
""", "responses": {"202": {"description": "Job accepted (status `queued`)."}}},
    "GET /api/v1/pipeline/jobs": {"description": "All background jobs of this server process, newest first. Jobs are kept in memory and lost on restart."},
    "GET /api/v1/pipeline/jobs/{job_id}": {"description": """
Status of one job: `queued` -> `running` -> `succeeded` | `failed`. When succeeded, `result` holds the job output
(validation passed, row counts and detection F1 for *generate*; reproducible flag and checksums for *reproducibility*).
""", "responses": {"404": E404("Job", "JOB-000000000000")}},
    "GET /api/v1/pipeline/validation": {"description": """
**Who:** administrator, analyst.

The validation summary saved at generation time: 111 checks grouped by category (primary keys, foreign keys,
ID formats, chronology, lifecycle, money and reconciliation, label isolation, coverage, detection vs ground truth).
Use `only_failed=true` for a compact answer.
""", "responses": {"404": _err("Validation summary not found.", "validation_summary.json not found. Generate the dataset first.")}},
    "POST /api/v1/pipeline/validate": {"description": """
**Who:** administrator. **Why:** check the live database after the API has changed it.

Re-runs every validation check against the stored baseline, then separately checks events appended through
`POST /opportunities/{id}/events` (`live_lifecycle`: every appended transition allowed, events chronological).
`passed` is true only if both pass. Read-only.
""", "responses": {"404": E404_DATA}},
    # ------------------------------------------------------------------------------------ Master data
    "GET /api/v1/channels": {"description": "The 8 channels with group (`digital`, `offline`, `partner`), whether they are paid, and the source system that reports them.",
                             "responses": {"404": E404_DATA}},
    "GET /api/v1/campaigns": {"description": """
**Who:** marketing operations, analyst.

Paged list of campaigns: name, channel, market, currency, budget, start / end dates, objective, status.
Filter by `channel_id`, `market`, `status` (`active` | `completed`) and `currency`.
""", "responses": {"404": E404_DATA}},
    "GET /api/v1/campaigns/{campaign_id}": {"description": """
One campaign with everything needed to judge it: its creatives, the audiences it targets (with effective dates),
number of cost records, spend in its own currency, number of delivered touchpoints, and any exceptions raised on it
(for example `campaign_cost_omitted` or `currency_mismatch`).
""", "responses": {"404": E404("Campaign", "CMP-2026-999999")}},
    "GET /api/v1/campaigns/{campaign_id}/costs": {"description": """
Cost records of one campaign. `quarantined: true` marks records in a currency different from the campaign's
contracted currency (rule R-CST-01); they are excluded from ROI and never auto-converted. Returns `[]` for an unknown ID.
""", "responses": {"404": E404_DATA}},
    "GET /api/v1/campaigns/{campaign_id}/touchpoints": {"description": "Paged touchpoints delivered by one campaign (all contacts).", "responses": {"404": E404_DATA}},
    "GET /api/v1/creatives": {"description": "Paged list of creatives (ad / asset variants). Each belongs to exactly one campaign.",
                              "responses": {"404": E404_DATA}},
    "GET /api/v1/audiences": {"description": "All audiences (segments) with their market and definition.", "responses": {"404": E404_DATA}},
    "GET /api/v1/audiences/{audience_id}/campaigns": {"description": """
Campaigns targeting an audience. Campaign <-> Audience is many-to-many through the `CampaignAudience` bridge,
which carries the dates the targeting was effective. Returns `[]` for an unknown ID.
""", "responses": {"404": E404_DATA}},
    "GET /api/v1/fx-rates": {"description": """
Monthly reference rates to USD used for reporting. Cost and revenue stay in their native currency; USD values
use the rate of the month the money was incurred (cost) or recognised (revenue).
""", "responses": {"404": E404_DATA}},
    "GET /api/v1/contacts": {"description": """
Paged list of contacts (synthetic people, no real PII). `lifecycle_state` is **derived** by rule from activity:
customer > opportunity > inactive (no activity for 180 days) > lead > prospect.
""", "responses": {"404": E404_DATA}},
    "GET /api/v1/contacts/{contact_id}": {"description": "One contact with counts of touchpoints, web sessions, leads and opportunities.",
                                          "responses": {"404": E404("Contact", "CON-2026-999999")}},
    "GET /api/v1/contacts/{contact_id}/journey": {"description": """
**Who:** support agent, analyst. **Why:** trace exactly what one person did and in what order.

One chronological timeline merging touchpoints (with campaign and channel, offline flagged), web sessions (with identity evidence),
leads, opportunity stage events and orders (with invoice ID). Also lists the distinct campaigns touched.
""", "responses": {"404": E404("Contact", "CON-2026-999999")}},
    # ----------------------------------------------------------------------------------------- Funnel
    "GET /api/v1/sessions": {"description": """
Paged list of web sessions. `anonymous=true` returns sessions with no resolved contact; `identity_evidence=none`
returns sessions with no evidence tying them to a person. Those are the inputs of the R-IDN-01 identity rule.
""", "responses": {"404": E404_DATA}},
    "GET /api/v1/sessions/{session_id}": {"description": "One web session with the leads captured in it.",
                                          "responses": {"404": E404("Session", "SES-2026-999999")}},
    "GET /api/v1/leads": {"description": """
Paged list of leads. `has_session=false` returns leads with **no web session ID** (event scans, CRM imports); they are
still valid and attributable through the contact. `status` is rule-derived: converted (has an opportunity),
disqualified (older than 120 days), qualified (touched since creation), otherwise new.
""", "responses": {"404": E404_DATA}},
    "GET /api/v1/leads/{lead_id}": {"description": """
**Who:** support agent.

One lead with its session, its opportunity and an `identity_link` verdict:
* `verified`: the session belongs to the same contact and has identity evidence
* `rejected`: rule R-IDN-01 fired (no evidence, no resolved contact, or a different contact)
* `no_session_offline_source`: lead came from an offline / CRM source
""", "responses": {"404": E404("Lead", "LEAD-2026-999999")}},
    "GET /api/v1/opportunities": {"description": """
Paged list of opportunities. `stage` filters on the **live** stage (`current_stage`) computed from the append-only
event history (SQL view `opportunity_current_stage`). `stage_snapshot` is the stage at generation time and never changes.
""", "responses": {"404": E404_DATA}},
    "GET /api/v1/opportunities/{opportunity_id}": {"description": """
One opportunity with its live stage (`current`), orders (billing) and the baseline attribution credit per model and campaign.
""", "responses": {"404": E404("Opportunity", "OPP-2026-999999")}},
    "GET /api/v1/opportunities/{opportunity_id}/events": {"description": """
The full lifecycle history of an opportunity, ordered by `sequence`. History is append-only: generated events have
`actor = crm_sync`, events added through the API carry the caller's `actor`.
""", "responses": {"404": E404("Opportunity", "OPP-2026-999999")}},
    "POST /api/v1/opportunities/{opportunity_id}/events": {"description": """
**Who:** sales operations. **Why:** move a deal forward without ever overwriting history.

Appends one stage-change event. Allowed transitions:

| From | To |
|---|---|
| `prospecting` | `qualified`, `closed_lost` |
| `qualified` | `proposal`, `closed_lost` |
| `proposal` | `closed_won`, `closed_lost` |
| `closed_won` / `closed_lost` | *(terminal, nothing)* |

`occurred_at` defaults to now (UTC) and must not be earlier than the latest event. The response contains the appended event and the new live stage.

> A `closed_won` event does **not** create revenue. Revenue is only recognised from `Order` (billing) records.

**Tip:** find a movable deal with `GET /api/v1/opportunities?stage=qualified`.
""", "responses": {
        "201": {"description": "Event appended."},
        "404": E404("Opportunity", "OPP-2026-999999"),
        "409": _err("Transition not allowed from the current stage.",
                    {"error": "invalid_transition", "current_stage": "qualified", "requested": "prospecting",
                     "allowed": ["closed_lost", "proposal"]}),
        "422": _err("Event is older than the latest event (out of order), or the body is invalid.",
                    {"error": "out_of_order_event", "last_event_at": "2026-09-14T11:02:51"}),
    }},
    "GET /api/v1/touchpoints": {"description": """
Paged list of touchpoints (ad clicks, email opens, event attendance, sales calls, ...). Useful filters:
`is_offline=true` for offline touches; `has_conversion_claim=true` for touches an ad platform claims produced a
conversion. Those are checked by rule R-TCH-01 (a claim after the deal closed is excluded from attribution).
""", "responses": {"404": E404_DATA}},
    "GET /api/v1/touchpoints/{touchpoint_id}": {"description": "One touchpoint.", "responses": {"404": E404("Touchpoint", "TOUCH-CON-2026-999999-000001")}},
    "GET /api/v1/orders": {"description": """
Paged list of orders (billing records, the **source of truth for revenue**). `recognised: false` marks an order
detected as duplicate revenue by rule R-REV-01 (repeated invoice ID, or cumulative orders above the deal amount); it
is excluded from attribution and ROI.
""", "responses": {"404": E404_DATA}},
    "GET /api/v1/cost-records": {"description": "Paged list of monthly cost records (spend per campaign, cost type and currency).",
                                 "responses": {"404": E404_DATA}},
    # ------------------------------------------------------------------------------------ Attribution
    "GET /api/v1/attribution/models": {"description": """
**Who:** analyst. The five models, the six touch-eligibility rules (E1-E6) every model shares, the default parameters
and how revenue is defined. Works without a dataset. Full definitions: `docs/attribution_rules.md`.
"""},
    "POST /api/v1/attribution/runs": {"description": """
**Who:** analyst. **Why:** ask "what if?" (shorter lookback, faster decay, ignore offline touches) without changing the baseline.

Runs the selected models over the live data with your parameters and **appends** the results as a new run
`RUN-2026-000001`, `RUN-2026-000002`, ... (the baseline is never modified). The response includes per-model attributed USD,
the touch-eligibility counts (how many touches each rule excluded) and `reconciliation_passed`.

Then use the `run_id` with `/attribution/results`, `/attribution/compare`, `/roi/campaigns`, `/roi/reconciliation`, ...

Validation (422): `lookback_days` 1-3650, `time_decay_half_life_days` > 0, each position weight 0-1 and their sum <= 1.
""", "responses": {
        "201": {"description": "Run created and persisted."},
        "404": E404_DATA,
        "422": _err("Invalid parameters.", "position_first_weight + position_last_weight must be <= 1"),
    }},
    "GET /api/v1/attribution/runs": {"description": "All attribution runs with their parameters, who triggered them and when. `RUN-BASELINE` is always present.",
                                     "responses": {"404": E404_DATA}},
    "GET /api/v1/attribution/runs/{run_id}": {"description": "One run's parameters plus, per model: result rows, opportunities credited and total credited USD.",
                                              "responses": {"404": E404_RUN}},
    "GET /api/v1/attribution/results": {"description": """
Paged credit rows: one row per (run, model, opportunity, campaign) with `credit_share`, `credited_revenue` in the
order currency, `credited_revenue_usd`, the number of eligible touches and the conversion date. Filter by model, campaign,
opportunity or channel.
""", "responses": {"404": E404_RUN}},
    "GET /api/v1/attribution/opportunities/{opportunity_id}": {"description": """
**Who:** analyst, support agent. **Why:** answer "why did this campaign get this much credit?" for one deal.

Returns every touch of the deal's contact with an `eligibility` verdict (`eligible`, `after_conversion`,
`outside_lookback`, `before_previous_conversion`, `flagged_exception`, `offline_excluded`, or `no_conversion` for an
open / lost deal), days before conversion, and a side-by-side table of credited revenue per campaign for every model.
""", "responses": {"404": E404("Opportunity", "OPP-2026-999999")}},
    "GET /api/v1/attribution/compare": {"description": """
**Who:** analyst. **Why:** see how much the choice of model changes the story.

Per model: revenue share by channel, Spearman rank correlation of campaign credit between every pair of models,
total credit shifted between models, and the distance of each model's channel mix from the ground-truth contribution.
""", "responses": {"404": E404_RUN}},
    "GET /api/v1/attribution/unattributed": {"description": """
Recognised revenue that no touch could be credited for (for example, no eligible touch inside the lookback window),
with the reason. It is reported, not silently dropped, so totals always reconcile.
"""},
    # -------------------------------------------------------------------------------------------- ROI
    "GET /api/v1/roi/campaigns": {"description": """
**Who:** analyst, marketing operations. Campaign ROI for one model, in USD.

`spend_usd` = valid cost records x FX; `roas` = attributed / spend; `roi_pct` = (attributed - spend) / spend x 100.
`roi_status`:
* `calculated`
* `calculated_excluding_quarantined_cost`: some cost rows were in the wrong currency and left out
* `unavailable_missing_cost`: no valid cost, so ROI and ROAS are **null** (never computed against zero spend)

Sort with `sort_by` / `descending`; filter by `roi_status`, `channel_id` or `market`.
""", "responses": {"404": E404_RUN}},
    "GET /api/v1/roi/campaigns/{campaign_id}": {"description": "One campaign's ROI row under every model in the run, side by side.",
                                                "responses": {"404": E404("Campaign", "CMP-2026-999999")}},
    "GET /api/v1/roi/channels": {"description": """
Channel roll-up for one model. Campaigns with missing cost are excluded from the channel's ROI and their revenue is
reported separately (`campaigns_missing_cost`, `revenue_on_missing_cost_campaigns_usd`), so a channel never looks better because costs are missing.
""", "responses": {"404": E404_RUN}},
    "GET /api/v1/roi/reconciliation": {"description": """
**Who:** finance, auditor. **Why:** prove no money was created or lost.

For each currency and each model: `gross orders = duplicate revenue + attributed + unattributed`, plus spend
(valid vs quarantined). `passed: true` means every identity holds to the cent.
""", "responses": {"404": E404_RUN}},
    # ------------------------------------------------------------------------------------- Exceptions
    "GET /api/v1/exceptions": {"description": """
**Who:** analyst, support agent. Paged exception log. Each row has the rule that fired (`R-REV-01`, `R-TCH-01`,
`R-CST-01`, `R-CST-02`, `R-IDN-01`), the offending record, a human-readable `detail`, and the `decision` applied by the engine.
Exceptions are found by rules only; labels are never inputs.
""", "responses": {"404": E404_DATA}},
    "GET /api/v1/exceptions/summary": {"description": "For each of the five issue types: rule ID, rule text, decision applied, and how many were detected.",
                                       "responses": {"404": E404_DATA}},
    "GET /api/v1/exceptions/{exception_id}": {"description": "One exception plus the full offending record (order, touchpoint, cost record, campaign or lead).",
                                              "responses": {"404": E404("Exception", "EXC-2026-999999")}},
    "POST /api/v1/exceptions/detect": {"description": """
Dry run: re-runs all five rule detectors on the current source tables and reports counts per issue type and whether
the result matches the stored exception log. Nothing is written. Returns at most 200 items.
""", "responses": {"404": E404_DATA}},
    # --------------------------------------------------------------------------------------- Identity
    "GET /api/v1/identity/gaps": {"description": """
**Why:** make missing identity links visible instead of hiding them.

Counts of anonymous sessions, sessions without evidence, leads without a session ID, rejected identity links,
and the revenue that could not be attributed, with reasons.
""", "responses": {"404": E404_DATA}},
    "GET /api/v1/identity/rejected-links": {"description": "Every lead whose session link was rejected by R-IDN-01, joined with the session so the missing evidence is visible.",
                                            "responses": {"404": E404_DATA}},
    # ------------------------------------------------------------------------------------- Evaluation
    "GET /api/v1/evaluation/summary": {"description": "Full evaluation report saved at generation time: detection scores, model comparison, chronological split, reconciliation.",
                                       "responses": {"404": _err("Evaluation summary not found.", "evaluation_summary.json not found. Generate the dataset first.")}},
    "GET /api/v1/evaluation/detection": {"description": """
Scores the rule detectors against the **separate** ground-truth file: true positives, false positives, false negatives,
precision, recall and F1 per issue type and overall.
""", "responses": {"404": E404_GT}},
    "GET /api/v1/evaluation/model-accuracy": {"description": """
How close each model's channel revenue share is to the true channel contribution used to generate outcomes
(total variation distance: 0 = identical, 1 = completely different), and which model is closest.
""", "responses": {"404": E404_GT}},
    "GET /api/v1/evaluation/chronological": {"description": """
**Why:** check that a model's conclusions hold over time, not just in hindsight.

Splits conversions and spend at `cutoff` into train and evaluation periods. Per model: ROAS by (channel x market) segment
in each period, the Spearman rank stability between the two (`roas_rank_stability_spearman`), and how much the
channel mix shifts (`channel_share_shift_tvd`).
""", "responses": {"404": E404_RUN, "422": _err("Invalid cutoff date.", "cutoff must be an ISO date")}},
    "GET /api/v1/ground-truth": {"description": """
The labelled anomalies (what was planted and the expected decision). Stored in `data/ground_truth/`, separate from
the inputs; used only for evaluation, never by the engine.
""", "responses": {"404": E404_GT}},
    "GET /api/v1/ground-truth/channel-contribution": {"description": "The true channel contribution derived from the documented generating rule (the yardstick for model accuracy).",
                                                      "responses": {"404": E404_GT}},
}

ERROR_SCHEMA = {"title": "ErrorResponse", "type": "object", "required": ["detail"],
                "properties": {"detail": {"description": "Message, or an object with `error` and context for 409 / 422 business errors.",
                                          "anyOf": [{"type": "string"}, {"type": "object"}]}}}

INTRO = """
Join campaign touches, web sessions, leads, opportunities and revenue to compare attribution approaches and ROI,
with explicit rules, exceptions and traceability.

### Quick start
1. Generate data: `python -m attribution_engine.cli generate --scale starter` (or `POST /api/v1/pipeline/generate`).
2. Read the data: **Master data** and **Funnel**.
3. Compare models: `GET /api/v1/attribution/compare`; explain one deal: `GET /api/v1/attribution/opportunities/{id}`.
4. ROI: `GET /api/v1/roi/campaigns?model=time_decay`; prove money adds up: `GET /api/v1/roi/reconciliation`.
5. Data quality: `GET /api/v1/exceptions/summary`; score against ground truth: `GET /api/v1/evaluation/detection`.

### Conventions
* All endpoints are under `/api/v1` except `/health`. JSON in and out. No authentication (local prototype).
* List endpoints are paged: `?limit=&offset=` -> `{"total", "limit", "offset", "items"}`.
* Money is reported in the native currency plus `*_usd` at the monthly FX rate.
* Errors: `{"detail": ...}` with 404 (not found / no dataset), 409 (invalid stage transition), 422 (invalid input).
* `run_id` defaults to `RUN-BASELINE` everywhere.

Docs: `docs/api_reference.md` (with example responses), `docs/openapi.json` / `.yaml`, Postman collection in `postman/`.
"""


def _load_examples() -> dict:
    if EXAMPLES_FILE.exists():
        return json.loads(EXAMPLES_FILE.read_text(encoding="utf-8"))
    return {}


def enrich(spec: dict) -> dict:
    """Return a copy of ``spec`` with descriptions, parameter docs, examples and error responses merged in."""
    spec = copy.deepcopy(spec)
    spec["info"]["description"] = INTRO.strip()
    spec["info"]["contact"] = {"name": "Marketing Attribution Engine"}
    spec["info"]["license"] = {"name": "Synthetic data, no real PII"}
    spec["servers"] = [{"url": "http://localhost:8000", "description": "Local (uvicorn / Docker)"}]
    spec.setdefault("components", {}).setdefault("schemas", {})["ErrorResponse"] = ERROR_SCHEMA
    examples = _load_examples()
    for path, item in spec["paths"].items():
        for method, op in item.items():
            key = f"{method.upper()} {path}"
            doc = OPS.get(key, {})
            if doc.get("description"):
                op["description"] = doc["description"].strip()
            for p in op.get("parameters", []):
                if p["name"] in PARAMS:
                    desc, ex = PARAMS[p["name"]]
                    p.setdefault("description", desc)
                    if ex is not None and "example" not in p:
                        p["example"] = ex
            if key in BODY_EXAMPLES and "requestBody" in op:
                op["requestBody"]["content"]["application/json"]["examples"] = BODY_EXAMPLES[key]
            responses = op.setdefault("responses", {})
            for code, resp in doc.get("responses", {}).items():
                if code in responses:                       # keep FastAPI's schema, add our description
                    responses[code]["description"] = resp["description"]
                    if "content" in resp:
                        responses[code]["content"] = resp["content"]
                else:
                    responses[code] = copy.deepcopy(resp)
            ex = examples.get(key)
            if ex is not None:
                code = next((c for c in ("200", "201", "202") if c in responses), "200")
                responses.setdefault(code, {"description": "Successful Response"}).setdefault("content", {}) \
                    .setdefault("application/json", {})["example"] = ex
    return spec


def install(app) -> None:
    """Serve the enriched spec at /openapi.json (and therefore in Swagger UI /docs and ReDoc /redoc)."""
    base_openapi = app.openapi

    def openapi():
        if app.openapi_schema is None:
            app.openapi_schema = enrich(base_openapi())
        return app.openapi_schema

    app.openapi = openapi
