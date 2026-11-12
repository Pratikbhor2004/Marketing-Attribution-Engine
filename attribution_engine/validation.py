"""Dataset validation: keys, joins, enums, ID formats, dates, arithmetic, lifecycle, duplicates.

Records that the detection layer flags are the *only* rows allowed to break a business rule;
everything else must pass. Each check is reported individually in validation_summary.json.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .attribution import recognized_revenue
from .detection import excluded_ids, ISSUE_TYPES
from .generator import (CHANNEL_NAMES, CURRENCIES, DEVICES, LEAD_SOURCES, MARKETS, OPP_STAGES, OPP_TRANSITIONS,
                        SESSION_EVIDENCE, TOUCH_TYPES, COMPANY_SIZES)

# Engine outputs: ExceptionLog legitimately carries the *detected* issue_type.
DERIVED_OUTPUTS = {"ExceptionLog", "AttributionRun", "AttributionResult", "UnattributedRevenue",
                   "TouchEligibilityAudit", "CampaignROI", "ChannelROI"}

PRIMARY_KEYS = {
    "Channel": "channel_id", "FxRate": "fx_rate_id", "Campaign": "campaign_id", "Creative": "creative_id",
    "Audience": "audience_id", "CampaignAudience": "campaign_audience_id", "Contact": "contact_id",
    "WebSession": "session_id", "Lead": "lead_id", "Opportunity": "opportunity_id", "OpportunityEvent": "event_id",
    "Touchpoint": "touchpoint_id", "Order": "order_id", "CostRecord": "cost_record_id",
    "AttributionResult": "attribution_id", "ExceptionLog": "exception_id",
}
# child table, column, parent table, parent column, nullable?
FOREIGN_KEYS = [
    ("Campaign", "channel_id", "Channel", "channel_id", False),
    ("Creative", "campaign_id", "Campaign", "campaign_id", False),
    ("CampaignAudience", "campaign_id", "Campaign", "campaign_id", False),
    ("CampaignAudience", "audience_id", "Audience", "audience_id", False),
    ("WebSession", "contact_id", "Contact", "contact_id", True),
    ("WebSession", "traffic_channel_id", "Channel", "channel_id", False),
    ("Lead", "contact_id", "Contact", "contact_id", False),
    ("Lead", "session_id", "WebSession", "session_id", True),
    ("Opportunity", "contact_id", "Contact", "contact_id", False),
    ("Opportunity", "lead_id", "Lead", "lead_id", False),
    ("OpportunityEvent", "opportunity_id", "Opportunity", "opportunity_id", False),
    ("Touchpoint", "contact_id", "Contact", "contact_id", False),
    ("Touchpoint", "campaign_id", "Campaign", "campaign_id", False),
    ("Touchpoint", "channel_id", "Channel", "channel_id", False),
    ("Touchpoint", "creative_id", "Creative", "creative_id", True),
    ("Touchpoint", "reported_conversion_id", "Opportunity", "opportunity_id", True),
    ("Order", "opportunity_id", "Opportunity", "opportunity_id", False),
    ("CostRecord", "campaign_id", "Campaign", "campaign_id", False),
    ("AttributionResult", "opportunity_id", "Opportunity", "opportunity_id", False),
    ("AttributionResult", "campaign_id", "Campaign", "campaign_id", False),
    ("AttributionResult", "contact_id", "Contact", "contact_id", False),
]
ID_PATTERNS = {
    ("Campaign", "campaign_id"): r"^CMP-2026-\d{6}$", ("Contact", "contact_id"): r"^CON-2026-\d{6}$",
    ("WebSession", "session_id"): r"^SES-2026-\d{6}$", ("Lead", "lead_id"): r"^LEAD-2026-\d{6}$",
    ("Opportunity", "opportunity_id"): r"^OPP-2026-\d{6}$", ("Order", "order_id"): r"^ORD-2026-\d{6}$",
    ("CostRecord", "cost_record_id"): r"^COST-2026-\d{6}$", ("Touchpoint", "touchpoint_id"): r"^TOUCH-CON-2026-\d{6}-\d{6}$",
}
ENUMS = {
    ("Channel", "channel_name"): CHANNEL_NAMES, ("Campaign", "currency"): CURRENCIES, ("Campaign", "market"): MARKETS,
    ("Campaign", "status"): ["active", "completed"], ("Campaign", "objective"): ["awareness", "lead_generation", "conversion"],
    ("Contact", "consent_status"): ["granted", "denied", "unknown"], ("Contact", "company_size"): COMPANY_SIZES,
    ("Contact", "lifecycle_state"): ["prospect", "lead", "opportunity", "customer", "inactive"],
    ("WebSession", "identity_evidence"): SESSION_EVIDENCE, ("WebSession", "device"): DEVICES,
    ("Lead", "status"): ["new", "qualified", "converted", "disqualified"], ("Lead", "source_system"): LEAD_SOURCES,
    ("Opportunity", "stage"): OPP_STAGES, ("Opportunity", "currency"): CURRENCIES, ("Touchpoint", "touch_type"): TOUCH_TYPES,
    ("Order", "currency"): CURRENCIES, ("CostRecord", "currency"): CURRENCIES,
    ("AttributionResult", "model"): ["first_touch", "last_touch", "linear_multi_touch", "position_based", "time_decay"],
}


def validate_dataset(tables: dict[str, pd.DataFrame], exceptions: pd.DataFrame, ground_truth: pd.DataFrame | None = None) -> dict:
    checks: list[dict] = []

    def check(category: str, name: str, passed, details="") -> None:
        checks.append({"category": category, "check": name, "passed": bool(passed), "details": str(details)})

    ts = lambda s: pd.to_datetime(s)  # noqa: E731
    t = tables

    # 1. Primary keys ------------------------------------------------------------------
    for table, key in PRIMARY_KEYS.items():
        if table in t:
            df = t[table]
            check("primary_key", f"{table}.{key} unique & not null", df[key].notna().all() and not df[key].duplicated().any(),
                  f"rows={len(df)} duplicates={int(df[key].duplicated().sum())}")
    ca = t["CampaignAudience"]
    check("primary_key", "CampaignAudience (campaign_id, audience_id) unique", not ca.duplicated(["campaign_id", "audience_id"]).any())

    # 2. Foreign keys ------------------------------------------------------------------
    for child, col, parent, pcol, nullable in FOREIGN_KEYS:
        if child not in t:
            continue
        vals = t[child][col]
        nulls = int(vals.isna().sum())
        bad = int((~vals.dropna().isin(set(t[parent][pcol]))).sum())
        check("foreign_key", f"{child}.{col} -> {parent}.{pcol}", bad == 0 and (nullable or nulls == 0),
              f"orphans={bad} nulls={nulls} nullable={nullable}")

    # 3. ID formats & enums ------------------------------------------------------------
    for (table, col), pattern in ID_PATTERNS.items():
        bad = int((~t[table][col].astype(str).str.match(pattern)).sum())
        check("id_format", f"{table}.{col} matches {pattern}", bad == 0, f"violations={bad}")
    tp = t["Touchpoint"]
    check("id_format", "Touchpoint id embeds its contact_id", (tp["touchpoint_id"].str[6:21] == tp["contact_id"]).all())
    for (table, col), allowed in ENUMS.items():
        if table in t:
            bad = int((~t[table][col].dropna().isin(allowed)).sum())
            check("enum", f"{table}.{col} in allowed values", bad == 0, f"violations={bad}")
    check("enum", "Monetary amounts non-negative",
          (t["Opportunity"]["amount"] >= 0).all() and (t["Order"]["revenue_amount"] >= 0).all() and
          (t["CostRecord"]["amount"] >= 0).all() and (t["Campaign"]["budget_amount"] >= 0).all())

    # 4. Temporal ordering -------------------------------------------------------------
    camp = t["Campaign"].set_index("campaign_id")
    contact_created = t["Contact"].set_index("contact_id")["created_at"].map(pd.Timestamp)
    check("temporal", "Campaign end_at >= start_at", (ts(camp["end_at"]) >= ts(camp["start_at"])).all())
    tpt = ts(tp["occurred_at"])
    in_window = (tpt >= ts(tp["campaign_id"].map(camp["start_at"]))) & (tpt <= ts(tp["campaign_id"].map(camp["end_at"])))
    check("temporal", "Touchpoint inside its campaign flight", in_window.all(), f"violations={int((~in_window).sum())}")
    check("temporal", "Touchpoint after contact creation", (tpt >= tp["contact_id"].map(contact_created)).all())
    check("temporal", "Touchpoint channel equals campaign channel", (tp["channel_id"] == tp["campaign_id"].map(camp["channel_id"])).all())
    s = t["WebSession"]
    check("temporal", "Session ended_at >= started_at", (ts(s["ended_at"]) >= ts(s["started_at"])).all())
    ident = s[s["contact_id"].notna()]
    check("temporal", "Identified session after contact creation", (ts(ident["started_at"]) >= ident["contact_id"].map(contact_created)).all())
    lead = t["Lead"]
    check("temporal", "Lead after contact creation", (ts(lead["created_at"]) >= lead["contact_id"].map(contact_created)).all())
    ls = lead[lead["session_id"].notna()].merge(s[["session_id", "started_at"]], on="session_id")
    check("temporal", "Lead created after its session started", (ts(ls["created_at"]) >= ts(ls["started_at"])).all())
    opp = t["Opportunity"]
    ol = opp.merge(lead[["lead_id", "created_at"]], on="lead_id", suffixes=("", "_lead"))
    check("temporal", "Opportunity created after lead", (ts(ol["created_at"]) >= ts(ol["created_at_lead"])).all())
    closed = opp[opp["closed_at"].notna()]
    check("temporal", "Opportunity closed_at >= created_at", (ts(closed["closed_at"]) >= ts(closed["created_at"])).all())
    check("temporal", "Open stages have null closed_at, closed stages have closed_at",
          (opp["closed_at"].isna() == ~opp["stage"].isin(["closed_won", "closed_lost"])).all())
    oo = t["Order"].merge(opp[["opportunity_id", "closed_at"]], on="opportunity_id")
    check("temporal", "Order recognised on/after opportunity close", (ts(oo["recognized_at"]) >= ts(oo["closed_at"])).all())
    cr = t["CostRecord"]
    cin = (ts(cr["incurred_at"]) >= ts(cr["campaign_id"].map(camp["start_at"]))) & (ts(cr["incurred_at"]) <= ts(cr["campaign_id"].map(camp["end_at"])))
    check("temporal", "Cost incurred inside campaign flight", cin.all(), f"violations={int((~cin).sum())}")

    # 5. Lifecycle (append-only history) -----------------------------------------------
    ev = t["OpportunityEvent"].sort_values(["opportunity_id", "sequence"])
    bad_transitions = [(r.from_stage if isinstance(r.from_stage, str) else None, r.to_stage) for r in ev.itertuples(index=False)
                       if r.to_stage not in OPP_TRANSITIONS.get(r.from_stage if isinstance(r.from_stage, str) else None, set())]
    check("lifecycle", "Every stage transition is allowed", not bad_transitions, f"invalid={bad_transitions[:5]}")
    check("lifecycle", "History starts with a created event", ev.groupby("opportunity_id").head(1)["event_type"].eq("created").all())
    check("lifecycle", "Event sequence is chronological",
          not ts(ev["occurred_at"]).groupby(ev["opportunity_id"]).diff().lt(pd.Timedelta(0)).any())
    last = ev.groupby("opportunity_id").tail(1).set_index("opportunity_id")["to_stage"]
    check("lifecycle", "Latest event equals current opportunity stage", (opp.set_index("opportunity_id")["stage"] == last.reindex(opp["opportunity_id"]).values).all())
    check("lifecycle", "Every opportunity has history", set(opp["opportunity_id"]) == set(ev["opportunity_id"]))
    has_opp = lead["lead_id"].isin(opp["lead_id"])
    check("derived", "Lead status 'converted' iff it has an opportunity", (has_opp == lead["status"].eq("converted")).all())
    won_contacts = set(opp.loc[opp["stage"] == "closed_won", "contact_id"])
    check("derived", "Contact 'customer' iff it has a won opportunity",
          (t["Contact"]["contact_id"].isin(won_contacts) == t["Contact"]["lifecycle_state"].eq("customer")).all())
    check("derived", "Touch is_offline derived from touch type",
          (tp["is_offline"].astype(str).str.lower().isin(["true", "1"]) == tp["touch_type"].isin(["event_attendance", "booth_scan", "sales_call"])).all())

    # 6. Arithmetic reconciliation ------------------------------------------------------
    dup = excluded_ids(exceptions, "duplicate_revenue")
    vo = t["Order"][~t["Order"]["order_id"].isin(dup)].merge(opp[["opportunity_id", "amount", "currency", "stage"]], on="opportunity_id", suffixes=("", "_opp"))
    check("arithmetic", "Valid order revenue equals opportunity amount", np.allclose(vo["revenue_amount"], vo["amount"], atol=0.005))
    check("arithmetic", "Valid order currency equals opportunity currency", (vo["currency"] == vo["currency_opp"]).all())
    check("arithmetic", "Orders only for closed-won opportunities", vo["stage"].eq("closed_won").all())
    check("arithmetic", "One valid order per won opportunity",
          not vo["opportunity_id"].duplicated().any() and set(vo["opportunity_id"]) == set(opp.loc[opp["stage"] == "closed_won", "opportunity_id"]))
    if "AttributionResult" in t and len(t["AttributionResult"]):
        ar = t["AttributionResult"]
        rev = recognized_revenue(t, exceptions).set_index("opportunity_id")["revenue"]
        for model, g in ar.groupby("model"):
            got = g.groupby("opportunity_id")["credited_revenue"].sum()
            diff = (got - rev.reindex(got.index)).abs().max()
            check("arithmetic", f"{model}: credited revenue == recognised revenue per opportunity", diff <= 0.005, f"max_diff={diff:.4f} opps={len(got)}")
            shares = g.groupby("opportunity_id")["credit_share"].sum()
            check("arithmetic", f"{model}: credit shares sum to 1", np.allclose(shares, 1, atol=1e-4))
        conv = ts(ar["conversion_at"])
        check("arithmetic", "Attribution currency equals order currency",
              (ar["currency"] == ar["opportunity_id"].map(vo.drop_duplicates("opportunity_id").set_index("opportunity_id")["currency"])).all())
        check("temporal", "Attribution only for converted opportunities", conv.notna().all())

    # 7. Duplicates & identity ----------------------------------------------------------
    dup_touch = tp.duplicated(["contact_id", "campaign_id", "occurred_at", "touch_type"]).sum()
    check("duplicate", "No duplicate touch events", dup_touch == 0, f"duplicates={int(dup_touch)}")
    unlabelled_dup_inv = t["Order"][~t["Order"]["order_id"].isin(dup)]["external_invoice_id"].duplicated().sum()
    check("duplicate", "No unflagged duplicate invoices", unlabelled_dup_inv == 0)
    rejected = excluded_ids(exceptions, "anonymous_session_without_evidence")
    li = lead[lead["session_id"].notna() & ~lead["lead_id"].isin(rejected)].merge(s[["session_id", "contact_id", "identity_evidence"]], on="session_id", suffixes=("", "_s"))
    check("identity", "Session-linked leads match the session contact", (li["contact_id"] == li["contact_id_s"]).all())
    check("identity", "Session-linked leads have identity evidence", li["identity_evidence"].ne("none").all())

    # 8. Ground-truth separation & anomaly coverage ------------------------------------
    leaked = [f"{n}.{c}" for n, df in t.items() if n not in DERIVED_OUTPUTS
              for c in df.columns if c in {"issue_type", "ground_truth", "is_anomaly", "label", "expected_decision"}]
    check("ground_truth", "No anomaly labels in input (source) tables", not leaked, f"leaked={leaked}")
    check("ground_truth", "All five exception classes detected", set(ISSUE_TYPES) <= set(exceptions["issue_type"]))
    if ground_truth is not None:
        lookup = {"Order": ("Order", "order_id"), "Touchpoint": ("Touchpoint", "touchpoint_id"), "CostRecord": ("CostRecord", "cost_record_id"),
                  "Campaign": ("Campaign", "campaign_id"), "Lead": ("Lead", "lead_id")}
        missing = [r.record_id for r in ground_truth.itertuples(index=False) if r.record_id not in set(t[lookup[r.record_type][0]][lookup[r.record_type][1]])]
        check("ground_truth", "Every ground-truth record id exists in the input tables", not missing, f"missing={missing[:5]}")
        check("ground_truth", "All five anomaly classes labelled", set(ISSUE_TYPES) <= set(ground_truth["issue_type"]))

    by_cat: dict[str, dict] = {}
    for c in checks:
        d = by_cat.setdefault(c["category"], {"passed": 0, "failed": 0})
        d["passed" if c["passed"] else "failed"] += 1
    return {"passed": all(c["passed"] for c in checks), "total_checks": len(checks),
            "failed_checks": [c for c in checks if not c["passed"]], "by_category": by_cat, "checks": checks,
            "row_counts": {k: int(len(v)) for k, v in t.items()}}
