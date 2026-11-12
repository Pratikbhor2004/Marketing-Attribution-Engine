# Architecture, use cases and sequence diagrams

The diagrams are Mermaid, so they render on GitHub, in VS Code (with a Mermaid extension) and at mermaid.live.

## 1. Components

```mermaid
flowchart LR
  subgraph Engine["attribution_engine (Python package)"]
    CFG[config.py<br/>DatasetConfig / AttributionParams]
    GEN[generator.py<br/>master → funnel → events → outcomes → anomalies]
    DET[detection.py<br/>5 rule detectors R-*]
    ATT[attribution.py<br/>eligibility E1–E6 + 5 models]
    ANA[analytics.py<br/>ROI · reconciliation · comparison · chronology · identity]
    VAL[validation.py<br/>111 checks]
    PIPE[pipeline.py<br/>orchestration · SQLite · export · digests]
    SCH[schema.py<br/>data dictionary]
  end
  CLI[cli.py] --> PIPE
  PIPE --> GEN --> DET --> ATT --> ANA
  PIPE --> VAL
  PIPE -->|CSV / JSON / SQL / SQLite| STORE[(data/generated)]
  PIPE -->|labels only| GT[(data/ground_truth)]
  API[api.py · FastAPI] --> STORE
  API -. evaluation only .-> GT
  DASH[dashboard/app.py · Streamlit] --> STORE
  POST[postman/ collection] --> API
```

**Design choices**

* **Labels never touch the engine.** Input tables contain no `issue_type`. The engine finds exceptions with rules (`detection.py`), and ground truth is read only by evaluation code.
* **Derived values are computed, not generated.** Contact `created_at` and `lifecycle_state`, lead `status`, opportunity stage and outcome, `is_offline`, cost amounts, order revenue, credit and ROI are all calculated from source records.
* **History is append-only.** Opportunity stage changes are events. The current stage is a SQL view, and attribution runs are new rows keyed by `run_id`.
* **Money is conserved.** Credit is split in integer cents, and reconciliation proves gross = duplicates + attributed + unattributed for every currency and model.
* **The same generator serves both scales.** `DatasetConfig.starter()` and `DatasetConfig()` differ only in volumes, and the seed drives a single `numpy.random.Generator`.

## 2. Entity-relationship diagram

```mermaid
erDiagram
  CHANNEL ||--o{ CAMPAIGN : "runs on"
  CAMPAIGN ||--|{ CREATIVE : has
  CAMPAIGN ||--o{ CAMPAIGN_AUDIENCE : targets
  AUDIENCE ||--o{ CAMPAIGN_AUDIENCE : "targeted by"
  CAMPAIGN ||--o{ COST_RECORD : incurs
  CAMPAIGN ||--o{ TOUCHPOINT : delivers
  CHANNEL ||--o{ TOUCHPOINT : via
  CREATIVE |o--o{ TOUCHPOINT : "shown in (optional)"
  CONTACT ||--o{ TOUCHPOINT : receives
  CONTACT |o--o{ WEB_SESSION : "identified in (optional)"
  CONTACT ||--o{ LEAD : becomes
  WEB_SESSION |o--o{ LEAD : "captured (optional)"
  LEAD ||--o| OPPORTUNITY : "converts to"
  CONTACT ||--o{ OPPORTUNITY : owns
  OPPORTUNITY ||--|{ OPPORTUNITY_EVENT : "history (append-only)"
  OPPORTUNITY ||--o{ ORDER : "billed as"
  OPPORTUNITY |o--o{ TOUCHPOINT : "claimed by platform"
  ATTRIBUTION_RUN ||--o{ ATTRIBUTION_RESULT : produces
  OPPORTUNITY ||--o{ ATTRIBUTION_RESULT : credited
  CAMPAIGN ||--o{ ATTRIBUTION_RESULT : receives
  FX_RATE }o..o{ COST_RECORD : "converts (currency, month)"
  FX_RATE }o..o{ ORDER : "converts (currency, month)"
```

Cardinality summary: Channel 1:N Campaign; Campaign 1:N Creative; Campaign **N:M** Audience through the `CampaignAudience` bridge (with effective dates); Contact 1:N Touchpoint / Lead / Opportunity; Contact 0..1:N WebSession (anonymous sessions have no contact); WebSession 0..1:N Lead (offline leads have no session); Lead 1:0..1 Opportunity; Opportunity 1:N OpportunityEvent; Opportunity 1:0..N Order (more than one valid order is an exception); AttributionRun 1:N AttributionResult.

## 3. Actors and use cases

| Actor | Goals | Main entry points |
|---|---|---|
| Customer / prospect | Generates the behaviour being measured: touches, sessions, form fills, purchases | Synthetic: `generator.py` |
| Marketing & sales operations | Manage campaigns and audiences, move deals through stages, check spend | `/campaigns`, `/opportunities/{id}/events`, `/cost-records` |
| Support agent | Investigate a contact's journey, a rejected identity link or a duplicate invoice | `/contacts/{id}/journey`, `/leads/{id}`, `/exceptions/{id}` |
| Analyst | Compare models, read ROI, rerun with other parameters, evaluate against ground truth | `/attribution/*`, `/roi/*`, `/evaluation/*`, dashboard |
| System administrator | Generate and regenerate data, validate, prove reproducibility, check health | CLI, `/pipeline/*`, `/health`, Docker |

```mermaid
flowchart LR
  P([Customer / prospect])
  M([Marketing & sales ops])
  S([Support agent])
  A([Analyst])
  X([System administrator])

  subgraph SYS[Marketing Attribution Engine]
    UC1((Interact with campaigns:<br/>touches, sessions, leads, orders))
    UC2((Manage campaigns,<br/>audiences & spend))
    UC3((Advance opportunity stage<br/>append-only))
    UC4((Trace contact journey))
    UC5((Triage exception /<br/>identity link))
    UC6((Compare attribution models))
    UC7((Run attribution with<br/>custom parameters))
    UC8((Review campaign & channel ROI))
    UC9((Evaluate vs ground truth<br/>& chronological split))
    UC10((Generate dataset))
    UC11((Validate dataset))
    UC12((Verify reproducibility))
    UC13((Detect exceptions))
    UC14((Reconcile revenue & spend))
  end

  P --- UC1
  M --- UC2
  M --- UC3
  M --- UC8
  S --- UC4
  S --- UC5
  A --- UC6
  A --- UC7
  A --- UC8
  A --- UC9
  X --- UC10
  X --- UC11
  X --- UC12
  UC10 -. include .-> UC13
  UC10 -. include .-> UC11
  UC7 -. include .-> UC14
  UC8 -. include .-> UC14
  UC5 -. extend .-> UC4
```

## 4. Sequence diagrams

### 4.1 Generate a dataset (administrator)

```mermaid
sequenceDiagram
  actor Admin as System administrator
  participant CLI as cli.py / POST /pipeline/generate
  participant Gen as generator
  participant Det as detection
  participant Att as attribution
  participant Ana as analytics
  participant Val as validation
  participant Exp as pipeline.export
  Admin->>CLI: generate --scale full --seed 20261022
  CLI->>Gen: generate_dataset(cfg)
  Gen->>Gen: master data → contacts → leads → opportunities
  Gen->>Gen: journeys/touchpoints → sessions
  Gen->>Gen: outcome rule → events, orders, costs, lifecycle
  Gen->>Gen: inject 5 anomaly classes as new rows
  Gen-->>CLI: tables + ground truth (separate)
  CLI->>Det: detect_exceptions(tables)
  Det-->>CLI: ExceptionLog (rule, decision)
  CLI->>Att: run_attribution(tables, exceptions, params)
  Att-->>CLI: results, unattributed, eligibility audit
  CLI->>Ana: ROI, reconciliation, comparison, chronology
  CLI->>Val: validate_dataset(...)
  Val-->>CLI: 111 checks, passed?
  CLI->>Exp: CSV / JSON / SQLite (PK+FK) / SQL / manifest
  Exp-->>Admin: summary JSON (exit 1 if validation fails)
```

### 4.2 Compare models and rerun with new parameters (analyst)

```mermaid
sequenceDiagram
  actor An as Analyst
  participant API as FastAPI
  participant DB as SQLite store
  participant Att as attribution
  participant Ana as analytics
  An->>API: GET /attribution/compare
  API->>DB: AttributionResult (RUN-BASELINE)
  API->>Ana: model_comparison(+ ground-truth channel share)
  Ana-->>An: channel shares, rank correlation, distance to truth
  An->>API: POST /attribution/runs {lookback_days: 90, include_offline: false}
  API->>API: validate parameters (422 if invalid)
  API->>DB: read Touchpoint, Order, Opportunity, ExceptionLog
  API->>Att: run_attribution(params, run_id=RUN-2026-000001)
  Att-->>API: results + unattributed + audit
  API->>DB: INSERT AttributionRun, AttributionResult, ... (append)
  API->>Ana: reconciliation(...)
  API-->>An: 201 run summary, reconciliation_passed
  An->>API: GET /roi/campaigns?run_id=RUN-2026-000001&model=time_decay
  API->>Ana: campaign_roi (valid spend, quarantine, missing cost)
  Ana-->>An: ROI rows with roi_status
```

### 4.3 Advance an opportunity stage (sales operations)

```mermaid
sequenceDiagram
  actor Ops as Sales ops
  participant API as FastAPI
  participant V as opportunity_current_stage (view)
  participant E as OpportunityEvent (append-only)
  Ops->>API: POST /opportunities/OPP-…/events {to_stage: proposal}
  API->>V: current stage, last event time, count
  alt transition not allowed
    API-->>Ops: 409 invalid_transition (allowed list)
  else event older than last event
    API-->>Ops: 422 out_of_order_event
  else valid
    API->>E: INSERT event (sequence n+1)
    API->>V: re-read current stage
    API-->>Ops: 201 appended + current stage
  end
  Note over API,E: Opportunity.stage snapshot is never updated;<br/>revenue only comes from Order records.
```

### 4.4 Investigate an exception (support agent)

```mermaid
sequenceDiagram
  actor Sup as Support agent
  participant API as FastAPI
  participant DB as SQLite store
  Sup->>API: GET /exceptions?issue_type=anonymous_session_without_evidence
  API->>DB: ExceptionLog
  API-->>Sup: EXC-… R-IDN-01 LEAD-… reject_identity_link
  Sup->>API: GET /leads/LEAD-…
  API->>DB: Lead + WebSession + ExceptionLog
  API-->>Sup: session evidence = none, identity_link = rejected
  Sup->>API: GET /contacts/CON-…/journey
  API-->>Sup: chronological touches, sessions, leads, events, orders
```

### 4.5 Prove reproducibility (administrator)

```mermaid
sequenceDiagram
  actor Admin
  participant CLI as verify-reproducibility
  participant P as run_pipeline
  Admin->>CLI: --scale full --seed 20261022
  CLI->>P: run 1 → row counts + SHA-256 per table
  CLI->>P: run 2 → row counts + SHA-256 per table
  CLI->>CLI: compare 23 tables
  CLI-->>Admin: PASS / FAIL + reports/reproducibility_report.json
```

## 5. Production-hardening notes

This is a synthetic prototype. A production version would add timezone-aware timestamps, a governed FX source, consent enforcement before attribution, authentication and role-based access on the API (the write endpoints are currently open), a server database such as PostgreSQL, incremental or streaming processing, and data-driven models (Markov or Shapley) alongside the rule-based ones.
