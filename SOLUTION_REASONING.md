# Solution reasoning

The problem, my assumptions, the key decisions and their trade-offs, how it was tested, what it can't do, and the
evidence that it works. Every number below comes from the committed full-scale run (`reports/`, seed 20261022) and can be
reproduced with the commands in §7.

---

## 1. The problem

A company spends money on campaigns across eight channels, and buyers touch several campaigns before a deal closes.
The business wants to know **which campaigns earned the revenue and which paid for themselves**, and the answer depends
on the attribution rule chosen. Real marketing data makes this harder: duplicate invoices, platforms claiming credit for
clicks after the sale, spend booked in the wrong currency, campaigns with no cost records, and anonymous web visits
stitched to people without evidence.

So the real deliverable isn't "a number per campaign". It is a system where:
1. every number can be **traced** back to source records,
2. bad data is **detected and handled by explicit rules**, not silently absorbed,
3. different attribution models can be **compared fairly**, and
4. we can **measure** which model is closest to the truth and whether it holds up over time.

Measuring that last point is impossible with real data, because nobody knows the true effect of a channel. That is why the
brief asks for synthetic data whose generating rule *is* known.

## 2. Assumptions

| Assumption | Why | Where enforced |
|---|---|---|
| A conversion is a **closed-won opportunity**; its time is `closed_at` | A form fill is a lead, not revenue; sales closes the deal | `attribution.recognized_revenue` |
| **Revenue comes only from `Order` (billing) records**, not from the deal amount or a stage change | Finance trusts billing; a CRM stage can be wrong | Same; `POST …/events` says so in its response |
| Credit goes only to touches by the **same contact**, inside a **365-day lookback**, **after the contact's previous conversion** | No credit for strangers, for ancient history, or for touches already credited to an earlier deal | Eligibility rules E1–E6 |
| Report in **USD** at a **monthly FX rate**; keep native amounts too | Campaigns run in INR, USD and EUR; ROI must be comparable | `FxRate`, `*_usd` columns |
| Wrong-currency spend is **quarantined, not converted** | Converting would hide a booking error inside ROI | Rule R-CST-01 |
| Missing cost means **ROI unavailable**, not ROI against zero spend | Zero spend gives infinite ROI and makes the worst-documented campaign look best | Rule R-CST-02, `roi_status` |
| The data is as of **2026-10-31**; train/evaluation cutoff **2026-07-01** | Leaves enough closed deals in both periods | `DatasetConfig` |
| Timestamps are timezone-naive | Synthetic data in one reporting zone keeps the logic readable | Listed as a limitation |

**Reading of the brief.** The brief asks to "keep an issue_type/ground_truth label" on anomalies, and also to "never leak
[ground truth] into input features". I resolved this in favour of *no leakage*. Labels live only in
`data/ground_truth/`, the engine has to find anomalies by rule, and the labels are used only to score it. A test enforces that
no input table contains a label column.

## 3. Key decisions and why

### 3.1 Detect exceptions by rule, score against ground truth
* **Decision:** five detectors (R-REV-01, R-TCH-01, R-CST-01, R-CST-02, R-IDN-01) that read only input tables.
* **Why:** the earlier version filtered on the planted `issue_type` column, so it "found" anomalies by reading the answer key. A production system never has labels.
* **Alternative rejected:** an ML anomaly model. Its decisions would be opaque, and the brief wants explicit, explainable decisions.
* **Result:** 105 of 105 planted anomalies found, 0 false positives.

### 3.2 Outcomes come from a documented rule
* **Decision:** a closed deal wins with probability `σ(−1 + Σ β_channel × min(touches, 3) + size_effect)`.
* **Why:** the brief forbids independent random outcomes. It also gives each channel a known true contribution, which lets us score attribution models. Without it, comparing models is just opinion.
* **Check:** won deals have more pre-close exposure than lost ones (test `test_outcomes_follow_rule_not_coin_flip`).

### 3.3 Money is split in integer cents
* **Decision:** each model produces weights; revenue is split in cents with the largest-remainder method.
* **Why:** float splits lose or create fractions of a cent, and with 5,000+ deals × 5 models that drift breaks reconciliation. Integer cents make it exact.
* **Result:** for every currency and model, `gross = duplicates + attributed + unattributed` with a difference of **0.00**.

### 3.4 One shared eligibility layer for all five models
* **Decision:** models differ *only* in how they weight eligible touches. Eligibility (E1–E6) is shared and audited per deal (`TouchEligibilityAudit`).
* **Why:** if each model filtered touches differently, a difference between models could be a filtering artefact rather than a modelling one. Sharing the layer makes the comparison fair. All models credit the same total; only the split differs.

### 3.5 Append-only history
* **Decision:** stage changes are `OpportunityEvent` rows, and the live stage is a SQL view (`opportunity_current_stage`). New attribution runs are new rows keyed by `run_id`. Nothing is updated in place.
* **Why:** the brief requires it, and it gives an audit trail: you can always answer "what did we believe on date X?".
* **Enforced:** the API rejects invalid transitions (409) and back-dated events (422).

### 3.6 SQLite with real constraints, plus flat exports
* **Decision:** one SQLite file with PRIMARY KEY and FOREIGN KEY constraints and indexes, plus CSV, JSON and a SQL dump.
* **Why:** no server to install, so reviewers can run it in minutes. The FKs make the database itself reject a broken join. The flat files cover the brief's export requirement.
* **Trade-off:** single-writer, so it isn't for concurrent production use (see §6).

### 3.7 Docs and Postman generated from code
* **Decision:** the Postman collection, OpenAPI/Swagger files and data dictionary are generated by scripts. Tests fail if any endpoint is missing from Postman or undocumented in Swagger, or if any column is missing from the dictionary.
* **Why:** hand-written API docs drift. "All the APIs must be in Postman" is a requirement that should be enforced by a test, not by memory.

### 3.8 One generator, two scales
* **Decision:** `starter` (40 campaigns, seconds) and `full` (500 / 50k / 200k / 10k, about 46 s) differ only in volume parameters, and one seeded `numpy.random.Generator` drives everything.
* **Why:** tests run on starter in about 20 s, while the published results come from full. The behaviour is identical because the code path is identical.

## 4. Trade-offs I accepted

| Choice | Gain | Cost |
|---|---|---|
| Rule-based models (first, last, linear, U-shaped, time-decay) | Transparent; every credit can be explained per touch | Not causal; §5 shows all of them miss the truth by a similar margin |
| Anomalies added as **new rows** rather than corrupting existing ones | Clean baseline; ground truth is exact | 200,019 touchpoints instead of exactly 200,000 (19 injected extras, documented) |
| Synthetic FX rates | Fully reproducible, no licensing questions | Not market rates |
| Quarantine wrong-currency cost instead of converting it | Errors stay visible | Those campaigns' ROI is understated until finance fixes the booking (`roi_status` says so) |
| In-memory background jobs in the API | Simple, no queue to deploy | Jobs are lost on restart |
| No API authentication | Easy to review and demo | Not deployable as is |

## 5. What the results say

**Data quality.** All 105 planted anomalies were detected with no false positives: 30 rejected identity links, 25 currency
mismatches, 21 duplicate invoices, 19 post-conversion claims and 10 campaigns with no cost. Money reconciles to the cent in all
three currencies.

**How close is each model to the truth?** The distance between each model's channel revenue mix and the true mix
(total variation distance, where 0 means identical):

| Model | Distance to truth | Rank stability, train → evaluation (Spearman) |
|---|---|---|
| time_decay | **0.252** (closest) | 0.33 |
| position_based | 0.259 | 0.24 |
| linear_multi_touch | 0.259 | 0.30 |
| last_touch | 0.264 | **0.40** |
| first_touch | 0.266 | 0.05 |

**How I read this:**
* **Every rule-based model is about 25% off the true channel mix, and they differ from each other by only about 1.5 points.**
  The model choice matters much less than people argue about. All of them credit touches in proportion to *exposure*, while
  the truth depends on each channel's *effect*:

  | Channel | True share | Credited by models (range) |
  |---|---|---|
  | Events | 27.9% | 14.2–15.5% (under-credited by about half) |
  | Partner | 19.5% | 11.7–12.0% (under) |
  | Display | 2.0% | 11.1–12.0% (over-credited about 6×) |
  | Paid Social | 7.6% | 13.9–15.4% (over) |

  High-volume, low-effect digital channels look better than they are, and offline / partner channels look worse.
* **Rank stability is weak** (Spearman 0.05–0.40 across 24 channel × market segments). A campaign ranking from the first half
  of the year is a poor guide to the second half. **First-touch is the least stable**, so it shouldn't drive budget moves.
* **Recommendation:** use time-decay as the default reporting model (closest to truth). Last-touch is the most stable ranking,
  but no rule-based model is good enough to move budget on its own. The next step is a data-driven model (Markov / Shapley)
  or a holdout experiment, both listed in §6.

This is the most important finding, and it's only possible because the data has a known truth.

**Eligibility audit.** Of 54,729 candidate touches, 39,178 were eligible. The rest were excluded: 10,414 came after the
conversion, 4,943 before the contact's previous conversion, 193 were outside the lookback, and 1 was flagged by R-TCH-01.
Most false platform claims are already excluded by the time rule E2. R-TCH-01 is defence in depth, and it also logs *why*
a claim was rejected.

## 6. Limitations
* **Synthetic only.** Distributions are plausible but not calibrated to a real business. No real PII was used.
* **Correlational models.** Rule-based attribution can't recover causal effect (§5). Markov-chain or Shapley attribution and holdout tests are the obvious next steps.
* **Production gaps:** no authentication on write endpoints, SQLite instead of a server database, batch rather than streaming processing, timezone-naive timestamps, synthetic FX, and no consent enforcement before attribution (the `consent_status` field exists but isn't used to filter).
* **Not verified on this machine:** the Docker build, the Postman desktop app, and a visual review of the dashboard and `swagger.html` (see [AI_USAGE.md](AI_USAGE.md) §5).

## 7. How I know it works
Each claim is backed by an automated check you can re-run:

| Claim | Evidence | Re-run |
|---|---|---|
| Code behaves as specified | 26 tests pass | `python -m pytest -q` |
| Dataset is internally consistent | 111/111 checks: PK, FK, ID formats, enums, date order, lifecycle transitions, arithmetic, duplicates, label isolation, coverage | `python -m attribution_engine.cli generate --scale full` (exits 1 on any failure) |
| Exceptions are found by rule, not labels | Precision = recall = 1.0 against the separate ground truth; test proves detectors read only input tables | `GET /api/v1/evaluation/detection` |
| No money is created or lost | Reconciliation difference 0.00 for every currency × model | `GET /api/v1/roi/reconciliation` |
| Temporal windows respected | No credited touch after conversion or outside the lookback (tests and audit table) | `GET /api/v1/attribution/opportunities/{id}` |
| Missing identity is visible | 6,000 anonymous sessions, 5,201 leads without a session, 30 rejected links, unattributed revenue with reasons | `GET /api/v1/identity/gaps` |
| Reproducible | Two regenerations match on all 23 tables (row counts and SHA-256) | `python -m attribution_engine.cli verify-reproducibility --scale full` |
| API is complete and documented | 58 operations; Postman 70/70 over HTTP; Swagger spec validated; tests fail on any gap | `python -m scripts.run_postman_collection` |

The tests are also designed to **fail when something is broken**: `test_validation_passes_and_catches_corruption`
deliberately corrupts the data and asserts that validation catches it.

## 8. Walkthrough plan (about 10 minutes)
1. **The problem (1 min):** the Priya example. Four teams claim one $10,000 deal, the invoice is counted twice, and the expo spend is booked in euros.
2. **Generate and validate (1 min):** `generate --scale starter`, then show the JSON summary: 111 checks passed, detection F1 = 1.0.
3. **Data quality (2 min):** `/docs` → `GET /exceptions/summary` → open one duplicate invoice → `GET /contacts/{id}/journey`.
4. **Attribution (2 min):** `GET /attribution/opportunities/{id}` shows each touch with its eligibility reason and credit per model, then `GET /attribution/compare`.
5. **ROI (1 min):** `GET /roi/campaigns?model=time_decay`, pointing at `unavailable_missing_cost`, then `GET /roi/reconciliation` with differences of 0.00.
6. **What-if and history (1 min):** `POST /attribution/runs` with a 90-day lookback; `POST /opportunities/{id}/events` with an invalid jump (409).
7. **The honest result (1 min):** the table in §5: every model is about 25% off the truth, rankings are unstable, and the next step is a data-driven model.
8. **Engineering (1 min):** the test suite, generated docs/Postman with coverage tests, the reproducibility proof, and Docker.

## 9. Questions reviewers may ask

**Why not just use last-touch like most tools?**
It is the most stable ranking here (Spearman 0.40), but its channel mix is about as far from the truth as first-touch (0.264 vs 0.266).
Like every rule-based model, it credits Events at about 14% when its true share is 28%, so it would push budget away from the most effective channel.

**How do you know detection isn't overfitted to your own injected anomalies?**
That's a fair challenge. The detectors encode business rules (repeated invoice ID, revenue above the deal amount, currency
≠ contract currency, and so on), not properties of the injection code. But the anomalies were designed alongside them, so F1 = 1.0 shows the rules are
*implemented* correctly, not that they're *complete*. On real data I'd expect new anomaly classes and would add rules as they appear.

**Why is unattributed revenue about 2% and not zero?**
Some won deals have no eligible touch (for example, all their touches are outside the lookback or before the previous
conversion). They are reported with a reason rather than forced onto a campaign.

**What happens at 10× scale?**
Generation and attribution are vectorised pandas (about 46 s at full scale). At 10× I'd move storage to PostgreSQL or DuckDB and
compute attribution incrementally per closed deal instead of as a full batch.

**What would you change first for production?**
Authentication on the write endpoints, consent filtering before attribution, a real FX source, timezone-aware timestamps,
and a data-driven attribution model validated with a holdout experiment.
