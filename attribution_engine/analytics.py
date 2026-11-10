from __future__ import annotations
import pandas as pd


def campaign_roi(tables: dict[str, pd.DataFrame], model: str = "linear_multi_touch") -> pd.DataFrame:
    """Campaign ROI using recognized, deduplicated Order revenue and valid same-currency spend."""
    campaigns = tables["Campaign"].copy()
    attrs = tables["AttributionResult"]
    attrs = attrs[attrs["model"] == model]
    revenue = attrs.groupby(["campaign_id", "currency"], as_index=False)["credited_revenue"].sum()
    # Keep non-reporting-currency revenue visible rather than silently dropping it.
    revenue_currency = attrs.merge(campaigns[["campaign_id", "currency"]], on="campaign_id", suffixes=("", "_campaign"))
    foreign_revenue = (revenue_currency[revenue_currency["currency"] != revenue_currency["currency_campaign"]]
                       .groupby("campaign_id", as_index=False)["credited_revenue"].sum()
                       .rename(columns={"credited_revenue": "unconverted_attributed_revenue"}))

    costs = tables["CostRecord"].merge(campaigns[["campaign_id", "currency"]], on="campaign_id", suffixes=("", "_campaign"))
    valid_costs = costs[(costs["currency"] == costs["currency_campaign"]) & costs["issue_type"].isna()]
    spend = valid_costs.groupby(["campaign_id", "currency"], as_index=False)["amount"].sum().rename(columns={"amount": "spend"})

    out = campaigns.merge(revenue, on=["campaign_id", "currency"], how="left").merge(spend, on=["campaign_id", "currency"], how="left")
    out = out.merge(foreign_revenue, on="campaign_id", how="left")
    out["unconverted_attributed_revenue"] = out["unconverted_attributed_revenue"].fillna(0.0)
    out["attributed_revenue"] = out["credited_revenue"].fillna(0.0)
    out["roi_pct"] = ((out["attributed_revenue"] - out["spend"]) / out["spend"] * 100).where(out["spend"].notna() & (out["spend"] > 0))
    out["roi_status"] = out["spend"].isna().map({True: "unavailable_missing_or_invalid_cost", False: "calculated"})
    out.loc[(out["spend"].notna()) & (out["unconverted_attributed_revenue"] > 0), "roi_status"] = "calculated_excluding_unconverted_revenue"
    return out.drop(columns=["credited_revenue"])


def exception_report(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    gt = tables["GroundTruth"]
    return pd.DataFrame([{"issue_type": r.get("issue_type"), "record_type": r.get("record_type"),
                          "record_id": r.get("record_id"), "expected_decision": r.get("expected_decision")}
                         for r in gt.to_dict("records")])


def evaluation_summary(tables: dict[str, pd.DataFrame], evaluation_train_cutoff: str = "2026-07-01") -> dict:
    opp = tables["Opportunity"]
    closed = opp[opp["stage"] == "closed_won"]
    attrs = tables["AttributionResult"]
    model_totals = attrs.groupby("model")["credited_revenue"].sum().to_dict() if len(attrs) else {}
    dates = pd.to_datetime(opp["created_at"])
    cutoff = pd.Timestamp(evaluation_train_cutoff)
    # Campaign allocations can differ while the portfolio total remains equal by design.
    model_campaign_totals = (attrs.groupby(["model", "campaign_id", "currency"])["credited_revenue"].sum().reset_index())
    return {
        "opportunities_total": int(len(opp)),
        "closed_won": int(len(closed)),
        "attribution_rows": int(len(attrs)),
        "model_attributed_revenue": {k: round(float(v), 2) for k, v in model_totals.items()},
        "model_campaign_allocation_rows": int(len(model_campaign_totals)),
        "chronological_split": {"train_before_cutoff": int((dates < cutoff).sum()),
                                "evaluation_on_or_after_cutoff": int((dates >= cutoff).sum()),
                                "cutoff": cutoff.date().isoformat()},
        "known_exception_count": int(len(tables["GroundTruth"])),
    }
