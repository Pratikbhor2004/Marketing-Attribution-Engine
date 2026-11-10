# Marketing Attribution Engine

A reproducible synthetic-data and campaign-attribution project for comparing first-touch, last-touch and linear multi-touch attribution, reconciling campaign spend/revenue, and exposing known data-quality exceptions.

**No real personal or confidential data is used.** The generator creates synthetic IDs and synthetic records only.

## Feature

- Generates the requested full baseline scale: 500 campaigns, 50,000 contacts, 200,000 touchpoints and 10,000 opportunities.
- Generates the related Channel, Creative, Audience, Contact, WebSession, Lead, Opportunity, Order and CostRecord entities.
- Uses deterministic, zero-padded IDs and a fixed seed (`20261022` by default).
- Exports CSV, JSON, SQLite database and standalone SQL seed data.
- Implements first-touch, last-touch and linear multi-touch attribution.
- Calculates campaign ROI without mixing currencies or treating missing cost as zero.
- Injects five labelled exception scenarios and writes separate ground-truth CSV/JSON.
- Includes field-level data dictionary, append-only opportunity lifecycle events, relationship/date/revenue validation, validation report, source manifest with SHA-256 hashes, API, Streamlit dashboard and tests.

## Quick start

Python 3.10+ is recommended.

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
```

Generate a quick starter dataset:

```bash
python -m attribution_engine.cli --scale starter
```

Generate the full assessment dataset:

```bash
python -m attribution_engine.cli --scale full --seed 20261022
```

The full dataset can take several minutes and requires more memory and disk space than starter mode. Use starter mode while developing.

## Outputs

A verified full-scale run report is included under `reports/`. Generated CSV/JSON/SQL/database files are intentionally not bundled in this source ZIP or expected to be committed to GitHub; regenerate them locally with the command below.

- `data/generated/*.csv` — source entities and derived attribution/ROI results
- `data/generated/*.json` — JSON equivalents
- `data/generated/attribution.db` — SQLite seed database
- `data/generated/seed_data.sql` — portable SQL schema/data dump
- `data/generated/opportunityevent.csv` and `.json` — append-only lifecycle event history
- `data/generated/validation_summary.json` — validation results and row counts
- `data/generated/source_manifest.json` — config and SHA-256 checksums
- `data/generated/evaluation_summary.json` — key evaluation metrics
- `data/ground_truth/ground_truth.csv` and `.json` — known anomaly labels, separate from model inputs

The generator also creates `campaign_roi.csv` and `evaluation_summary.json`. The source manifest is refreshed after all CLI-level outputs are written and includes SHA-256 hashes for generated CSV/JSON/SQL/database deliverables and the separate ground-truth files.

## Run tests

```bash
pytest -q
python -m scripts.verify_reproducibility --scale full --seed 20261022
```

The reproducibility command generates the selected scale twice and compares per-table row counts and stable checksums. Use `--scale starter` for a quick check.

## Run the API

Generate data first, then:

```bash
uvicorn attribution_engine.api:app --reload
```

Open `http://127.0.0.1:8000/docs`.

Endpoints:
- `GET /health`
- `GET /campaigns?limit=100`
- `GET /roi?model=linear_multi_touch&limit=100`
- `GET /exceptions`

## Run the dashboard

```bash
streamlit run dashboard/app.py
```

## Project layout

```text
attribution_engine/  generator, validation, analytics, CLI and API
dashboard/           Streamlit analytics dashboard
docs/                data dictionary, architecture and attribution rules
scripts/             reproducibility verification
tests/               automated tests
data/generated/      generated input/output tables (not committed)
data/ground_truth/   separate anomaly labels (not committed)
```

## Business rules

- First-touch credits all opportunity revenue to the earliest eligible touchpoint.
- Last-touch credits all revenue to the latest eligible touchpoint before conversion.
- Linear multi-touch splits revenue equally across eligible touchpoint events.
- Touches after conversion are excluded.
- Duplicate invoice IDs are quarantined and excluded from recognized Order revenue and attribution.
- Currency mismatch cost rows are quarantined; attributed revenue in a non-reporting currency is surfaced separately and excluded from ROI unless an explicit FX rate is supplied.
- Campaigns with missing/invalid spend have unavailable ROI, not zero-cost ROI. ROI also reports when foreign-currency revenue is excluded.
- Anonymous sessions without identity evidence are not treated as proven contact identity.

See `docs/attribution_rules.md` and `docs/data_dictionary.md` for detailed definitions.

## Reproducibility

The same seed and configuration produce identical source tables, including lifecycle history. The manifest records the configuration and checksums. Verify using:

```bash
python scripts/verify_reproducibility.py
```

For a formal full-scale two-run check, run `python -m scripts.verify_reproducibility --scale full --seed 20261022`.

## Data provenance and limitations

All data is synthetically generated by this repository. No third-party dataset, scraping, private information, or external data source is used. The implementation is a practical assessment prototype. See `docs/architecture.md` for production-hardening recommendations.

## Submission checklist

- [ ] Run full generator successfully.
- [ ] Confirm `validation_summary.json` reports `passed: true`.
- [ ] Run `pytest -q`.
- [ ] Run `python -m scripts.verify_reproducibility --scale full --seed 20261022`.
- [ ] Inspect dashboard and API.
- [ ] Keep the repository public for reviewer access.
- [ ] Do not upload the assessment prompt or confidential assessment material.
- [ ] Upload generated datasets only if the repository size is reasonable; the code regenerates them from the fixed seed.
