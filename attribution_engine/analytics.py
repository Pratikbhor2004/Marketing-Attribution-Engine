"""ROI, reconciliation, model comparison, chronological evaluation and identity-gap analytics.

All monetary comparisons across currencies use the FxRate reference table (monthly rate to USD).
Native-currency amounts are kept alongside so nothing is silently converted.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .attribution import MODELS, recognized_revenue
from .detection import excluded_ids, evaluate_detection
from .generator import fx_lookup


def _spearman(a: pd.Series, b: pd.Series) -> float:
    """Spearman rank correlation without SciPy (Pearson correlation of average ranks)."""
    return float(a.rank().corr(b.rank()))


def _r(x, n=2):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), n)


# --------------------------------------------------------------------------------- spend
def cost_ledger(tables: dict[str, pd.DataFrame], exceptions: pd.DataFrame) -> pd.DataFrame:
    cost = tables["CostRecord"].copy()
    cost["quarantined"] = cost["cost_record_id"].isin(excluded_ids(exceptions, "currency_mismatch"))
    cost["amount_usd"] = (cost["amount"] * fx_lookup(tables["FxRate"], cost["currency"], cost["incurred_at"])).round(2)
    return cost


def campaign_roi(tables: dict[str, pd.DataFrame], exceptions: pd.DataFrame, results: pd.DataFrame,
                 model: str = "linear_multi_touch") -> pd.DataFrame:
    """Campaign ROI in USD: (attributed revenue - valid spend) / valid spend.

    Missing spend is never treated as zero: ROI is null and roi_status explains why.
    """
    camp = tables["Campaign"][["campaign_id", "campaign_name", "channel_id", "market", "currency", "budget_amount", "status"]].copy()
    cost = cost_ledger(tables, exceptions)
    valid = cost[~cost["quarantined"]].groupby("campaign_id").agg(spend=("amount", "sum"), spend_usd=("amount_usd", "sum"),
                                                                   cost_records=("cost_record_id", "count"))
    quarantined = cost[cost["quarantined"]].groupby("campaign_id").agg(quarantined_cost_usd=("amount_usd", "sum"),
                                                                       quarantined_cost_records=("cost_record_id", "count"))
    r = results[results["model"] == model]
    rev = r.groupby("campaign_id").agg(attributed_revenue_usd=("credited_revenue_usd", "sum"),
                                       attributed_conversions=("credit_share", "sum"),
                                       credited_opportunities=("opportunity_id", "nunique"))
    native = r[r["currency"] == r["campaign_id"].map(camp.set_index("campaign_id")["currency"])]
    native = native.groupby("campaign_id")["credited_revenue"].sum().rename("attributed_revenue_native_same_currency")
    out = camp.join(valid, on="campaign_id").join(quarantined, on="campaign_id").join(rev, on="campaign_id").join(native, on="campaign_id")
    for c in ["attributed_revenue_usd", "attributed_conversions", "credited_opportunities", "quarantined_cost_usd",
              "quarantined_cost_records", "attributed_revenue_native_same_currency"]:
        out[c] = out[c].fillna(0)
    out["cost_records"] = out["cost_records"].fillna(0).astype(int)
    omitted = excluded_ids(exceptions, "campaign_cost_omitted")
    has_spend = out["spend_usd"].notna() & (out["spend_usd"] > 0)
    out["roas"] = (out["attributed_revenue_usd"] / out["spend_usd"]).where(has_spend).round(4)
    out["roi_pct"] = ((out["attributed_revenue_usd"] - out["spend_usd"]) / out["spend_usd"] * 100).where(has_spend).round(2)
    out["cost_per_conversion_usd"] = (out["spend_usd"] / out["attributed_conversions"]).where(has_spend & (out["attributed_conversions"] > 0)).round(2)
    out["roi_status"] = np.where(out["campaign_id"].isin(omitted) | ~has_spend, "unavailable_missing_cost",
                                 np.where(out["quarantined_cost_records"] > 0, "calculated_excluding_quarantined_cost", "calculated"))
    out["model"] = model
    for c in ["spend", "spend_usd", "attributed_revenue_usd", "quarantined_cost_usd", "attributed_conversions"]:
        out[c] = out[c].round(4 if c == "attributed_conversions" else 2)
    return out.reset_index(drop=True)


def channel_roi(roi: pd.DataFrame, channels: pd.DataFrame) -> pd.DataFrame:
    """Channel roll-up. ROI only over campaigns whose spend is known, so missing cost cannot inflate ROI."""
    known = roi[roi["roi_status"] != "unavailable_missing_cost"]
    g = known.groupby("channel_id").agg(spend_usd=("spend_usd", "sum"), attributed_revenue_usd=("attributed_revenue_usd", "sum"),
                                        attributed_conversions=("attributed_conversions", "sum"), campaigns=("campaign_id", "count"))
    excl = roi[roi["roi_status"] == "unavailable_missing_cost"].groupby("channel_id").agg(
        campaigns_missing_cost=("campaign_id", "count"), revenue_on_missing_cost_campaigns_usd=("attributed_revenue_usd", "sum"))
    out = channels[["channel_id", "channel_name", "channel_group", "is_paid"]].join(g, on="channel_id").join(excl, on="channel_id").fillna(0)
    out["roas"] = (out["attributed_revenue_usd"] / out["spend_usd"]).where(out["spend_usd"] > 0).round(4)
    out["roi_pct"] = ((out["attributed_revenue_usd"] - out["spend_usd"]) / out["spend_usd"] * 100).where(out["spend_usd"] > 0).round(2)
    total = out["attributed_revenue_usd"].sum() + out["revenue_on_missing_cost_campaigns_usd"].sum()
    out["revenue_share"] = ((out["attributed_revenue_usd"] + out["revenue_on_missing_cost_campaigns_usd"]) / total).round(6) if total else 0.0
    return out.round(2)


# ------------------------------------------------------------------------- reconciliation
def reconciliation(tables, exceptions, results, unattributed) -> dict:
    """Prove that money is conserved end to end: orders -> recognised -> attributed + unattributed."""
    orders = tables["Order"].copy()
    orders["dup"] = orders["order_id"].isin(excluded_ids(exceptions, "duplicate_revenue"))
    by_cur = {}
    rev = recognized_revenue(tables, exceptions)
    checks = []
    for cur, g in orders.groupby("currency"):
        recognised = round(float(g.loc[~g["dup"], "revenue_amount"].sum()), 2)
        dup = round(float(g.loc[g["dup"], "revenue_amount"].sum()), 2)
        un = round(float(unattributed.loc[unattributed["currency"] == cur, "revenue"].sum()), 2)
        per_model = {}
        for m in MODELS:
            att = round(float(results.loc[(results["model"] == m) & (results["currency"] == cur), "credited_revenue"].sum()), 2)
            diff = round(recognised - att - un, 2)
            per_model[m] = {"attributed": att, "difference": diff}
            checks.append(abs(diff) <= 0.01)
        by_cur[cur] = {"gross_order_revenue": round(recognised + dup, 2), "duplicate_revenue_excluded": dup,
                       "recognised_revenue": recognised, "unattributed_revenue": un, "models": per_model}
    cost = cost_ledger(tables, exceptions)
    spend = {"ledger_total_usd": _r(cost["amount_usd"].sum()), "valid_spend_usd": _r(cost.loc[~cost["quarantined"], "amount_usd"].sum()),
             "quarantined_spend_usd": _r(cost.loc[cost["quarantined"], "amount_usd"].sum()),
             "campaigns_missing_cost": len(excluded_ids(exceptions, "campaign_cost_omitted"))}
    usd = {m: _r(results.loc[results["model"] == m, "credited_revenue_usd"].sum()) for m in MODELS}
    return {"passed": bool(all(checks)), "by_currency": by_cur, "spend": spend,
            "recognised_opportunities": int(len(rev)), "unattributed_opportunities": int(len(unattributed)),
            "attributed_revenue_usd_by_model": usd, "unattributed_revenue_usd": _r(unattributed["revenue_usd"].sum())}


# ----------------------------------------------------------------------- model comparison
def model_comparison(tables, exceptions, results, channel_truth: pd.DataFrame | None = None) -> dict:
    channels = tables["Channel"]
    total = results.groupby("model")["credited_revenue_usd"].sum()
    share = (results.groupby(["model", "channel_id"])["credited_revenue_usd"].sum() / total).unstack("model").reindex(channels["channel_id"]).fillna(0)
    out = {"channel_revenue_share": {ch: {m: round(float(share.loc[ch, m]), 4) for m in share.columns} for ch in share.index}}
    if channel_truth is not None and len(channel_truth):
        truth = channel_truth.set_index("channel_id")["true_share"].reindex(share.index).fillna(0)
        out["channel_true_share"] = {k: round(float(v), 4) for k, v in truth.items()}
        out["distance_to_ground_truth"] = {
            m: {"total_variation_distance": round(float((share[m] - truth).abs().sum() / 2), 4),
                "mean_absolute_error": round(float((share[m] - truth).abs().mean()), 4)} for m in share.columns}
        out["best_model_vs_ground_truth"] = min(out["distance_to_ground_truth"], key=lambda m: out["distance_to_ground_truth"][m]["total_variation_distance"])
    camp = results.groupby(["campaign_id", "model"])["credited_revenue_usd"].sum().unstack("model").fillna(0)
    out["campaign_rank_correlation_spearman"] = {a: {b: round(_spearman(camp[a], camp[b]), 4) for b in camp.columns}
                                                 for a in camp.columns} if len(camp) > 2 else {}
    # How much campaign-level credit moves when you switch model (portfolio total stays equal).
    base = camp["linear_multi_touch"] if "linear_multi_touch" in camp else None
    if base is not None and base.sum():
        out["credit_shift_vs_linear"] = {m: round(float((camp[m] - base).abs().sum() / 2 / base.sum()), 4) for m in camp.columns}
    return out


# ------------------------------------------------------------------ chronological evaluation
def chronological_evaluation(tables, exceptions, results, cutoff: str) -> dict:
    """Split conversions and spend at `cutoff`; test whether each model's train-period channel x market
    ROAS ranking predicts the evaluation period (Spearman), and how stable credit shares are."""
    cut = pd.Timestamp(cutoff)
    opp = tables["Opportunity"]
    created = pd.to_datetime(opp["created_at"])
    cost = cost_ledger(tables, exceptions)
    cost = cost[~cost["quarantined"]].merge(tables["Campaign"][["campaign_id", "channel_id", "market"]], on="campaign_id")
    cost["period"] = np.where(pd.to_datetime(cost["incurred_at"]) < cut, "train", "evaluation")
    spend = cost.groupby(["period", "channel_id", "market"])["amount_usd"].sum()
    res = results.merge(tables["Campaign"][["campaign_id", "market"]], on="campaign_id")
    res["period"] = np.where(pd.to_datetime(res["conversion_at"]) < cut, "train", "evaluation")
    per_model = {}
    for m in sorted(res["model"].unique()):
        r = res[res["model"] == m].groupby(["period", "channel_id", "market"])["credited_revenue_usd"].sum()
        roas = (r / spend).dropna()
        if "train" not in roas.index.get_level_values(0) or "evaluation" not in roas.index.get_level_values(0):
            per_model[m] = {"note": "insufficient data on one side of the cutoff"}
            continue
        tr, ev = roas.loc["train"], roas.loc["evaluation"]
        common = tr.index.intersection(ev.index)
        share_tr = r.loc["train"].groupby("channel_id").sum() / r.loc["train"].sum()
        share_ev = r.loc["evaluation"].groupby("channel_id").sum() / r.loc["evaluation"].sum()
        share_ev = share_ev.reindex(share_tr.index).fillna(0)
        per_model[m] = {"segments_compared": int(len(common)),
                        "roas_rank_stability_spearman": _r(_spearman(tr.loc[common], ev.loc[common]), 4) if len(common) > 2 else None,
                        "channel_share_shift_tvd": _r((share_tr - share_ev).abs().sum() / 2, 4),
                        "train_attributed_usd": _r(r.loc["train"].sum()), "evaluation_attributed_usd": _r(r.loc["evaluation"].sum())}
    conv = pd.to_datetime(res.drop_duplicates("opportunity_id")["conversion_at"])
    return {"cutoff": cut.date().isoformat(),
            "opportunities_created": {"train": int((created < cut).sum()), "evaluation": int((created >= cut).sum())},
            "conversions": {"train": int((conv < cut).sum()), "evaluation": int((conv >= cut).sum())},
            "spend_usd": {"train": _r(spend.loc["train"].sum()) if "train" in spend.index else 0,
                          "evaluation": _r(spend.loc["evaluation"].sum()) if "evaluation" in spend.index else 0},
            "models": per_model}


# ------------------------------------------------------------------------- identity gaps
def identity_gaps(tables, exceptions, unattributed) -> dict:
    s, l, tp, opp = tables["WebSession"], tables["Lead"], tables["Touchpoint"], tables["Opportunity"]
    rejected = excluded_ids(exceptions, "anonymous_session_without_evidence")
    won = opp[opp["stage"] == "closed_won"]
    return {
        "sessions_total": int(len(s)),
        "anonymous_sessions": int(s["contact_id"].isna().sum()),
        "anonymous_session_share": _r(s["contact_id"].isna().mean(), 4),
        "sessions_by_identity_evidence": s["identity_evidence"].value_counts().to_dict(),
        "leads_total": int(len(l)),
        "leads_without_session": int(l["session_id"].isna().sum()),
        "leads_without_session_by_source": l.loc[l["session_id"].isna(), "source_system"].value_counts().to_dict(),
        "rejected_identity_links": int(len(rejected)),
        "rejected_identity_link_ids": sorted(rejected)[:50],
        "offline_touches": int(tp["is_offline"].astype(str).str.lower().eq("true").sum()),
        "touches_without_creative": int(tp["creative_id"].isna().sum()),
        "won_opportunities": int(len(won)),
        "won_opportunities_unattributed": int(len(unattributed)),
        "unattributed_revenue_usd": _r(unattributed["revenue_usd"].sum()),
        "unattributed_by_reason": unattributed["reason"].value_counts().to_dict(),
    }


def evaluation_summary(tables, exceptions, attribution, ground_truth: dict | None, cutoff: str) -> dict:
    gt = (ground_truth or {}).get("ground_truth")
    ch = (ground_truth or {}).get("channel_contribution")
    opp = tables["Opportunity"]
    return {
        "volumes": {k: int(len(v)) for k, v in tables.items()},
        "opportunity_stages": opp["stage"].value_counts().to_dict(),
        "win_rate_closed": _r((opp["stage"] == "closed_won").sum() / max(opp["stage"].isin(["closed_won", "closed_lost"]).sum(), 1), 4),
        "attribution_rows": int(len(attribution.results)),
        "exceptions_detected": exceptions["issue_type"].value_counts().to_dict(),
        "detection_vs_ground_truth": evaluate_detection(exceptions, gt) if gt is not None else None,
        "reconciliation": reconciliation(tables, exceptions, attribution.results, attribution.unattributed),
        "model_comparison": model_comparison(tables, exceptions, attribution.results, ch),
        "chronological_evaluation": chronological_evaluation(tables, exceptions, attribution.results, cutoff),
        "identity_gaps": identity_gaps(tables, exceptions, attribution.unattributed),
        "touch_eligibility": {k: int(v) for k, v in attribution.touch_audit.drop(columns=["opportunity_id"]).sum().items()},
    }
