"""REST API over the attribution store (SQLite).

Run:  uvicorn attribution_engine.api:app --reload      (docs at /docs, Postman collection in postman/)

Environment:
  ATTRIBUTION_DATA_DIR   directory holding attribution.db (default: data/generated)

Tags map to the primary actors of the business workflow:
  System / Pipeline     -> system administrator
  Master data / Funnel  -> marketing & sales operations, support agent
  Attribution / ROI     -> analyst
  Exceptions / Identity -> analyst, support agent (data-quality triage)
  Evaluation            -> analyst (uses the *separate* ground truth; never fed back into the engine)
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from . import __version__
from .analytics import (campaign_roi, channel_roi, chronological_evaluation, identity_gaps, model_comparison,
                        reconciliation)
from .attribution import MODELS, AttributionOutput, run_attribution
from .config import AttributionParams, DatasetConfig, DEFAULT_SEED
from .detection import RULES, detect_exceptions, evaluate_detection
from .generator import OPP_TRANSITIONS
from .pipeline import (DERIVED_TABLES, SOURCE_TABLES, attribution_run_record, connect, insert_rows)

TAGS = [
    {"name": "System", "description": "Health, manifest, configuration and schema."},
    {"name": "Pipeline", "description": "Administrator: generate data, validate, prove reproducibility."},
    {"name": "Master data", "description": "Channels, campaigns, creatives, audiences, FX rates, contacts."},
    {"name": "Funnel", "description": "Sessions, leads, opportunities (append-only lifecycle), touchpoints, orders, costs."},
    {"name": "Attribution", "description": "Models, runs, results, per-opportunity explanations."},
    {"name": "ROI", "description": "Campaign / channel ROI and money reconciliation."},
    {"name": "Exceptions", "description": "Rule-detected data-quality exceptions and decisions."},
    {"name": "Identity", "description": "Missing / rejected identity links."},
    {"name": "Evaluation", "description": "Scores against the separate ground truth and the chronological split."},
]
app = FastAPI(title="Marketing Attribution Engine API", version=__version__, openapi_tags=TAGS,
              description="Join campaign touches, web sessions, leads, opportunities and revenue to compare attribution "
                          "approaches and ROI, with explicit rules, exceptions and traceability.")


# ----------------------------------------------------------------------------- storage
def data_dir() -> Path:
    return Path(os.environ.get("ATTRIBUTION_DATA_DIR", "data/generated"))


def db_path() -> Path:
    return data_dir() / "attribution.db"


def gt_dir() -> Path:
    return data_dir().parent / "ground_truth"


_lock = threading.RLock()
_cache: dict = {"mtime": None, "tables": {}}


def _require_db() -> Path:
    p = db_path()
    if not p.exists():
        raise HTTPException(404, "Dataset not found. Run `python -m attribution_engine.cli generate --scale starter` "
                                 "or POST /api/v1/pipeline/generate first.")
    return p


def query(sql: str, params: tuple = ()) -> pd.DataFrame:
    with connect(_require_db()) as conn:
        return pd.read_sql_query(sql, conn, params=params)


def table(name: str) -> pd.DataFrame:
    """Cached full-table read; invalidated whenever the database file changes."""
    p = _require_db()
    with _lock:
        mtime = p.stat().st_mtime_ns
        if _cache["mtime"] != mtime:
            _cache.update(mtime=mtime, tables={})
        if name not in _cache["tables"]:
            _cache["tables"][name] = query(f'SELECT * FROM "{name.lower()}"')
        return _cache["tables"][name]


def tables(names: list[str]) -> dict[str, pd.DataFrame]:
    return {n: table(n) for n in names}


def records(df: pd.DataFrame) -> list[dict]:
    if df is None or not len(df):
        return []
    clean = df.replace([np.inf, -np.inf], np.nan).astype(object).where(pd.notna(df), None)
    return clean.to_dict("records")


def page(df: pd.DataFrame, limit: int, offset: int) -> dict:
    return {"total": int(len(df)), "limit": limit, "offset": offset, "items": records(df.iloc[offset: offset + limit])}


def one(df: pd.DataFrame, what: str, key: str) -> dict:
    if not len(df):
        raise HTTPException(404, f"{what} {key} not found")
    return records(df.head(1))[0]


def _filter(df: pd.DataFrame, **conds) -> pd.DataFrame:
    for col, val in conds.items():
        if val is not None:
            df = df[df[col].astype(str) == str(val)]
    return df


def _json_file(path: Path, hint: str):
    if not path.exists():
        raise HTTPException(404, f"{path.name} not found. {hint}")
    return json.loads(path.read_text(encoding="utf-8"))


def _ground_truth(name: str = "ground_truth") -> pd.DataFrame:
    p = gt_dir() / f"{name}.csv"
    if not p.exists():
        raise HTTPException(404, "Ground truth not found. Generate the dataset first.")
    return pd.read_csv(p)


Limit = Query(100, ge=1, le=5000)
Offset = Query(0, ge=0)
ModelName = Literal["first_touch", "last_touch", "linear_multi_touch", "position_based", "time_decay"]


# ------------------------------------------------------------------------------ System
@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/docs")


@app.get("/health", tags=["System"], summary="Liveness and dataset availability")
def health():
    p = db_path()
    return {"status": "ok", "version": __version__, "data_directory": str(data_dir()), "dataset_available": p.exists(),
            "database_updated_at": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat() if p.exists() else None}


@app.get("/api/v1/meta/manifest", tags=["System"], summary="Source manifest: seed, config, provenance, checksums")
def manifest():
    return _json_file(data_dir() / "source_manifest.json", "Generate the dataset first.")


@app.get("/api/v1/meta/config", tags=["System"], summary="Default generator and attribution configuration")
def config():
    return {"dataset_defaults": DatasetConfig().to_dict(), "starter_volumes": DatasetConfig.starter().to_dict(),
            "attribution_defaults": AttributionParams().__dict__, "models": MODELS,
            "opportunity_transitions": {str(k): sorted(v) for k, v in OPP_TRANSITIONS.items()}}


@app.get("/api/v1/meta/schema", tags=["System"], summary="Live schema: tables, columns, types, PK/FK, row counts")
def schema():
    out = {}
    with connect(_require_db()) as conn:
        names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        for n in names:
            cols = [{"name": c[1], "type": c[2], "primary_key": bool(c[5])} for c in conn.execute(f'PRAGMA table_info("{n}")')]
            fks = [{"column": f[3], "references": f"{f[2]}.{f[4]}"} for f in conn.execute(f'PRAGMA foreign_key_list("{n}")')]
            rows = conn.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0]
            out[n] = {"rows": rows, "columns": cols, "foreign_keys": fks}
    return out


# ---------------------------------------------------------------------------- Pipeline
_jobs: dict[str, dict] = {}


class GenerateRequest(BaseModel):
    scale: Literal["starter", "full"] = "starter"
    seed: int = DEFAULT_SEED


class ReproRequest(BaseModel):
    scale: Literal["starter", "full"] = "starter"
    seed: int = DEFAULT_SEED


def _run_job(job_id: str, fn):
    _jobs[job_id].update(status="running", started_at=datetime.now(timezone.utc).isoformat())
    try:
        _jobs[job_id].update(status="succeeded", result=fn())
    except Exception as exc:  # surfaced through the job resource
        _jobs[job_id].update(status="failed", error=f"{type(exc).__name__}: {exc}")
    _jobs[job_id]["finished_at"] = datetime.now(timezone.utc).isoformat()


def _start_job(kind: str, params: dict, fn) -> dict:
    job_id = f"JOB-{uuid.uuid4().hex[:12]}"
    _jobs[job_id] = {"job_id": job_id, "kind": kind, "params": params, "status": "queued",
                     "created_at": datetime.now(timezone.utc).isoformat()}
    threading.Thread(target=_run_job, args=(job_id, fn), daemon=True).start()
    return _jobs[job_id]


@app.post("/api/v1/pipeline/generate", tags=["Pipeline"], status_code=202,
          summary="Regenerate the dataset (background job): generate -> detect -> attribute -> validate -> export")
def generate(req: GenerateRequest):
    def work():
        from .pipeline import export, run_pipeline
        cfg = DatasetConfig.for_scale(req.scale, req.seed)
        with _lock:
            result = run_pipeline(cfg)
            m = export(result, cfg, data_dir())
            _cache.update(mtime=None, tables={})
        return {"validation_passed": result.validation["passed"], "row_counts": m["row_counts"],
                "detection_f1": result.evaluation["detection_vs_ground_truth"]["overall"]["f1"]}
    return _start_job("generate", req.model_dump(), work)


@app.post("/api/v1/pipeline/reproducibility", tags=["Pipeline"], status_code=202,
          summary="Generate twice with the same seed and compare row counts + checksums (background job)")
def reproducibility(req: ReproRequest):
    def work():
        from .pipeline import dataset_digests
        cfg = DatasetConfig.for_scale(req.scale, req.seed)
        c1, d1 = dataset_digests(cfg)
        c2, d2 = dataset_digests(cfg)
        mism = sorted(k for k in d1 if d1[k] != d2.get(k))
        return {"reproducible": c1 == c2 and not mism, "tables_compared": len(d1), "mismatched_tables": mism,
                "row_counts": c1, "checksums": d1}
    return _start_job("reproducibility", req.model_dump(), work)


@app.get("/api/v1/pipeline/jobs", tags=["Pipeline"], summary="List background jobs")
def list_jobs():
    return sorted(_jobs.values(), key=lambda j: j["created_at"], reverse=True)


@app.get("/api/v1/pipeline/jobs/{job_id}", tags=["Pipeline"], summary="Job status and result")
def get_job(job_id: str):
    if job_id not in _jobs:
        raise HTTPException(404, f"Job {job_id} not found")
    return _jobs[job_id]


@app.get("/api/v1/pipeline/validation", tags=["Pipeline"], summary="Validation summary written at generation time")
def validation_summary(only_failed: bool = False):
    v = _json_file(data_dir() / "validation_summary.json", "Generate the dataset first.")
    if only_failed:
        v = {k: v[k] for k in ("passed", "total_checks", "by_category", "failed_checks")}
    return v


@app.post("/api/v1/pipeline/validate", tags=["Pipeline"], summary="Re-run every validation check against the live database")
def revalidate():
    from .validation import validate_dataset
    t = tables(SOURCE_TABLES + DERIVED_TABLES)
    # Validate the generated baseline (snapshot + RUN-BASELINE); events appended later through the API
    # are checked separately because Opportunity.stage is the generation snapshot, not the live state.
    base = {k: (v[v["run_id"] == "RUN-BASELINE"] if "run_id" in v.columns else v) for k, v in t.items()}
    ev = t["OpportunityEvent"]
    base["OpportunityEvent"] = ev[ev["actor"] == "crm_sync"]
    gt = _ground_truth() if (gt_dir() / "ground_truth.csv").exists() else None
    r = validate_dataset(base, detect_exceptions(t), gt)
    live = ev.sort_values(["opportunity_id", "sequence"])
    prev = live.groupby("opportunity_id")["to_stage"].shift(1)
    appended = live[live["actor"] != "crm_sync"]
    bad = [e for e, f, to in zip(appended["event_id"], prev[appended.index], appended["to_stage"])
           if to not in OPP_TRANSITIONS.get(f if isinstance(f, str) else None, set())]
    ordered = not pd.to_datetime(live["occurred_at"]).groupby(live["opportunity_id"]).diff().lt(pd.Timedelta(0)).any()
    out = {k: r[k] for k in ("passed", "total_checks", "by_category", "failed_checks", "row_counts")}
    out["live_lifecycle"] = {"api_appended_events": int(len(appended)), "invalid_transitions": bad,
                             "chronological": bool(ordered), "passed": not bad and bool(ordered)}
    out["passed"] = out["passed"] and out["live_lifecycle"]["passed"]
    return out


# ------------------------------------------------------------------------- Master data
@app.get("/api/v1/channels", tags=["Master data"], summary="List channels")
def channels():
    return records(table("Channel"))


@app.get("/api/v1/campaigns", tags=["Master data"], summary="List campaigns with filters")
def campaigns(channel_id: Optional[str] = None, market: Optional[str] = None, status: Optional[str] = None,
              currency: Optional[str] = None, limit: int = Limit, offset: int = Offset):
    return page(_filter(table("Campaign"), channel_id=channel_id, market=market, status=status, currency=currency), limit, offset)


@app.get("/api/v1/campaigns/{campaign_id}", tags=["Master data"], summary="Campaign with creatives, audiences, spend and delivery")
def campaign(campaign_id: str):
    c = one(table("Campaign")[table("Campaign")["campaign_id"] == campaign_id], "Campaign", campaign_id)
    cost = table("CostRecord")[table("CostRecord")["campaign_id"] == campaign_id]
    exc = table("ExceptionLog")
    c["creatives"] = records(table("Creative")[table("Creative")["campaign_id"] == campaign_id])
    c["audiences"] = records(table("CampaignAudience")[table("CampaignAudience")["campaign_id"] == campaign_id]
                             .merge(table("Audience"), on="audience_id", suffixes=("", "_audience")))
    c["cost_records"] = int(len(cost))
    c["ledger_spend_native"] = round(float(cost.loc[cost["currency"] == c["currency"], "amount"].sum()), 2)
    c["touchpoints"] = int((table("Touchpoint")["campaign_id"] == campaign_id).sum())
    c["exceptions"] = records(exc[(exc["record_id"] == campaign_id) | (exc["related_id"] == campaign_id)])
    return c


@app.get("/api/v1/campaigns/{campaign_id}/costs", tags=["Master data"], summary="Cost records of a campaign (with quarantine flag)")
def campaign_costs(campaign_id: str):
    cost = table("CostRecord")[table("CostRecord")["campaign_id"] == campaign_id].copy()
    q = set(table("ExceptionLog").query("issue_type == 'currency_mismatch'")["record_id"])
    cost["quarantined"] = cost["cost_record_id"].isin(q)
    return records(cost)


@app.get("/api/v1/campaigns/{campaign_id}/touchpoints", tags=["Master data"], summary="Touchpoints delivered by a campaign")
def campaign_touchpoints(campaign_id: str, limit: int = Limit, offset: int = Offset):
    return page(table("Touchpoint")[table("Touchpoint")["campaign_id"] == campaign_id], limit, offset)


@app.get("/api/v1/creatives", tags=["Master data"], summary="List creatives")
def creatives(campaign_id: Optional[str] = None, format: Optional[str] = None, limit: int = Limit, offset: int = Offset):
    return page(_filter(table("Creative"), campaign_id=campaign_id, format=format), limit, offset)


@app.get("/api/v1/audiences", tags=["Master data"], summary="List audiences")
def audiences():
    return records(table("Audience"))


@app.get("/api/v1/audiences/{audience_id}/campaigns", tags=["Master data"], summary="Campaigns targeting an audience (N:M bridge)")
def audience_campaigns(audience_id: str):
    b = table("CampaignAudience")[table("CampaignAudience")["audience_id"] == audience_id]
    return records(b.merge(table("Campaign")[["campaign_id", "campaign_name", "channel_id", "market"]], on="campaign_id"))


@app.get("/api/v1/fx-rates", tags=["Master data"], summary="Monthly FX reference rates to USD")
def fx_rates(currency: Optional[str] = None, month: Optional[str] = None):
    return records(_filter(table("FxRate"), currency=currency, rate_month=month))


@app.get("/api/v1/contacts", tags=["Master data"], summary="List contacts")
def contacts(market: Optional[str] = None, lifecycle_state: Optional[str] = None, company_size: Optional[str] = None,
             limit: int = Limit, offset: int = Offset):
    return page(_filter(table("Contact"), market=market, lifecycle_state=lifecycle_state, company_size=company_size), limit, offset)


@app.get("/api/v1/contacts/{contact_id}", tags=["Master data"], summary="Contact with funnel counts")
def contact(contact_id: str):
    c = one(table("Contact")[table("Contact")["contact_id"] == contact_id], "Contact", contact_id)
    for n, col in [("Touchpoint", "touchpoints"), ("WebSession", "sessions"), ("Lead", "leads"), ("Opportunity", "opportunities")]:
        c[col] = int((table(n)["contact_id"] == contact_id).sum())
    return c


@app.get("/api/v1/contacts/{contact_id}/journey", tags=["Master data"],
         summary="Chronological journey: touches, sessions, leads, opportunity events, orders")
def contact_journey(contact_id: str):
    contact(contact_id)
    ev = []
    for r in records(table("Touchpoint")[table("Touchpoint")["contact_id"] == contact_id]):
        ev.append({"at": r["occurred_at"], "type": "touchpoint", "id": r["touchpoint_id"], "campaign_id": r["campaign_id"],
                   "channel_id": r["channel_id"], "detail": r["touch_type"] + (" (offline)" if r["is_offline"] else "")})
    for r in records(table("WebSession")[table("WebSession")["contact_id"] == contact_id]):
        ev.append({"at": r["started_at"], "type": "session", "id": r["session_id"], "detail": f"evidence={r['identity_evidence']}"})
    leads = table("Lead")[table("Lead")["contact_id"] == contact_id]
    for r in records(leads):
        ev.append({"at": r["created_at"], "type": "lead", "id": r["lead_id"], "detail": f"{r['source_system']} session={r['session_id']}"})
    opps = table("Opportunity")[table("Opportunity")["contact_id"] == contact_id]
    oe = table("OpportunityEvent")
    for r in records(oe[oe["opportunity_id"].isin(opps["opportunity_id"])]):
        ev.append({"at": r["occurred_at"], "type": "opportunity_event", "id": r["event_id"], "opportunity_id": r["opportunity_id"],
                   "detail": f"{r['from_stage']} -> {r['to_stage']}"})
    orders = table("Order")[table("Order")["opportunity_id"].isin(opps["opportunity_id"])]
    for r in records(orders):
        ev.append({"at": r["recognized_at"], "type": "order", "id": r["order_id"], "opportunity_id": r["opportunity_id"],
                   "detail": f"{r['revenue_amount']} {r['currency']} invoice={r['external_invoice_id']}"})
    ev.sort(key=lambda e: (e["at"] or "", e["type"]))
    return {"contact_id": contact_id, "events": ev, "event_count": len(ev),
            "campaigns_touched": sorted({e["campaign_id"] for e in ev if e.get("campaign_id")})}


# ------------------------------------------------------------------------------ Funnel
@app.get("/api/v1/sessions", tags=["Funnel"], summary="List web sessions")
def sessions(contact_id: Optional[str] = None, identity_evidence: Optional[str] = None, anonymous: Optional[bool] = None,
             limit: int = Limit, offset: int = Offset):
    df = _filter(table("WebSession"), contact_id=contact_id, identity_evidence=identity_evidence)
    if anonymous is not None:
        df = df[df["contact_id"].isna() == anonymous]
    return page(df, limit, offset)


@app.get("/api/v1/sessions/{session_id}", tags=["Funnel"], summary="Session with linked leads")
def session(session_id: str):
    s = one(table("WebSession")[table("WebSession")["session_id"] == session_id], "Session", session_id)
    s["leads"] = records(table("Lead")[table("Lead")["session_id"] == session_id])
    return s


@app.get("/api/v1/leads", tags=["Funnel"], summary="List leads")
def leads(status: Optional[str] = None, source_system: Optional[str] = None, contact_id: Optional[str] = None,
          has_session: Optional[bool] = None, limit: int = Limit, offset: int = Offset):
    df = _filter(table("Lead"), status=status, source_system=source_system, contact_id=contact_id)
    if has_session is not None:
        df = df[df["session_id"].notna() == has_session]
    return page(df, limit, offset)


@app.get("/api/v1/leads/{lead_id}", tags=["Funnel"], summary="Lead with session, opportunity and identity verdict")
def lead(lead_id: str):
    l = one(table("Lead")[table("Lead")["lead_id"] == lead_id], "Lead", lead_id)
    s = table("WebSession")[table("WebSession")["session_id"] == l["session_id"]]
    l["session"] = records(s)[0] if len(s) else None
    l["opportunities"] = records(table("Opportunity")[table("Opportunity")["lead_id"] == lead_id])
    exc = table("ExceptionLog")
    rejected = exc[(exc["issue_type"] == "anonymous_session_without_evidence") & (exc["record_id"] == lead_id)]
    l["identity_link"] = "rejected" if len(rejected) else ("verified" if l["session_id"] else "no_session_offline_source")
    return l


def _current_stage(opportunity_id: str) -> dict | None:
    df = query("SELECT * FROM opportunity_current_stage WHERE opportunity_id = ?", (opportunity_id,))
    return records(df)[0] if len(df) else None


@app.get("/api/v1/opportunities", tags=["Funnel"], summary="List opportunities")
def opportunities(stage: Optional[str] = None, currency: Optional[str] = None, contact_id: Optional[str] = None,
                  created_from: Optional[str] = None, created_to: Optional[str] = None, limit: int = Limit, offset: int = Offset):
    """`stage` filters on the live stage derived from the event history (current_stage)."""
    cur = query("SELECT opportunity_id, current_stage, stage_since FROM opportunity_current_stage")
    df = table("Opportunity").rename(columns={"stage": "stage_snapshot"}).merge(cur, on="opportunity_id", how="left")
    df = _filter(df, current_stage=stage, currency=currency, contact_id=contact_id)
    if created_from:
        df = df[df["created_at"] >= created_from]
    if created_to:
        df = df[df["created_at"] < created_to]
    return page(df, limit, offset)


@app.get("/api/v1/opportunities/{opportunity_id}", tags=["Funnel"], summary="Opportunity with current stage (from history), orders, attribution")
def opportunity(opportunity_id: str):
    o = one(table("Opportunity")[table("Opportunity")["opportunity_id"] == opportunity_id], "Opportunity", opportunity_id)
    o["stage_snapshot"] = o.pop("stage")
    o["current"] = _current_stage(opportunity_id)
    o["orders"] = records(table("Order")[table("Order")["opportunity_id"] == opportunity_id])
    ar = query("SELECT model, campaign_id, credit_share, credited_revenue, currency FROM attributionresult "
               "WHERE run_id = 'RUN-BASELINE' AND opportunity_id = ? ORDER BY model, campaign_id", (opportunity_id,))
    o["attribution"] = records(ar)
    return o


@app.get("/api/v1/opportunities/{opportunity_id}/events", tags=["Funnel"], summary="Append-only lifecycle history")
def opportunity_events(opportunity_id: str):
    df = query("SELECT * FROM opportunityevent WHERE opportunity_id = ? ORDER BY sequence", (opportunity_id,))
    if not len(df):
        raise HTTPException(404, f"Opportunity {opportunity_id} not found")
    return records(df)


class StageChange(BaseModel):
    to_stage: Literal["prospecting", "qualified", "proposal", "closed_won", "closed_lost"]
    occurred_at: Optional[str] = Field(None, description="ISO timestamp; defaults to now (UTC)")
    actor: str = Field("api_user", max_length=64)
    note: Optional[str] = Field(None, max_length=500)


@app.post("/api/v1/opportunities/{opportunity_id}/events", tags=["Funnel"], status_code=201,
          summary="Append a stage change (validated transition; history is never overwritten)")
def append_opportunity_event(opportunity_id: str, change: StageChange):
    with _lock:
        cur = _current_stage(opportunity_id)
        if cur is None:
            raise HTTPException(404, f"Opportunity {opportunity_id} not found")
        allowed = OPP_TRANSITIONS.get(cur["current_stage"], set())
        if change.to_stage not in allowed:
            raise HTTPException(409, {"error": "invalid_transition", "current_stage": cur["current_stage"],
                                      "requested": change.to_stage, "allowed": sorted(allowed)})
        ts = pd.Timestamp(change.occurred_at) if change.occurred_at else pd.Timestamp(datetime.now(timezone.utc)).tz_localize(None)
        if ts.tzinfo is not None:
            ts = ts.tz_convert("UTC").tz_localize(None)
        if ts < pd.Timestamp(cur["stage_since"]):
            raise HTTPException(422, {"error": "out_of_order_event", "last_event_at": cur["stage_since"]})
        seq = int(cur["event_count"]) + 1
        row = pd.DataFrame([{"event_id": f"OPPEVT-{opportunity_id}-{seq:02d}", "opportunity_id": opportunity_id, "sequence": seq,
                             "event_type": "closed" if change.to_stage.startswith("closed") else "stage_changed",
                             "from_stage": cur["current_stage"], "to_stage": change.to_stage,
                             "occurred_at": ts.strftime("%Y-%m-%dT%H:%M:%S"), "actor": change.actor}])
        with connect(_require_db()) as conn:
            insert_rows(conn, "OpportunityEvent", row)
    return {"appended": records(row)[0], "current": _current_stage(opportunity_id),
            "note": "Revenue is only recognised from Order records (billing); a closed_won event alone credits nothing."}


@app.get("/api/v1/touchpoints", tags=["Funnel"], summary="List touchpoints")
def touchpoints(contact_id: Optional[str] = None, campaign_id: Optional[str] = None, channel_id: Optional[str] = None,
                is_offline: Optional[bool] = None, has_conversion_claim: Optional[bool] = None,
                limit: int = Limit, offset: int = Offset):
    df = _filter(table("Touchpoint"), contact_id=contact_id, campaign_id=campaign_id, channel_id=channel_id)
    if is_offline is not None:
        df = df[df["is_offline"].astype(bool) == is_offline]
    if has_conversion_claim is not None:
        df = df[df["reported_conversion_id"].notna() == has_conversion_claim]
    return page(df, limit, offset)


@app.get("/api/v1/touchpoints/{touchpoint_id}", tags=["Funnel"], summary="Single touchpoint")
def touchpoint(touchpoint_id: str):
    return one(table("Touchpoint")[table("Touchpoint")["touchpoint_id"] == touchpoint_id], "Touchpoint", touchpoint_id)


@app.get("/api/v1/orders", tags=["Funnel"], summary="List orders (closed revenue) with recognition status")
def orders(opportunity_id: Optional[str] = None, currency: Optional[str] = None, limit: int = Limit, offset: int = Offset):
    df = _filter(table("Order"), opportunity_id=opportunity_id, currency=currency).copy()
    dup = set(table("ExceptionLog").query("issue_type == 'duplicate_revenue'")["record_id"])
    df["recognised"] = ~df["order_id"].isin(dup)
    return page(df, limit, offset)


@app.get("/api/v1/cost-records", tags=["Funnel"], summary="List cost records")
def cost_records(campaign_id: Optional[str] = None, currency: Optional[str] = None, period_month: Optional[str] = None,
                 limit: int = Limit, offset: int = Offset):
    return page(_filter(table("CostRecord"), campaign_id=campaign_id, currency=currency, period_month=period_month), limit, offset)


# ------------------------------------------------------------------------- Attribution
MODEL_DOCS = {
    "first_touch": "100% credit to the campaign of the earliest eligible touch.",
    "last_touch": "100% credit to the campaign of the latest eligible touch before conversion.",
    "linear_multi_touch": "Equal credit to every eligible touch, summed per campaign.",
    "position_based": "U-shaped: 40% first, 40% last, 20% spread over middle touches (2 touches: 50/50).",
    "time_decay": "Weight 0.5^(days_before_conversion / half_life); half-life 14 days by default.",
}
ELIGIBILITY = ["E1 same contact as the opportunity", "E2 occurred_at <= conversion (opportunity closed_at)",
               "E3 occurred_at >= conversion - lookback_days", "E4 after the contact's previous conversion",
               "E5 not a detected post-conversion exception", "E6 offline touches included unless include_offline=false"]


@app.get("/api/v1/attribution/models", tags=["Attribution"], summary="Attribution models and eligibility rules")
def attribution_models():
    return {"models": MODEL_DOCS, "eligibility_rules": ELIGIBILITY, "defaults": AttributionParams().__dict__,
            "revenue_source": "Order rows not detected as duplicate revenue; credit allocated in integer cents (largest remainder)."}


class RunRequest(BaseModel):
    models: list[ModelName] = Field(default_factory=lambda: list(MODELS))
    lookback_days: int = Field(365, ge=1, le=3650)
    time_decay_half_life_days: float = Field(14.0, gt=0, le=365)
    position_first_weight: float = Field(0.4, ge=0, le=1)
    position_last_weight: float = Field(0.4, ge=0, le=1)
    include_offline: bool = True
    triggered_by: str = Field("api_user", max_length=64)


def _attr_inputs() -> dict[str, pd.DataFrame]:
    return tables(["Touchpoint", "Order", "Opportunity", "Campaign", "FxRate", "Channel", "CostRecord"])


@app.post("/api/v1/attribution/runs", tags=["Attribution"], status_code=201,
          summary="Run attribution with custom parameters and persist results as a new run")
def create_run(req: RunRequest):
    params = AttributionParams(lookback_days=req.lookback_days, time_decay_half_life_days=req.time_decay_half_life_days,
                               position_first_weight=req.position_first_weight, position_last_weight=req.position_last_weight,
                               include_offline=req.include_offline)
    try:
        params.validate()
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    with _lock:
        existing = table("AttributionRun")
        run_id = f"RUN-2026-{int((existing['run_id'] != 'RUN-BASELINE').sum()) + 1:06d}"
        out = run_attribution(_attr_inputs(), table("ExceptionLog"), params, run_id=run_id, models=list(dict.fromkeys(req.models)))
        run = attribution_run_record(run_id, out.params, out, req.triggered_by)
        un = out.unattributed.copy(); un.insert(0, "run_id", run_id)
        audit = out.touch_audit.copy(); audit.insert(0, "run_id", run_id)
        with connect(_require_db()) as conn:
            insert_rows(conn, "AttributionRun", run)
            insert_rows(conn, "AttributionResult", out.results)
            insert_rows(conn, "UnattributedRevenue", un)
            insert_rows(conn, "TouchEligibilityAudit", audit)
    rec = reconciliation(_attr_inputs(), table("ExceptionLog"), out.results, out.unattributed)
    return {"run": records(run)[0], "reconciliation_passed": rec["passed"],
            "attributed_revenue_usd_by_model": {m: v for m, v in rec["attributed_revenue_usd_by_model"].items() if m in out.params["models"]},
            "touch_eligibility": {k: int(v) for k, v in out.touch_audit.drop(columns=["opportunity_id"]).sum().items()}}


@app.get("/api/v1/attribution/runs", tags=["Attribution"], summary="List attribution runs")
def list_runs():
    return records(table("AttributionRun"))


@app.get("/api/v1/attribution/runs/{run_id}", tags=["Attribution"], summary="Run parameters and per-model totals")
def get_run(run_id: str):
    r = one(table("AttributionRun")[table("AttributionRun")["run_id"] == run_id], "Run", run_id)
    r["totals_by_model"] = records(query("SELECT model, COUNT(*) AS rows, COUNT(DISTINCT opportunity_id) AS opportunities, "
                                         "ROUND(SUM(credited_revenue_usd), 2) AS credited_revenue_usd FROM attributionresult "
                                         "WHERE run_id = ? GROUP BY model", (run_id,)))
    return r


def _results(run_id: str, model: Optional[str] = None) -> pd.DataFrame:
    sql, params = "SELECT * FROM attributionresult WHERE run_id = ?", [run_id]
    if model:
        sql += " AND model = ?"; params.append(model)
    df = query(sql, tuple(params))
    if not len(df) and not len(query("SELECT 1 FROM attributionrun WHERE run_id = ?", (run_id,))):
        raise HTTPException(404, f"Run {run_id} not found")
    return df


def _output(run_id: str) -> AttributionOutput:
    res = _results(run_id)
    un = query("SELECT * FROM unattributedrevenue WHERE run_id = ?", (run_id,)).drop(columns=["run_id"])
    audit = query("SELECT * FROM toucheligibilityaudit WHERE run_id = ?", (run_id,)).drop(columns=["run_id"])
    return AttributionOutput(results=res, unattributed=un, touch_audit=audit, params={"models": sorted(res["model"].unique())})


@app.get("/api/v1/attribution/results", tags=["Attribution"], summary="Attribution result rows")
def attribution_results(run_id: str = "RUN-BASELINE", model: Optional[ModelName] = None, campaign_id: Optional[str] = None,
                        opportunity_id: Optional[str] = None, channel_id: Optional[str] = None, limit: int = Limit, offset: int = Offset):
    df = _filter(_results(run_id, model), campaign_id=campaign_id, opportunity_id=opportunity_id, channel_id=channel_id)
    return page(df, limit, offset)


@app.get("/api/v1/attribution/opportunities/{opportunity_id}", tags=["Attribution"],
         summary="Explain one conversion: every touch with its eligibility reason, and credit per model side by side")
def explain_opportunity(opportunity_id: str, run_id: str = "RUN-BASELINE"):
    opp = one(table("Opportunity")[table("Opportunity")["opportunity_id"] == opportunity_id], "Opportunity", opportunity_id)
    run = one(table("AttributionRun")[table("AttributionRun")["run_id"] == run_id], "Run", run_id)
    tp = table("Touchpoint")[table("Touchpoint")["contact_id"] == opp["contact_id"]].sort_values("occurred_at").copy()
    flagged = set(table("ExceptionLog").query("issue_type == 'post_conversion_touch'")["record_id"])
    conv = pd.Timestamp(opp["closed_at"]) if opp["stage"] == "closed_won" and opp["closed_at"] else None
    if conv is not None:
        won = table("Opportunity")
        prev = won[(won["contact_id"] == opp["contact_id"]) & (won["stage"] == "closed_won") & (won["closed_at"] < opp["closed_at"])]["closed_at"].max()
        t = pd.to_datetime(tp["occurred_at"])
        lb = conv - pd.Timedelta(days=int(run["lookback_days"]))
        before_prev = (t <= pd.Timestamp(prev)) if pd.notna(prev) else pd.Series(False, index=tp.index)
        offline_out = tp["is_offline"].astype(bool) & (not bool(run["include_offline"]))
        tp["eligibility"] = np.select(
            [t > conv, t < lb, before_prev, tp["touchpoint_id"].isin(flagged), offline_out],
            ["after_conversion", "outside_lookback", "before_previous_conversion", "flagged_exception", "offline_excluded"], "eligible")
        tp["days_before_conversion"] = ((conv - t).dt.total_seconds() / 86400).round(2)
    else:
        tp["eligibility"] = "no_conversion"
    credit = _results(run_id).query("opportunity_id == @opportunity_id")
    pivot = credit.pivot_table(index="campaign_id", columns="model", values="credited_revenue", aggfunc="sum").reset_index()
    return {"opportunity": opp, "run_id": run_id, "conversion_at": opp["closed_at"] if conv is not None else None,
            "touches": records(tp), "credit_by_campaign": records(pivot),
            "unattributed": records(query("SELECT * FROM unattributedrevenue WHERE run_id = ? AND opportunity_id = ?", (run_id, opportunity_id)))}


@app.get("/api/v1/attribution/compare", tags=["Attribution"], summary="Compare models: channel shares, rank correlation, credit shift")
def compare_models(run_id: str = "RUN-BASELINE"):
    res = _results(run_id)
    truth = _ground_truth("channel_contribution") if (gt_dir() / "channel_contribution.csv").exists() else None
    return model_comparison(tables(["Channel"]), table("ExceptionLog"), res, truth)


@app.get("/api/v1/attribution/unattributed", tags=["Attribution"], summary="Recognised revenue with no eligible touch (visible, never dropped)")
def unattributed(run_id: str = "RUN-BASELINE", limit: int = Limit, offset: int = Offset):
    return page(query("SELECT * FROM unattributedrevenue WHERE run_id = ?", (run_id,)), limit, offset)


# --------------------------------------------------------------------------------- ROI
def _roi(model: str, run_id: str) -> pd.DataFrame:
    return campaign_roi(tables(["Campaign", "CostRecord", "FxRate"]), table("ExceptionLog"), _results(run_id, model), model)


@app.get("/api/v1/roi/campaigns", tags=["ROI"], summary="Campaign ROI for a model (USD reporting currency)")
def roi_campaigns(model: ModelName = "linear_multi_touch", run_id: str = "RUN-BASELINE", roi_status: Optional[str] = None,
                  channel_id: Optional[str] = None, market: Optional[str] = None,
                  sort_by: Literal["roi_pct", "roas", "attributed_revenue_usd", "spend_usd", "campaign_id"] = "roi_pct",
                  descending: bool = True, limit: int = Limit, offset: int = Offset):
    df = _filter(_roi(model, run_id), roi_status=roi_status, channel_id=channel_id, market=market)
    df = df.sort_values(sort_by, ascending=not descending, na_position="last")
    return page(df, limit, offset)


@app.get("/api/v1/roi/campaigns/{campaign_id}", tags=["ROI"], summary="One campaign's ROI under every model")
def roi_campaign(campaign_id: str, run_id: str = "RUN-BASELINE"):
    rows = []
    for m in sorted(_results(run_id)["model"].unique()):
        r = _roi(m, run_id)
        rows += records(r[r["campaign_id"] == campaign_id])
    if not rows:
        raise HTTPException(404, f"Campaign {campaign_id} not found")
    return {"campaign_id": campaign_id, "run_id": run_id, "by_model": rows}


@app.get("/api/v1/roi/channels", tags=["ROI"], summary="Channel ROI roll-up for a model")
def roi_channels(model: ModelName = "linear_multi_touch", run_id: str = "RUN-BASELINE"):
    return records(channel_roi(_roi(model, run_id), table("Channel")))


@app.get("/api/v1/roi/reconciliation", tags=["ROI"], summary="Money conservation: orders -> recognised -> attributed + unattributed; spend")
def roi_reconciliation(run_id: str = "RUN-BASELINE"):
    o = _output(run_id)
    return reconciliation(tables(["Order", "Opportunity", "CostRecord", "FxRate", "Campaign"]), table("ExceptionLog"), o.results, o.unattributed)


# -------------------------------------------------------------------------- Exceptions
@app.get("/api/v1/exceptions", tags=["Exceptions"], summary="Detected exceptions with rule and decision")
def exceptions(issue_type: Optional[str] = None, record_type: Optional[str] = None, limit: int = Limit, offset: int = Offset):
    return page(_filter(table("ExceptionLog"), issue_type=issue_type, record_type=record_type), limit, offset)


@app.get("/api/v1/exceptions/summary", tags=["Exceptions"], summary="Counts per issue type with rule definitions")
def exceptions_summary():
    exc = table("ExceptionLog")
    counts = exc["issue_type"].value_counts().to_dict()
    return [{"issue_type": k, "rule_id": v[0], "rule": v[1], "decision": v[2], "detected": int(counts.get(k, 0))} for k, v in RULES.items()]


@app.get("/api/v1/exceptions/{exception_id}", tags=["Exceptions"], summary="One exception with the offending record")
def exception(exception_id: str):
    e = one(table("ExceptionLog")[table("ExceptionLog")["exception_id"] == exception_id], "Exception", exception_id)
    key = {"Order": "order_id", "Touchpoint": "touchpoint_id", "CostRecord": "cost_record_id", "Campaign": "campaign_id", "Lead": "lead_id"}[e["record_type"]]
    src = table(e["record_type"])
    e["record"] = records(src[src[key] == e["record_id"]])[0] if (src[key] == e["record_id"]).any() else None
    return e


@app.post("/api/v1/exceptions/detect", tags=["Exceptions"], summary="Re-run the rule detectors on live data (dry run, not persisted)")
def detect_now():
    found = detect_exceptions(tables(SOURCE_TABLES))
    stored = table("ExceptionLog")
    return {"detected": int(len(found)), "by_issue_type": found["issue_type"].value_counts().to_dict(),
            "matches_stored_log": set(found["record_id"]) == set(stored["record_id"]), "items": records(found.head(200))}


# ---------------------------------------------------------------------------- Identity
@app.get("/api/v1/identity/gaps", tags=["Identity"], summary="Missing / rejected identity links and unattributable revenue")
def identity(run_id: str = "RUN-BASELINE"):
    un = query("SELECT * FROM unattributedrevenue WHERE run_id = ?", (run_id,))
    return identity_gaps(tables(["WebSession", "Lead", "Touchpoint", "Opportunity"]), table("ExceptionLog"), un)


@app.get("/api/v1/identity/rejected-links", tags=["Identity"], summary="Leads whose session link was rejected (no identity evidence)")
def rejected_links():
    exc = table("ExceptionLog").query("issue_type == 'anonymous_session_without_evidence'")
    l = table("Lead").merge(exc[["exception_id", "record_id", "detail"]], left_on="lead_id", right_on="record_id")
    return records(l.merge(table("WebSession"), on="session_id", how="left", suffixes=("", "_session")))


# -------------------------------------------------------------------------- Evaluation
@app.get("/api/v1/evaluation/summary", tags=["Evaluation"], summary="Full evaluation summary written at generation time")
def evaluation():
    return _json_file(data_dir() / "evaluation_summary.json", "Generate the dataset first.")


@app.get("/api/v1/evaluation/detection", tags=["Evaluation"], summary="Detector precision / recall / F1 against ground truth")
def evaluation_detection():
    return evaluate_detection(table("ExceptionLog"), _ground_truth())


@app.get("/api/v1/evaluation/model-accuracy", tags=["Evaluation"], summary="Distance of each model's channel credit from ground-truth contribution")
def evaluation_models(run_id: str = "RUN-BASELINE"):
    c = model_comparison(tables(["Channel"]), table("ExceptionLog"), _results(run_id), _ground_truth("channel_contribution"))
    return {k: c[k] for k in ("channel_true_share", "distance_to_ground_truth", "best_model_vs_ground_truth", "channel_revenue_share")}


@app.get("/api/v1/evaluation/chronological", tags=["Evaluation"], summary="Train / evaluation split: ROAS rank stability per model")
def evaluation_chronological(cutoff: str = "2026-07-01", run_id: str = "RUN-BASELINE"):
    try:
        pd.Timestamp(cutoff)
    except ValueError:
        raise HTTPException(422, "cutoff must be an ISO date")
    return chronological_evaluation(tables(["Opportunity", "CostRecord", "FxRate", "Campaign"]), table("ExceptionLog"), _results(run_id), cutoff)


@app.get("/api/v1/ground-truth", tags=["Evaluation"], summary="Labelled anomalies (separate file; never an engine input)")
def ground_truth(issue_type: Optional[str] = None):
    return records(_filter(_ground_truth(), issue_type=issue_type))


@app.get("/api/v1/ground-truth/channel-contribution", tags=["Evaluation"], summary="True channel contribution from the generating rule")
def ground_truth_channels():
    return records(_ground_truth("channel_contribution"))
