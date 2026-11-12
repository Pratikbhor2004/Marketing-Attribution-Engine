# Feature guide: what the problem is, and why, how to use and how we built each feature

## Part A — The problem statement, step by step

**Business question.** Marketing spends money across campaigns and channels: paid search, social, email, events, partners and others. Sales closes deals. *Which campaigns actually produced that revenue, and which ones paid for themselves?* The answer depends on the attribution rule you choose, so we must compare rules side by side and show how each one reaches its result.

**What we have to build**, in the order the brief asks for it:

1. **Master data first.** Channels, campaigns (budget, channel, market, flight dates), creatives, audiences, and the contacts we market to.
2. **Dependent records with valid parent IDs.** Touchpoints for each contact (with timestamps, online or offline), web sessions, leads linked to sessions, opportunities linked to leads, orders (closed revenue) and cost records (spend).
3. **Business logic.** Campaigns get budgets and channels. Journeys get timestamps. Sessions link to leads and opportunities through synthetic IDs. Costs and revenue are recorded. Attribution has explicit first-touch, last-touch and multi-touch rules. Evaluation periods are split chronologically.
4. **History, not overwrites.** Stage changes are events, and new attribution runs are new rows.
5. **Outcomes from rules.** Whether a deal is won must follow a documented rule, not a coin flip, and so must the anomaly labels.
6. **Entities and keys.** 12 core entities, each with a primary key and foreign keys. Cardinalities are documented, and an N:M relationship uses a bridge table. Every join is validated.
7. **ID conventions.** `CMP-2026-000001`, `TOUCH-<contact-id>-000001`, `SES-2026-000001`, `LEAD-2026-000001`, `OPP-2026-000001`.
8. **Volume.** 500 campaigns, 50,000 contacts, 200,000 touchpoints and 10,000 opportunities, with long conversion windows, offline touches, missing identifiers and multi-campaign journeys. A fixed seed, and a starter scale produced by the same generator.
9. **Edge cases, labelled separately.** Revenue counted twice, a touch after conversion that gets attributed, spend in the wrong currency, campaign costs left out, and an anonymous session linked to a person without evidence.
10. **Validation and exports.** Key, join, enum, date-order, arithmetic, lifecycle and duplicate checks. CSV, JSON and SQL exports with a README, data dictionary, manifest and validation summary. Generate twice to prove reproducibility.
11. **Acceptance.** Attribution rules are explicit, ROI reconciles to cost and revenue, time windows are respected, and missing identity links are visible.

**What we delivered on top:** a REST API (58 operations) with a Postman collection, a Streamlit dashboard, Docker packaging, use-case, sequence and ER diagrams, and this guide.

---

## Part B — Features

Each feature lists **Why** (the requirement it serves), **How to use** it, and **How we built** it.

### 1. Deterministic synthetic data generator
* **Why.** We need realistic, lawful, reproducible data at two scales (requirements 1–3, 5, 7–8).
* **How to use.** `python -m attribution_engine.cli generate --scale starter` (about 3 s) or `--scale full` (about 45 s). Add `--seed N` for a different but still reproducible world. In the API: `POST /api/v1/pipeline/generate`.
* **How we built it.** `generator.py` uses one seeded `numpy.random.Generator` and runs in parent-to-child order:
  1. Channels, FX rates, campaigns (24 always-on ones plus timed flights), creatives, audiences and the CampaignAudience bridge.
  2. Contacts, leads, then opportunities with planned dates. Sales cycles are log-normal, up to 540 days, which gives long conversion windows.
  3. Touchpoint journeys. Each touch is assigned only to a campaign whose flight covers that moment, and about 92 % stay in the contact's own market.
  4. Sessions: about 6 % are anonymous, and leads from event scans or CRM imports have no session (missing identifiers).
  5. Outcomes from a documented logistic rule over channel exposure, then lifecycle events, orders and monthly costs.

### 2. ID conventions and immutability
* **Why.** Traceability, plus consistent references across all exported files (requirement 7).
* **How to use.** IDs look like `CMP-2026-000001`, `CON-2026-000001`, `TOUCH-CON-2026-000001-000003`, `SES-2026-000001`, `LEAD-2026-000001`, `OPP-2026-000001`, `ORD-…`, `COST-…`, `EXC-…`, `RUN-2026-…`.
* **How we built it.** Counters are assigned after sorting by creation time, so they are chronological. Touch counters run per contact. Injected anomaly rows continue the counter and never reuse an ID. Validation checks every pattern with a regex.

### 3. Rule-derived outcomes and derived fields
* **Why.** "Generate outcomes from documented rules, not independent random assignment" and "calculate derived values from source records" (requirements 4–5).
* **How to use.** Read [attribution_rules.md §6](attribution_rules.md). The test `test_outcomes_follow_rule_not_coin_flip` shows that won deals had more pre-close exposure than lost ones.
* **How we built it.**
  * Win probability is `σ(−1 + Σ β·min(touches, 3) + size effect)`.
  * A deal is open when its planned close falls after the as-of date.
  * Lead status, contact lifecycle, contact `created_at`, `is_offline`, campaign status, cost amounts and order revenue are all computed from other records.

### 4. Labelled edge cases with separate ground truth
* **Why.** The five required negative scenarios, kept apart from the inputs (requirements 9 and Step 7).
* **How to use.** `data/ground_truth/ground_truth.csv|json`, or `GET /api/v1/ground-truth`. The full scale has 105 labelled anomalies.
* **How we built it.** `_inject_anomalies` adds **new** rows: re-imported invoices, second invoices, claimed clicks after conversion, adjustments in the wrong currency, and leads stitched to anonymous sessions. The one exception is the omitted-cost case, which removes a campaign's ledger rows. Unrelated rows are never altered. No input table has an `issue_type` column, and a validation check enforces that.

### 5. Rule-based exception detection
* **Why.** The engine must make the right decision on bad data without seeing the labels. It also gives a measurable precision and recall.
* **How to use.** `GET /api/v1/exceptions`, `/exceptions/summary` and `/exceptions/{id}` (which includes the offending record). `POST /exceptions/detect` reruns the detectors live.
* **How we built it.** `detection.py` has five detectors (R-REV-01, R-TCH-01, R-CST-01, R-CST-02, R-IDN-01). Each writes an `ExceptionLog` row with the rule, the evidence and the decision. `evaluate_detection` compares the log with ground truth. Result: precision = recall = 1.0 at both scales.

### 6. Attribution engine: five models with explicit eligibility
* **Why.** Compare attribution approaches with explicit rules and respected time windows (acceptance criteria).
* **How to use.** `GET /api/v1/attribution/models` documents the rules. `GET /attribution/results?model=…` returns credit rows, and `GET /attribution/opportunities/{id}` explains one conversion touch by touch.
* **How we built it.** `attribution.py` joins conversions to the contact's touches and labels each touch with eligibility reason E1–E6. It then computes per-touch weights for first-touch, last-touch, linear, position-based and time-decay. Credit is split in integer cents (largest remainder), so every model reconciles exactly.

### 7. Parameterised, persisted attribution runs
* **Why.** Analysts need to test sensitivity (lookback window, half-life, U-shape weights, whether to count offline touches) without regenerating data, and every run must stay traceable.
* **How to use.** `POST /api/v1/attribution/runs` with `{"lookback_days": 90, "include_offline": false}`, then pass `?run_id=RUN-2026-000001` to any results, ROI, compare or reconciliation endpoint.
* **How we built it.** Requests are validated with Pydantic models (422 on bad input). Each run appends rows to `AttributionRun`, `AttributionResult`, `UnattributedRevenue` and `TouchEligibilityAudit`. The baseline run is `RUN-BASELINE`.

### 8. Campaign and channel ROI
* **Why.** "Campaign ROI reconciles to cost and revenue."
* **How to use.** `GET /api/v1/roi/campaigns?model=time_decay&sort_by=roas`, `/roi/campaigns/{id}` (all models side by side) and `/roi/channels`.
* **How we built it.** `analytics.campaign_roi` converts valid spend and credited revenue to USD at monthly FX. Currency-mismatch costs are quarantined. A campaign with missing cost gets `roi_status = unavailable_missing_cost` and a null ROI, never a fake ROI against zero spend.

### 9. Money reconciliation
* **Why.** It proves that nothing is created or lost between orders, attribution and ROI.
* **How to use.** `GET /api/v1/roi/reconciliation?run_id=…`, or the `reconciliation` block in `evaluation_summary.json`.
* **How we built it.** For each currency and model: gross orders − duplicates = recognised = attributed + unattributed, to within 1 cent. Spend: ledger = valid + quarantined.

### 10. Identity-gap visibility
* **Why.** "Missing identity links are visible."
* **How to use.** `GET /api/v1/identity/gaps` and `/identity/rejected-links`. `GET /leads/{id}` shows `identity_link` as verified, rejected or no_session_offline_source.
* **How we built it.** `analytics.identity_gaps` counts anonymous sessions, leads without a session by source, rejected links, offline touches, touches with no creative, and unattributed revenue by reason.

### 11. Append-only opportunity lifecycle
* **Why.** "Persist state changes as explicit records; do not overwrite history."
* **How to use.** `GET /api/v1/opportunities/{id}/events` and `POST /api/v1/opportunities/{id}/events {"to_stage": "proposal"}`.
* **How we built it.** Allowed transitions are listed in `generator.OPP_TRANSITIONS`. An illegal move returns 409 and a back-dated event returns 422. The current stage comes from the SQL view `opportunity_current_stage`. The `Opportunity.stage` snapshot is never updated.

### 12. Model comparison and evaluation against ground truth
* **Why.** Meaningful evaluation metrics: which model best reflects the true drivers?
* **How to use.** `GET /api/v1/attribution/compare`, `/evaluation/model-accuracy` and `/evaluation/detection`.
* **How we built it.**
  * Channel revenue share for each model is compared with the true contribution share from the generating rule, using total variation distance and MAE.
  * Pairwise Spearman correlation is computed between models' campaign credit.
  * "Credit shift vs linear" shows how much money moves between campaigns when you change model.
  * At full scale, time-decay comes closest to the generating rule (TVD 0.252 against 0.266 for first-touch).

### 13. Chronological split
* **Why.** "Split evaluation periods chronologically."
* **How to use.** `GET /api/v1/evaluation/chronological?cutoff=2026-07-01`.
* **How we built it.** Conversions and spend are split at the cutoff. For each model, ROAS rank stability is measured per channel × market segment (Spearman), along with the shift in channel share between periods. At full scale: 7,448 train and 2,552 evaluation opportunities.

### 14. Validation suite (111 checks)
* **Why.** Step 8: keys, joins, enums, date order, arithmetic, lifecycle and duplicates.
* **How to use.** It runs automatically in `generate` (exit code 1 on failure). You can also run `python -m attribution_engine.cli validate`, `POST /api/v1/pipeline/validate`, or open `data/generated/validation_summary.json`.
* **How we built it.** `validation.py` checks:
  * primary and composite keys; 21 foreign keys (with nullability); ID regexes and enums
  * temporal rules: touch inside its campaign flight, events after their parents
  * lifecycle transitions and derived-state consistency
  * revenue and credit reconciliation; duplicate touches and invoices
  * that session-linked leads have identity evidence, and that no labels leak into inputs

  Only rows the detectors flagged may break a business rule.

### 15. Exports, manifest and provenance
* **Why.** Step 9.
* **How to use.** Everything is under `data/generated/` (CSV, JSON, `attribution.db`, `seed_data.sql`, summaries, `source_manifest.json`) and `data/ground_truth/`.
* **How we built it.**
  * `pipeline.export` writes one CSV and one JSON per table.
  * SQLite is built from explicit DDL with PRIMARY KEY, FOREIGN KEY and indexes, then checked with `PRAGMA foreign_key_check`. The SQL dump comes from that database.
  * The manifest records the seed, config, command, provenance (fully synthetic, no external data), row counts and SHA-256 hashes of the content and of each file.

### 16. Reproducibility proof
* **Why.** Step 10.
* **How to use.** `python -m attribution_engine.cli verify-reproducibility --scale full`, or `POST /api/v1/pipeline/reproducibility`.
* **How we built it.** It runs the full pipeline twice and compares row counts and SHA-256 content digests for all 23 tables, including ground truth. The latest full-scale report is `reports/reproducibility_report.json` (PASS).

### 17. Machine-readable data dictionary
* **Why.** Step 1: type, required flag, allowed values, units, uniqueness and validation rule for each field.
* **How to use.** [data_dictionary.md](data_dictionary.md), [data_dictionary.json](data_dictionary.json), or `GET /api/v1/meta/schema` for the live schema.
* **How we built it.** `schema.py` holds the specification and renders it. A test fails if any exported column is missing from it.

### 18. REST API and Postman collection
* **Why.** Every capability above is reachable by every actor.
* **How to use.** `uvicorn attribution_engine.api:app`, then open http://localhost:8000/docs. Import `postman/Marketing_Attribution_Engine.postman_collection.json` and the environment file, then run the collection top to bottom.
* **How we built it.**
  * FastAPI, with the SQLite store cached per database modification time.
  * The collection is **generated from the OpenAPI schema** (`scripts/build_postman_collection.py`), so it always contains every endpoint: 58 operations plus helper and negative-path requests (70 requests in total).
  * Each request has status tests, and list requests capture IDs for the detail requests that follow.
  * `scripts/run_postman_collection.py` runs the collection without Node, and a test asserts it passes 70/70.

### 19. Analyst dashboard
* **Why.** A visual way to compare models and ROI.
* **How to use.** `streamlit run dashboard/app.py` → http://localhost:8501.
* **How we built it.** Streamlit and Plotly over the same SQLite store and analytics functions as the API. Tabs: model comparison, campaign ROI, channel ROI, exceptions, identity gaps, explain a conversion, evaluation.

### 20. Docker packaging
* **Why.** One-command run on any machine.
* **How to use.** `docker compose up --build` → API on :8000 and dashboard on :8501. Use `DATA_SCALE=full docker compose up --build` for the full dataset.
* **How we built it.** A `python:3.12-slim` image running as a non-root user, with a health check on `/health`. The entrypoint generates the dataset on first start and then runs `api`, `dashboard`, `generate` or `test`. Compose shares one data volume between both services.
