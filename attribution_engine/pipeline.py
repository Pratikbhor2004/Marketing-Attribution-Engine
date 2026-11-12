"""End-to-end pipeline: generate -> detect -> attribute -> analyse -> validate -> export.

Also provides the SQLite persistence layer used by the API (schema with real PK/FK
constraints) and the reproducibility digest used by scripts/verify_reproducibility.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import sqlite3

import pandas as pd

from . import __version__
from .analytics import campaign_roi, channel_roi, evaluation_summary
from .attribution import MODELS, AttributionOutput, run_attribution
from .config import AttributionParams, DatasetConfig
from .detection import detect_exceptions
from .generator import GeneratedDataset, generate_dataset
from .validation import FOREIGN_KEYS, PRIMARY_KEYS, validate_dataset

SOURCE_TABLES = ["Channel", "FxRate", "Campaign", "Creative", "Audience", "CampaignAudience", "Contact", "WebSession",
                 "Lead", "Opportunity", "OpportunityEvent", "Touchpoint", "Order", "CostRecord"]
DERIVED_TABLES = ["ExceptionLog", "AttributionRun", "AttributionResult", "UnattributedRevenue", "TouchEligibilityAudit",
                  "CampaignROI", "ChannelROI"]
COMPOSITE_KEYS = {"CampaignROI": ["campaign_id", "model"], "ChannelROI": ["channel_id", "model"],
                  "UnattributedRevenue": ["run_id", "opportunity_id"], "TouchEligibilityAudit": ["run_id", "opportunity_id"]}
EXTRA_PK = {"AttributionRun": "run_id"}


@dataclass
class PipelineResult:
    dataset: GeneratedDataset
    exceptions: pd.DataFrame
    attribution: AttributionOutput
    tables: dict[str, pd.DataFrame]        # source + derived
    validation: dict
    evaluation: dict


def attribution_run_record(run_id: str, params: dict, output: AttributionOutput, triggered_by: str) -> pd.DataFrame:
    return pd.DataFrame([{
        "run_id": run_id, "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") if run_id != "RUN-BASELINE" else "baseline",
        "triggered_by": triggered_by, "models": ",".join(params["models"]), "lookback_days": params["lookback_days"],
        "time_decay_half_life_days": params["time_decay_half_life_days"], "position_first_weight": params["position_first_weight"],
        "position_last_weight": params["position_last_weight"], "include_offline": bool(params["include_offline"]),
        "result_rows": int(len(output.results)), "attributed_opportunities": int(output.results["opportunity_id"].nunique()),
        "unattributed_opportunities": int(len(output.unattributed)),
        "attributed_revenue_usd": round(float(output.results.loc[output.results["model"] == params["models"][0], "credited_revenue_usd"].sum()), 2),
    }])


def build_derived(tables, exceptions, output: AttributionOutput, run_id: str = "RUN-BASELINE", triggered_by: str = "pipeline") -> dict[str, pd.DataFrame]:
    roi = pd.concat([campaign_roi(tables, exceptions, output.results, m) for m in output.params["models"]], ignore_index=True)
    ch = pd.concat([channel_roi(roi[roi["model"] == m], tables["Channel"]).assign(model=m) for m in output.params["models"]], ignore_index=True)
    un = output.unattributed.copy(); un.insert(0, "run_id", run_id)
    audit = output.touch_audit.copy(); audit.insert(0, "run_id", run_id)
    return {"ExceptionLog": exceptions, "AttributionRun": attribution_run_record(run_id, output.params, output, triggered_by),
            "AttributionResult": output.results, "UnattributedRevenue": un, "TouchEligibilityAudit": audit,
            "CampaignROI": roi, "ChannelROI": ch}


def run_pipeline(cfg: DatasetConfig) -> PipelineResult:
    ds = generate_dataset(cfg)
    exceptions = detect_exceptions(ds.tables)
    output = run_attribution(ds.tables, exceptions, AttributionParams.from_config(cfg))
    tables = dict(ds.tables) | build_derived(ds.tables, exceptions, output)
    validation = validate_dataset(tables, exceptions, ds.ground_truth["ground_truth"])
    evaluation = evaluation_summary(ds.tables, exceptions, output, ds.ground_truth, cfg.evaluation_train_cutoff)
    return PipelineResult(ds, exceptions, output, tables, validation, evaluation)


# ------------------------------------------------------------------------------ SQLite
def _sql_type(series: pd.Series) -> str:
    if pd.api.types.is_bool_dtype(series):
        return "INTEGER"
    if pd.api.types.is_integer_dtype(series):
        return "INTEGER"
    if pd.api.types.is_float_dtype(series):
        return "REAL"
    return "TEXT"


def _to_sql_values(df: pd.DataFrame) -> list[tuple]:
    clean = df.astype(object).where(pd.notna(df), None)
    rows = []
    for r in clean.itertuples(index=False, name=None):
        rows.append(tuple(int(v) if isinstance(v, bool) else v for v in r))
    return rows


def create_table(conn: sqlite3.Connection, name: str, df: pd.DataFrame) -> None:
    cols = [f'"{c}" {_sql_type(df[c])}' for c in df.columns]
    if name in PRIMARY_KEYS or name in EXTRA_PK:
        pk = PRIMARY_KEYS.get(name, EXTRA_PK.get(name))
        cols = [c + (" PRIMARY KEY" if c.startswith(f'"{pk}" ') else "") for c in cols]
    elif name in COMPOSITE_KEYS:
        cols.append("PRIMARY KEY (" + ", ".join(f'"{c}"' for c in COMPOSITE_KEYS[name]) + ")")
    for child, col, parent, pcol, _ in FOREIGN_KEYS:
        if child == name and col in df.columns:
            cols.append(f'FOREIGN KEY ("{col}") REFERENCES "{parent.lower()}"("{pcol}")')
    conn.execute(f'CREATE TABLE IF NOT EXISTS "{name.lower()}" ({", ".join(cols)})')


def insert_rows(conn: sqlite3.Connection, name: str, df: pd.DataFrame) -> None:
    if not len(df):
        return
    placeholders = ", ".join("?" for _ in df.columns)
    cols = ", ".join(f'"{c}"' for c in df.columns)
    conn.executemany(f'INSERT INTO "{name.lower()}" ({cols}) VALUES ({placeholders})', _to_sql_values(df))


def write_sqlite(tables: dict[str, pd.DataFrame], db_path: Path) -> None:
    if db_path.exists():
        db_path.unlink()
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        for name in SOURCE_TABLES + DERIVED_TABLES:
            create_table(conn, name, tables[name])
            insert_rows(conn, name, tables[name])
        conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_touch_contact_time ON touchpoint(contact_id, occurred_at);
            CREATE INDEX IF NOT EXISTS idx_touch_campaign ON touchpoint(campaign_id);
            CREATE INDEX IF NOT EXISTS idx_attr_campaign_model ON attributionresult(run_id, model, campaign_id);
            CREATE INDEX IF NOT EXISTS idx_attr_opp ON attributionresult(opportunity_id);
            CREATE INDEX IF NOT EXISTS idx_opp_contact ON opportunity(contact_id);
            CREATE INDEX IF NOT EXISTS idx_event_opp ON opportunityevent(opportunity_id, sequence);
            CREATE INDEX IF NOT EXISTS idx_lead_contact ON lead(contact_id);
            CREATE INDEX IF NOT EXISTS idx_session_contact ON websession(contact_id);
            CREATE INDEX IF NOT EXISTS idx_exception_type ON exceptionlog(issue_type, record_id);
            -- Current state is derived from the append-only event history, never overwritten.
            CREATE VIEW IF NOT EXISTS opportunity_current_stage AS
              SELECT e.opportunity_id, e.to_stage AS current_stage, e.occurred_at AS stage_since, e.sequence AS event_count
              FROM opportunityevent e
              JOIN (SELECT opportunity_id, MAX(sequence) AS s FROM opportunityevent GROUP BY opportunity_id) m
                ON m.opportunity_id = e.opportunity_id AND m.s = e.sequence;
        """)
        fk_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk_violations:
            raise RuntimeError(f"SQLite foreign key violations: {fk_violations[:5]}")


def load_tables(db_path: Path, names: list[str] | None = None) -> dict[str, pd.DataFrame]:
    with sqlite3.connect(db_path) as conn:
        return {n: pd.read_sql_query(f'SELECT * FROM "{n.lower()}"', conn) for n in (names or SOURCE_TABLES)}


# ------------------------------------------------------------------------------ export
def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def table_digest(df: pd.DataFrame) -> str:
    """Stable content checksum (independent of file encoding)."""
    h = hashlib.sha256()
    h.update("|".join(map(str, df.columns)).encode())
    h.update(df.astype(str).to_csv(index=False, lineterminator="\n").encode())
    return h.hexdigest()


def export(result: PipelineResult, cfg: DatasetConfig, output_dir: str | Path, formats=("csv", "json", "sqlite", "sql")) -> dict:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    gt_dir = out.parent / "ground_truth"
    gt_dir.mkdir(parents=True, exist_ok=True)
    for name, df in result.tables.items():
        if "csv" in formats:
            df.to_csv(out / f"{name.lower()}.csv", index=False, lineterminator="\n")
        if "json" in formats:
            (out / f"{name.lower()}.json").write_text(df.to_json(orient="records", indent=1, date_format="iso"), encoding="utf-8")
    for name, df in result.dataset.ground_truth.items():
        df.to_csv(gt_dir / f"{name}.csv", index=False, lineterminator="\n")
        (gt_dir / f"{name}.json").write_text(df.to_json(orient="records", indent=1), encoding="utf-8")
    if "sqlite" in formats or "sql" in formats:
        write_sqlite(result.tables, out / "attribution.db")
    if "sql" in formats:
        with sqlite3.connect(out / "attribution.db") as conn, open(out / "seed_data.sql", "w", encoding="utf-8", newline="\n") as fh:
            for line in conn.iterdump():
                fh.write(line + "\n")
    (out / "validation_summary.json").write_text(json.dumps(result.validation, indent=2, default=str), encoding="utf-8")
    (out / "evaluation_summary.json").write_text(json.dumps(result.evaluation, indent=2, default=str), encoding="utf-8")

    manifest = {
        "generator": "attribution_engine", "version": __version__, "seed": cfg.seed, "config": cfg.to_dict(),
        "command": f"python -m attribution_engine.cli generate --scale {'full' if cfg.contacts >= 50_000 else 'starter'} --seed {cfg.seed}",
        "provenance": {"type": "fully synthetic", "external_data": "none", "personal_data": "none",
                       "license": "MIT (generated data may be redistributed freely)",
                       "reference": "Schema inspired by common CRM / marketing-automation concepts; no third-party dataset used."},
        "row_counts": {k: int(len(v)) for k, v in result.tables.items()},
        "table_content_digests": {k: table_digest(v) for k, v in result.tables.items()},
        "ground_truth_digests": {k: table_digest(v) for k, v in result.dataset.ground_truth.items()},
        "files": {}, "ground_truth_files": {},
    }
    for p in sorted(out.iterdir()):
        if p.is_file() and p.name != "source_manifest.json" and p.suffix != ".db":
            manifest["files"][p.name] = {"sha256": _sha(p), "bytes": p.stat().st_size}
    for p in sorted(gt_dir.iterdir()):
        if p.is_file():
            manifest["ground_truth_files"][p.name] = {"sha256": _sha(p), "bytes": p.stat().st_size}
    (out / "source_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def dataset_digests(cfg: DatasetConfig) -> tuple[dict[str, int], dict[str, str]]:
    r = run_pipeline(cfg)
    all_tables = dict(r.tables) | {f"ground_truth.{k}": v for k, v in r.dataset.ground_truth.items()}
    return {k: int(len(v)) for k, v in all_tables.items()}, {k: table_digest(v) for k, v in all_tables.items()}
