"""Rule-based exception detection.

The detectors look only at input tables (never at ground truth). Each detected exception
is persisted as an explicit record with the rule that fired and the decision the engine
applied, so every exclusion downstream is traceable. `evaluate_detection` then scores the
detectors against the separate ground-truth labels.
"""
from __future__ import annotations

import pandas as pd

ISSUE_TYPES = ["duplicate_revenue", "post_conversion_touch", "currency_mismatch",
               "campaign_cost_omitted", "anonymous_session_without_evidence"]

RULES = {
    "duplicate_revenue": ("R-REV-01", "Order shares an external_invoice_id with an earlier order, or pushes cumulative "
                                      "order revenue for its opportunity above the opportunity amount.",
                          "exclude_from_recognized_revenue"),
    "post_conversion_touch": ("R-TCH-01", "Touchpoint carries a platform conversion claim but occurred after the claimed "
                                          "opportunity closed (or the opportunity never closed won).",
                              "exclude_from_attribution"),
    "currency_mismatch": ("R-CST-01", "Cost record currency differs from the campaign's contracted currency.",
                          "quarantine_cost_from_roi"),
    "campaign_cost_omitted": ("R-CST-02", "Campaign has delivered touchpoints but no valid cost records.",
                              "roi_unavailable_missing_cost"),
    "anonymous_session_without_evidence": ("R-IDN-01", "Lead links to a web session that has no identity evidence, no "
                                                       "resolved contact, or a different contact.",
                                           "reject_identity_link"),
}

COLUMNS = ["exception_id", "issue_type", "rule_id", "record_type", "record_id", "related_id", "detail", "decision"]


def _rows(issue: str, record_type: str, df: pd.DataFrame, id_col: str, related_col: str | None, detail) -> list[dict]:
    rule_id, _, decision = RULES[issue]
    out = []
    for r in df.to_dict("records"):
        out.append({"issue_type": issue, "rule_id": rule_id, "record_type": record_type, "record_id": r[id_col],
                    "related_id": r.get(related_col) if related_col else None,
                    "detail": detail(r) if callable(detail) else detail, "decision": decision})
    return out


def detect_duplicate_revenue(t: dict[str, pd.DataFrame]) -> list[dict]:
    orders = t["Order"].copy()
    orders["_ts"] = pd.to_datetime(orders["recognized_at"])
    orders = orders.sort_values(["_ts", "order_id"], kind="mergesort")
    first_by_invoice = orders.drop_duplicates("external_invoice_id").set_index("external_invoice_id")["order_id"]
    dup_invoice = orders[orders.duplicated("external_invoice_id", keep="first")].copy()
    dup_invoice["first_order"] = dup_invoice["external_invoice_id"].map(first_by_invoice)
    rows = _rows("duplicate_revenue", "Order", dup_invoice, "order_id", "first_order",
                 lambda r: f"invoice {r['external_invoice_id']} already recognised on {r['first_order']}")
    rest = orders[~orders["order_id"].isin(dup_invoice["order_id"])]
    rest = rest.merge(t["Opportunity"][["opportunity_id", "amount"]], on="opportunity_id", how="left")
    rest["cum"] = rest.groupby("opportunity_id")["revenue_amount"].cumsum()
    over = rest[rest["cum"] > rest["amount"] + 0.01].copy()
    first_by_opp = rest.drop_duplicates("opportunity_id").set_index("opportunity_id")["order_id"]
    over["first_order"] = over["opportunity_id"].map(first_by_opp)
    rows += _rows("duplicate_revenue", "Order", over, "order_id", "first_order",
                  lambda r: f"cumulative revenue {r['cum']:.2f} exceeds opportunity amount {r['amount']:.2f}")
    return rows


def detect_post_conversion_touch(t: dict[str, pd.DataFrame]) -> list[dict]:
    tp = t["Touchpoint"]
    claimed = tp[tp["reported_conversion_id"].notna()].merge(
        t["Opportunity"][["opportunity_id", "closed_at", "stage"]], left_on="reported_conversion_id",
        right_on="opportunity_id", how="left")
    bad = claimed[(claimed["stage"] != "closed_won") | claimed["closed_at"].isna() |
                  (pd.to_datetime(claimed["occurred_at"]) > pd.to_datetime(claimed["closed_at"]))]
    return _rows("post_conversion_touch", "Touchpoint", bad, "touchpoint_id", "reported_conversion_id",
                 lambda r: f"touch at {r['occurred_at']} claims conversion closed at {r['closed_at']}")


def detect_currency_mismatch(t: dict[str, pd.DataFrame]) -> list[dict]:
    c = t["CostRecord"].merge(t["Campaign"][["campaign_id", "currency"]], on="campaign_id", suffixes=("", "_campaign"))
    bad = c[c["currency"] != c["currency_campaign"]]
    return _rows("currency_mismatch", "CostRecord", bad, "cost_record_id", "campaign_id",
                 lambda r: f"cost in {r['currency']}, campaign contracted in {r['currency_campaign']}")


def detect_campaign_cost_omitted(t: dict[str, pd.DataFrame]) -> list[dict]:
    c = t["CostRecord"].merge(t["Campaign"][["campaign_id", "currency"]], on="campaign_id", suffixes=("", "_campaign"))
    valid = set(c.loc[c["currency"] == c["currency_campaign"], "campaign_id"])
    touched = t["Touchpoint"].groupby("campaign_id").size().rename("touches")
    camp = t["Campaign"].merge(touched, left_on="campaign_id", right_index=True, how="inner")
    bad = camp[~camp["campaign_id"].isin(valid)]
    return _rows("campaign_cost_omitted", "Campaign", bad, "campaign_id", None,
                 lambda r: f"{int(r['touches'])} touchpoints delivered, 0 valid cost records")


def detect_anonymous_identity_link(t: dict[str, pd.DataFrame]) -> list[dict]:
    leads = t["Lead"][t["Lead"]["session_id"].notna()].merge(
        t["WebSession"][["session_id", "contact_id", "identity_evidence"]], on="session_id", how="left",
        suffixes=("", "_session"))
    bad = leads[(leads["identity_evidence"].fillna("none") == "none") | leads["contact_id_session"].isna() |
                (leads["contact_id_session"] != leads["contact_id"])]
    return _rows("anonymous_session_without_evidence", "Lead", bad, "lead_id", "session_id",
                 lambda r: f"session evidence={r['identity_evidence']}, session contact={r['contact_id_session']}")


DETECTORS = [detect_duplicate_revenue, detect_post_conversion_touch, detect_currency_mismatch,
             detect_campaign_cost_omitted, detect_anonymous_identity_link]


def detect_exceptions(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows = [r for fn in DETECTORS for r in fn(tables)]
    df = pd.DataFrame(rows, columns=[c for c in COLUMNS if c != "exception_id"])
    df.insert(0, "exception_id", [f"EXC-2026-{i:06d}" for i in range(1, len(df) + 1)])
    return df


def excluded_ids(exceptions: pd.DataFrame, issue_type: str) -> set[str]:
    return set(exceptions.loc[exceptions["issue_type"] == issue_type, "record_id"])


def evaluate_detection(exceptions: pd.DataFrame, ground_truth: pd.DataFrame) -> dict:
    """Precision / recall / F1 of detected exceptions against ground-truth labels."""
    per_type = {}
    tp_all = fp_all = fn_all = 0
    for issue in ISSUE_TYPES:
        detected = excluded_ids(exceptions, issue)
        truth = set(ground_truth.loc[ground_truth["issue_type"] == issue, "record_id"])
        tp, fp, fn = len(detected & truth), len(detected - truth), len(truth - detected)
        tp_all, fp_all, fn_all = tp_all + tp, fp_all + fp, fn_all + fn
        precision = tp / (tp + fp) if tp + fp else 1.0
        recall = tp / (tp + fn) if tp + fn else 1.0
        per_type[issue] = {"labelled": len(truth), "detected": len(detected), "true_positive": tp,
                           "false_positive": fp, "false_negative": fn, "precision": round(precision, 4),
                           "recall": round(recall, 4),
                           "f1": round(2 * precision * recall / (precision + recall), 4) if precision + recall else 0.0,
                           "false_positive_ids": sorted(detected - truth)[:20], "missed_ids": sorted(truth - detected)[:20]}
    p = tp_all / (tp_all + fp_all) if tp_all + fp_all else 1.0
    r = tp_all / (tp_all + fn_all) if tp_all + fn_all else 1.0
    return {"overall": {"precision": round(p, 4), "recall": round(r, 4), "f1": round(2 * p * r / (p + r), 4) if p + r else 0.0,
                        "true_positive": tp_all, "false_positive": fp_all, "false_negative": fn_all},
            "by_issue_type": per_type}
