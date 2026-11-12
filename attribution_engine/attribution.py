"""Attribution models.

Eligibility (applied identically to every model, see docs/attribution_rules.md):
  E1  touch.contact_id == opportunity.contact_id
  E2  touch.occurred_at <= conversion_at  (conversion_at = opportunity.closed_at of a closed-won deal)
  E3  touch.occurred_at >= conversion_at - lookback_days
  E4  touch.occurred_at >  the same contact's previous conversion (touches are not re-credited)
  E5  touch is not a detected post-conversion exception
  E6  offline touches are included unless include_offline=False

Revenue source of truth: Order rows that are not detected as duplicate revenue.
Credit is allocated in integer cents with a deterministic largest-remainder rule, so every model
reconciles exactly to recognised order revenue per opportunity.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
import numpy as np
import pandas as pd

from .config import AttributionParams
from .detection import excluded_ids
from .generator import fx_lookup

MODELS = ["first_touch", "last_touch", "linear_multi_touch", "position_based", "time_decay"]
MODEL_CODE = {"first_touch": "FT", "last_touch": "LT", "linear_multi_touch": "LIN", "position_based": "POS", "time_decay": "TD"}
RESULT_COLUMNS = ["attribution_id", "run_id", "model", "opportunity_id", "contact_id", "campaign_id", "channel_id",
                  "conversion_at", "credit_share", "credited_revenue", "currency", "credited_revenue_usd",
                  "eligible_touch_count", "campaign_touch_count"]


@dataclass
class AttributionOutput:
    results: pd.DataFrame          # AttributionResult rows
    unattributed: pd.DataFrame     # recognised revenue that no eligible touch could explain
    touch_audit: pd.DataFrame      # per-opportunity eligibility accounting
    params: dict


def recognized_revenue(tables: dict[str, pd.DataFrame], exceptions: pd.DataFrame) -> pd.DataFrame:
    """Valid order revenue per closed-won opportunity, with conversion timestamp."""
    dup = excluded_ids(exceptions, "duplicate_revenue")
    orders = tables["Order"][~tables["Order"]["order_id"].isin(dup)]
    rev = orders.groupby(["opportunity_id", "currency"], as_index=False).agg(
        revenue=("revenue_amount", "sum"), order_count=("order_id", "count"))
    opp = tables["Opportunity"]
    rev = rev.merge(opp[opp["stage"] == "closed_won"][["opportunity_id", "contact_id", "closed_at"]], on="opportunity_id")
    rev = rev.rename(columns={"closed_at": "conversion_at"})
    rev["conversion_ts"] = pd.to_datetime(rev["conversion_at"])
    rev["revenue"] = rev["revenue"].round(2)
    return rev


def _allocate_cents(weights: pd.DataFrame, cents_col: str) -> pd.Series:
    """Largest-remainder allocation of integer cents per opportunity (ties by campaign_id)."""
    w = weights
    total_w = w.groupby("opportunity_id")["w"].transform("sum")
    exact = w[cents_col] * w["w"] / total_w
    base = np.floor(exact).astype("int64")
    frac = exact - base
    remainder = (w[cents_col] - base.groupby(w["opportunity_id"]).transform("sum")).astype("int64")
    order = pd.DataFrame({"opp": w["opportunity_id"], "frac": -frac, "cid": w["campaign_id"]})
    rank = order.sort_values(["opp", "frac", "cid"], kind="mergesort").groupby("opp").cumcount()
    rank = rank.reindex(w.index)
    return base + (rank < remainder).astype("int64")


def run_attribution(tables: dict[str, pd.DataFrame], exceptions: pd.DataFrame,
                    params: AttributionParams | None = None, run_id: str = "RUN-BASELINE",
                    models: list[str] | None = None) -> AttributionOutput:
    params = params or AttributionParams()
    params.validate()
    models = models or MODELS
    unknown = set(models) - set(MODELS)
    if unknown:
        raise ValueError(f"Unknown models: {sorted(unknown)}")

    rev = recognized_revenue(tables, exceptions)
    tp = tables["Touchpoint"][["touchpoint_id", "contact_id", "campaign_id", "channel_id", "occurred_at", "is_offline"]].copy()
    tp["ts"] = pd.to_datetime(tp["occurred_at"])
    flagged = excluded_ids(exceptions, "post_conversion_touch")
    tp["flagged"] = tp["touchpoint_id"].isin(flagged)
    tp["is_offline"] = tp["is_offline"].astype(str).str.lower().isin(["true", "1"])

    # Previous conversion per contact (E4): touches before it were already credited.
    conv = rev[["opportunity_id", "contact_id", "conversion_ts"]].sort_values(["contact_id", "conversion_ts", "opportunity_id"])
    conv["prev_conversion_ts"] = conv.groupby("contact_id")["conversion_ts"].shift(1)
    rev = rev.merge(conv[["opportunity_id", "prev_conversion_ts"]], on="opportunity_id")

    j = rev[["opportunity_id", "contact_id", "conversion_ts", "prev_conversion_ts"]].merge(tp, on="contact_id", how="inner")
    lookback = pd.Timedelta(days=params.lookback_days)
    j["reason"] = "eligible"
    j.loc[j["ts"] > j["conversion_ts"], "reason"] = "after_conversion"
    j.loc[(j["reason"] == "eligible") & (j["ts"] < j["conversion_ts"] - lookback), "reason"] = "outside_lookback"
    j.loc[(j["reason"] == "eligible") & j["prev_conversion_ts"].notna() & (j["ts"] <= j["prev_conversion_ts"]), "reason"] = "before_previous_conversion"
    j.loc[(j["reason"] == "eligible") & j["flagged"], "reason"] = "flagged_exception"
    if not params.include_offline:
        j.loc[(j["reason"] == "eligible") & j["is_offline"], "reason"] = "offline_excluded"

    audit = j.pivot_table(index="opportunity_id", columns="reason", values="touchpoint_id", aggfunc="count", fill_value=0)
    for col in ["eligible", "after_conversion", "outside_lookback", "before_previous_conversion", "flagged_exception", "offline_excluded"]:
        if col not in audit:
            audit[col] = 0
    audit = rev[["opportunity_id"]].merge(audit.reset_index(), on="opportunity_id", how="left").fillna(0)
    audit.iloc[:, 1:] = audit.iloc[:, 1:].astype(int)

    e = j[j["reason"] == "eligible"].sort_values(["opportunity_id", "ts", "touchpoint_id"], kind="mergesort").copy()
    e["pos"] = e.groupby("opportunity_id").cumcount()
    e["n"] = e.groupby("opportunity_id")["touchpoint_id"].transform("count")
    age_days = (e["conversion_ts"] - e["ts"]).dt.total_seconds() / 86400

    weights = {
        "first_touch": (e["pos"] == 0).astype(float),
        "last_touch": (e["pos"] == e["n"] - 1).astype(float),
        "linear_multi_touch": pd.Series(1.0, index=e.index),
        "time_decay": np.power(0.5, age_days / params.time_decay_half_life_days),
    }
    f, l = params.position_first_weight, params.position_last_weight
    middle = (1 - f - l) / (e["n"] - 2).clip(lower=1)
    pos = np.where(e["n"] == 1, 1.0,
                   np.where(e["n"] == 2, np.where(e["pos"] == 0, f, l) / (f + l if f + l else 1),
                            np.where(e["pos"] == 0, f, np.where(e["pos"] == e["n"] - 1, l, middle))))
    weights["position_based"] = pd.Series(pos, index=e.index)

    rev_idx = rev.set_index("opportunity_id")
    fx_by_opp = pd.Series(fx_lookup(tables["FxRate"], rev["currency"], rev["conversion_ts"]), index=rev["opportunity_id"])
    channel_by_campaign = tables["Campaign"].set_index("campaign_id")["channel_id"]
    out = []
    for model in models:
        e["w"] = weights[model].to_numpy()
        g = e.groupby(["opportunity_id", "campaign_id"], as_index=False).agg(w=("w", "sum"), campaign_touch_count=("touchpoint_id", "count"))
        g = g[g["w"] > 0].reset_index(drop=True)
        g["cents"] = (g["opportunity_id"].map(rev_idx["revenue"]) * 100).round().astype("int64")
        g["alloc"] = _allocate_cents(g, "cents")
        g["credited_revenue"] = g["alloc"] / 100
        g["credit_share"] = (g["w"] / g.groupby("opportunity_id")["w"].transform("sum")).round(6)
        g["model"] = model
        out.append(g)
    res = pd.concat(out, ignore_index=True) if out else pd.DataFrame()
    if len(res):
        res["run_id"] = run_id
        res["contact_id"] = res["opportunity_id"].map(rev_idx["contact_id"])
        res["conversion_at"] = res["opportunity_id"].map(rev_idx["conversion_at"])
        res["currency"] = res["opportunity_id"].map(rev_idx["currency"])
        res["credited_revenue_usd"] = (res["credited_revenue"] * res["opportunity_id"].map(fx_by_opp)).round(2)
        res["channel_id"] = res["campaign_id"].map(channel_by_campaign)
        res["eligible_touch_count"] = res["opportunity_id"].map(e.groupby("opportunity_id").size()).astype(int)
        suffix = "" if run_id == "RUN-BASELINE" else f"-{run_id}"
        res["attribution_id"] = ("ATR-" + res["opportunity_id"] + "-" + res["model"].map(MODEL_CODE) + "-" + res["campaign_id"] + suffix)
        res = res[RESULT_COLUMNS].sort_values(["model", "opportunity_id", "campaign_id"], kind="mergesort").reset_index(drop=True)
    else:
        res = pd.DataFrame(columns=RESULT_COLUMNS)

    attributed = set(e["opportunity_id"])
    un = rev[~rev["opportunity_id"].isin(attributed)].merge(audit, on="opportunity_id", how="left")
    has_touch = set(j["opportunity_id"])
    un["reason"] = np.where(~un["opportunity_id"].isin(has_touch), "no_touchpoints_for_contact",
                            np.where(un["outside_lookback"] > 0, "all_touches_outside_lookback_or_ineligible",
                                     "only_post_conversion_or_ineligible_touches"))
    un["revenue_usd"] = (un["revenue"] * un["opportunity_id"].map(fx_by_opp)).round(2)
    un = un[["opportunity_id", "contact_id", "conversion_at", "revenue", "currency", "revenue_usd", "reason"]].reset_index(drop=True)
    return AttributionOutput(results=res, unattributed=un, touch_audit=audit, params=asdict(params) | {"run_id": run_id, "models": models})
