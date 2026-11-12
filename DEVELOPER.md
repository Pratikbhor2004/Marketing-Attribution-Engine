# Developer guide

How to set up, run, test and change the Marketing Attribution Engine. For *what* the product does, see the
[README](README.md) and the [feature guide](docs/feature_guide.md).

**Contents:**
1. [Prerequisites](#1-prerequisites)
2. [First-time setup](#2-first-time-setup)
3. [Run every component](#3-run-every-component)
4. [Configuration](#4-configuration)
5. [Where things live](#5-where-things-live)
6. [Development loop](#6-development-loop)
7. [Common changes, step by step](#7-common-changes-step-by-step)
8. [Generated files: what to regenerate when](#8-generated-files-what-to-regenerate-when)
9. [Debugging tips](#9-debugging-tips)
10. [Troubleshooting](#10-troubleshooting)
11. [Git conventions](#11-git-conventions)

---

## 1. Prerequisites

| Tool | Version | Needed for |
|---|---|---|
| Python | 3.10+ (developed on 3.14, Docker image uses 3.12) | everything |
| pip | recent | installing dependencies |
| Git | any | version control |
| Docker Desktop | optional | containerised run (`docker compose up --build`) |
| Postman | optional | manual API exploration (a Python runner is included, so it's not required) |

There's no database server to install: the store is a single SQLite file, and nothing calls the internet at runtime.

---

## 2. First-time setup

**Windows (PowerShell)**

```powershell
git clone <repo-url> marketing-attribution-engine
cd marketing-attribution-engine
python -m venv .venv
.venv\Scripts\Activate.ps1          # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -r requirements.txt
pip install -e .                    # optional: adds the `attribution-engine` command
```

**macOS / Linux**

```bash
git clone <repo-url> marketing-attribution-engine
cd marketing-attribution-engine
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .                    # optional
```

**Check the setup** (about 20 seconds; the tests generate their own starter dataset in a temp folder):

```bash
python -m pytest -q                 # expect: 26 passed
```

---

## 3. Run every component

Every command below runs from the repository root with the virtual environment active.

### 3.1 Generate the data (always first)

The API and dashboard read the data from `data/generated/`, so generate it before starting either.

```bash
python -m attribution_engine.cli generate --scale starter                   # ~3 s, 40 campaigns / 1k contacts
python -m attribution_engine.cli generate --scale full --seed 20261022      # ~45 s, 500 / 50k / 200k / 10k, ~450 MB
```

| Flag | Default | Meaning |
|---|---|---|
| `--scale` | `starter` | `starter` or `full`; both come from the same generator |
| `--seed` | `20261022` | same seed → byte-identical data |
| `--output` | `data/generated` | where to write the exports (ground truth goes to the sibling `ground_truth/`) |
| `--formats` | `csv,json,sqlite,sql` | limit the export formats (the API and dashboard need `sqlite`) |

A run prints a JSON summary and **exits with code 1 if any validation check fails**.

Output:

```text
data/generated/    attribution.db (SQLite, PK/FK), <table>.csv, <table>.json, seed_data.sql,
                   source_manifest.json, validation_summary.json, evaluation_summary.json
data/ground_truth/ ground_truth.csv, channel_contribution.csv   (labels, never read by the engine)
```

### 3.2 API (FastAPI)

```bash
uvicorn attribution_engine.api:app --reload                  # http://127.0.0.1:8000
uvicorn attribution_engine.api:app --reload --port 8010      # if 8000 is taken
```

| URL | What |
|---|---|
| http://127.0.0.1:8000/docs | Swagger UI. All 58 operations have descriptions, examples and "Try it out" |
| http://127.0.0.1:8000/redoc | ReDoc |
| http://127.0.0.1:8000/health | liveness, shows whether a dataset is loaded |
| http://127.0.0.1:8000/openapi.json | live OpenAPI spec |

`--reload` restarts the server when you change a `.py` file. The API caches tables in memory and drops the cache automatically
when `attribution.db` changes, so a regenerate never needs a restart.

### 3.3 Dashboard (Streamlit)

Run this in a second terminal (activate `.venv` there too):

```bash
streamlit run dashboard/app.py                     # http://localhost:8501
```

It has KPI cards and seven tabs: Model comparison, Campaign ROI, Channel ROI, Exceptions, Identity gaps, Explain a conversion, and Evaluation.

### 3.4 CLI checks

```bash
python -m attribution_engine.cli validate                                           # re-run the 111 checks on data/generated/attribution.db
python -m attribution_engine.cli validate --db path/to/attribution.db
python -m attribution_engine.cli verify-reproducibility --scale full --seed 20261022  # writes reports/reproducibility_report.json
```

### 3.5 Postman collection

```bash
python -m scripts.run_postman_collection                                   # all 70 requests in-process (no server, no Node)
python -m scripts.run_postman_collection --base-url http://localhost:8000  # against a running server
```

Or, in the Postman app, import both files from `postman/`, select the *Marketing Attribution Engine* environment and run the collection.
Run it top to bottom: list requests save IDs that later requests use, and the *Pipeline* folder runs last because it regenerates the data.

### 3.6 Docker

```bash
docker compose up --build                                   # API :8000 + dashboard :8501, starter data in a volume
$env:DATA_SCALE="full"; docker compose up --build           # PowerShell, full scale
docker compose run --rm api test                            # test suite inside the container
docker compose down -v                                      # stop and delete the data volume
```

---

## 4. Configuration

| Setting | Where | Default |
|---|---|---|
| `ATTRIBUTION_DATA_DIR` | env var, read by the API and dashboard | `data/generated` (ground truth is read from `../ground_truth`) |
| `DATA_SCALE`, `DATA_SEED` | env vars, Docker only | `starter`, `20261022` |
| Volumes, dates, anomaly rates | `DatasetConfig` in [attribution_engine/config.py](attribution_engine/config.py) | full: 500 / 50k / 200k / 10k; data as-of 2026-10-31; train cutoff 2026-07-01 |
| Attribution parameters | `AttributionParams` in the same file | lookback 365 d, half-life 14 d, position 0.4 / 0.4, offline included |

To try different attribution parameters **without regenerating**, use `POST /api/v1/attribution/runs`. It stores a new run
(`RUN-2026-000001`, ...) next to `RUN-BASELINE`.

---

## 5. Where things live

```text
attribution_engine/
  config.py          DatasetConfig (volumes, seed, dates, anomaly rates), AttributionParams (+ validate)
  generator.py       master data → funnel → touchpoints/sessions → rule-derived outcomes → anomalies + ground truth
  detection.py       RULES table + one detector per exception type; evaluate_detection (scoring vs ground truth)
  attribution.py     eligibility E1–E6, model weights, integer-cent allocation (largest remainder)
  analytics.py       cost ledger, campaign/channel ROI, reconciliation, model comparison, chronological split, identity gaps
  validation.py      111 checks; PRIMARY_KEYS / FOREIGN_KEYS / DERIVED_OUTPUTS
  pipeline.py        run_pipeline, SQLite DDL (PK/FK, indexes, opportunity_current_stage view), export, digests
  schema.py          data dictionary spec → docs/data_dictionary.md|json
  api.py             FastAPI app (58 operations)
  openapi_docs.py    Swagger descriptions, parameter docs, request examples, error responses
  openapi_examples.json  real example responses (generated, see §8)
  cli.py             generate · validate · verify-reproducibility
dashboard/app.py     Streamlit dashboard (reads the SQLite store)
scripts/             build_postman_collection · run_postman_collection · export_openapi · verify_reproducibility
tests/               conftest (session-scoped starter dataset) · test_generator · test_engine_rules · test_api
docs/                feature guide, architecture + diagrams, rules, data dictionary, API reference, openapi.*, swagger.html
reports/             full-scale validation / evaluation / manifest / reproducibility reports
postman/             collection + environment (generated)
docker/, Dockerfile, docker-compose.yml
```

**Data flow:**
`cli.generate` → `pipeline.run_pipeline` → `generator.generate_dataset` → `detection.detect_exceptions`
→ `attribution.run_attribution` → `analytics.*` → `validation.validate_dataset` → `pipeline.export`.
The API and dashboard only **read** the exported SQLite store. The API's only writes are appended opportunity events and new attribution runs.

**Design rules to keep:**
* **Labels never enter the engine.** No `issue_type` in input tables; detectors use only source tables. A test enforces this.
* **Derived values are computed, never randomly assigned.** Outcomes, lead status, lifecycle, ROI and credit all come from rules.
* **Append-only history.** Never `UPDATE` an `OpportunityEvent` or an existing run; add rows.
* **Money in integer cents.** Credit per opportunity must add up exactly to recognised revenue (reconciliation test).
* **Determinism.** All randomness goes through the single `numpy.random.Generator` seeded from the config. Don't use `random` or unseeded numpy.
* **pandas 3:** datetime units are no longer always `ns`. Pin with `.as_unit("ns")` when doing arithmetic on timestamps.

---

## 6. Development loop

```bash
python -m pytest -q                                  # full suite (~20 s)
python -m pytest -q tests/test_engine_rules.py       # one file
python -m pytest -q -k reconcile                     # by name
python -m pytest -q -x --lf                          # stop at first failure, rerun last failures
```

The tests build **one starter dataset per session** (`tests/conftest.py`) and export it to a temp directory. They never touch
`data/generated/`, so you can run them while the API is serving your own data.

What the suites cover:

| File | Covers |
|---|---|
| `test_generator.py` | volumes, ID formats, reproducibility, no label leakage, edge-case coverage, rule-derived outcomes, data dictionary completeness |
| `test_engine_rules.py` | detection = ground truth, reconciliation, eligibility rules, model behaviour, ROI statuses, validation catches corruption |
| `test_api.py` | append-only lifecycle (409/422), custom runs, Postman covers every route and runs green, Swagger documents every operation, exported spec matches code |

Before you commit, run `python -m pytest -q` and, for engine changes, also do a full-scale run:
`python -m attribution_engine.cli generate --scale full` (it must exit 0).

---

## 7. Common changes, step by step

### Add or change an API endpoint
1. Add the route in [attribution_engine/api.py](attribution_engine/api.py) with `tags=[...]` and a `summary=...`.
2. Add its entry in `OPS` in [attribution_engine/openapi_docs.py](attribution_engine/openapi_docs.py) (description and error responses). Add any
   new query/path parameter to `PARAMS` and any POST body example to `BODY_EXAMPLES`.
3. If it needs a path ID the capture script doesn't know, add it to `ids` in `scripts/export_openapi.py`.
4. Regenerate: `python -m scripts.export_openapi` and `python -m scripts.build_postman_collection`.
5. `python -m pytest -q`. The tests fail if the endpoint is missing from Postman or undocumented in Swagger.

### Add an attribution model
1. Add the name to `MODELS` in [attribution_engine/attribution.py](attribution_engine/attribution.py) and its per-touch weight to the `weights` dict in `run_attribution`
   (eligible touches are in `e`, with `pos`, `n` and the age in days available).
2. Add it to `ModelName` and `MODEL_DOCS` in `api.py`, and to the `model` description in `openapi_docs.PARAMS`.
3. Document the formula in [docs/attribution_rules.md](docs/attribution_rules.md) §3.
4. Regenerate the data, OpenAPI docs and Postman collection, then run the tests. Reconciliation must still pass, because cent allocation is shared.

### Add an exception rule
1. Add `issue_type: (rule_id, rule text, decision)` to `RULES` in [attribution_engine/detection.py](attribution_engine/detection.py) and write a detector
   `detect_<issue>(tables) -> list[dict]` that reads **only input tables**. Register it in `DETECTORS`.
2. Inject labelled examples in `generator._inject_anomalies` and write them to the ground truth (with the minimum count from `DatasetConfig`).
3. Apply the decision where it matters (attribution, ROI or identity).
4. Update `PARAMS["issue_type"]` in `openapi_docs.py` and [docs/attribution_rules.md](docs/attribution_rules.md) §5.
5. `test_detection_matches_ground_truth_exactly` must stay at F1 = 1.0.

### Add a column or table
1. Produce it in `generator.py` (or `pipeline.build_derived` for a derived table).
2. Add it to `ENTITIES` in [attribution_engine/schema.py](attribution_engine/schema.py). `test_every_exported_column_is_in_the_data_dictionary` fails until you do.
3. For a new table: add the DDL (PK/FK) in `pipeline.write_sqlite` and its keys to `validation.PRIMARY_KEYS` / `FOREIGN_KEYS`.
4. Regenerate the dictionary: `python -m attribution_engine.schema`.

### Change volumes or dates
Edit `DatasetConfig` (full) or `DatasetConfig.starter()` in `config.py`, then regenerate and run the tests. Re-run
`verify-reproducibility` and update `reports/` if you publish new full-scale numbers.

---

## 8. Generated files: what to regenerate when

These files are produced by scripts and committed. **Don't hand-edit them**; rerun the command instead.

| File(s) | Regenerate with | When |
|---|---|---|
| `docs/openapi.json`, `docs/openapi.yaml`, `docs/swagger.html`, `docs/api_reference.md`, `attribution_engine/openapi_examples.json` | `python -m scripts.export_openapi` (`--no-capture` reuses the saved examples) | API routes, parameters, models or `openapi_docs.py` changed |
| `postman/*.json` | `python -m scripts.build_postman_collection` | any API change |
| `docs/data_dictionary.md`, `docs/data_dictionary.json` | `python -m attribution_engine.schema` | `schema.py` changed |
| `reports/reproducibility_report.json` | `python -m attribution_engine.cli verify-reproducibility --scale full` | generator changed |
| `reports/full_run_*.json` | `python -m attribution_engine.cli generate --scale full`, then copy the summaries from `data/generated/` | generator / engine changed |

`data/generated/` and `data/ground_truth/` are **git-ignored**. Never commit generated datasets.

---

## 9. Debugging tips

* **Inspect the store:** `python -c "import sqlite3; c=sqlite3.connect('data/generated/attribution.db'); print(c.execute('select count(*) from touchpoint').fetchone())"`,
  or open `attribution.db` in *DB Browser for SQLite* or a VS Code SQLite extension. The live stage is in the view `opportunity_current_stage`.
* **Run the pipeline in a REPL without writing files:**
  ```python
  from attribution_engine.config import DatasetConfig
  from attribution_engine.pipeline import run_pipeline
  r = run_pipeline(DatasetConfig.starter())
  r.tables["Touchpoint"].head(); r.validation["failed_checks"]; r.evaluation["detection_vs_ground_truth"]
  ```
* **Why did a campaign get credit?** `GET /api/v1/attribution/opportunities/{id}` lists every touch with its eligibility verdict.
* **Why is a lead unlinked?** `GET /api/v1/leads/{id}` (shows `identity_link`) and `GET /api/v1/contacts/{id}/journey`.
* **A validation check failed:** `GET /api/v1/pipeline/validation?only_failed=true` or `data/generated/validation_summary.json` → `failed_checks`.
* **Debug the API in VS Code:** create a launch config with module `uvicorn` and args `attribution_engine.api:app --port 8000`, then set breakpoints in `api.py`.

---

## 10. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `404 Dataset not found` from every endpoint | No `attribution.db` in `ATTRIBUTION_DATA_DIR`. Run `python -m attribution_engine.cli generate --scale starter` from the repo root, or check the env var. |
| `Activate.ps1 cannot be loaded` | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, then activate again. |
| `ModuleNotFoundError: attribution_engine` | Run commands from the repo root, or `pip install -e .`. |
| `[Errno 10048]` / address already in use | Another server is on port 8000 (or 8501). Use `--port 8010`, or stop it: `netstat -ano \| findstr :8000` then `taskkill /PID <pid> /F`. |
| `PermissionError` / `database is locked` while regenerating (Windows) | The API, dashboard or a DB viewer holds `attribution.db` open. Stop them, regenerate, start again. |
| Dates show as 1970 after a change | A pandas 3 datetime unit issue. Pin with `.as_unit("ns")` before converting to integers. |
| `generate` exits with code 1 | A validation check failed. Read `failed_checks` in the printed summary. |
| Tests fail with "missing from the Postman collection" / "no description" | You added or changed an endpoint. Follow §7 *Add or change an API endpoint* and regenerate. |
| `docs/openapi.yaml` not written | Install PyYAML: `pip install pyyaml`. |
| Docker dashboard waits forever | The API container is still generating (full scale takes minutes). Watch it with `docker compose logs -f api`. |

---

## 11. Git conventions

* One commit per logical step, with an imperative subject (e.g. `Add time-decay half-life parameter to API`) and an optional body.
* No author or co-author lines in commit messages.
* Keep generated files in sync in the same commit as the change that caused them (see §8).
* `main` must always pass `python -m pytest -q`.
