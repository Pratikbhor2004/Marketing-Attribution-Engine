import numpy as np

from attribution_engine.analytics import campaign_roi
from attribution_engine.attribution import recognized_revenue, run_attribution
from attribution_engine.config import AttributionParams
from attribution_engine.detection import detect_exceptions, evaluate_detection
from attribution_engine.validation import validate_dataset


def test_detection_matches_ground_truth_exactly(result):
    score = evaluate_detection(result.exceptions, result.dataset.ground_truth["ground_truth"])
    assert score["overall"]["precision"] == 1.0 and score["overall"]["recall"] == 1.0


def test_detection_uses_only_input_tables(tables, result):
    assert detect_exceptions({k: v.copy() for k, v in tables.items()}).equals(result.exceptions)


def test_every_model_reconciles_to_recognised_revenue(result, tables):
    rev = recognized_revenue(tables, result.exceptions).set_index("opportunity_id")["revenue"]
    un = set(result.attribution.unattributed["opportunity_id"])
    for model, g in result.attribution.results.groupby("model"):
        got = g.groupby("opportunity_id")["credited_revenue"].sum()
        assert np.allclose(got, rev.loc[got.index], atol=0.005), model
        assert set(got.index) | un == set(rev.index), model


def test_duplicate_revenue_never_credited(result, tables):
    dups = set(result.exceptions.query("issue_type == 'duplicate_revenue'")["record_id"])
    orders = tables["Order"]
    res = result.attribution.results
    for opp in orders[orders["order_id"].isin(dups)]["opportunity_id"]:
        credited = res[(res["opportunity_id"] == opp) & (res["model"] == "linear_multi_touch")]["credited_revenue"].sum()
        valid = orders[(orders["opportunity_id"] == opp) & ~orders["order_id"].isin(dups)]["revenue_amount"].sum()
        assert abs(credited - valid) < 0.01 or credited == 0


def test_credited_campaigns_were_touched_before_conversion(result, tables):
    tp = tables["Touchpoint"][["contact_id", "campaign_id", "occurred_at"]]
    for model in ["first_touch", "last_touch", "time_decay"]:
        r = result.attribution.results.query("model == @model").merge(tp, on=["contact_id", "campaign_id"])
        ok = r.assign(before=r["occurred_at"] <= r["conversion_at"]).groupby("attribution_id")["before"].any()
        assert ok.all(), model


def test_lookback_and_offline_parameters_change_eligibility(result, tables):
    wide = run_attribution(tables, result.exceptions, AttributionParams(lookback_days=3650))
    narrow = run_attribution(tables, result.exceptions, AttributionParams(lookback_days=7))
    assert narrow.touch_audit["eligible"].sum() < wide.touch_audit["eligible"].sum()
    assert len(narrow.unattributed) >= len(wide.unattributed)
    online = run_attribution(tables, result.exceptions, AttributionParams(include_offline=False))
    assert online.touch_audit["offline_excluded"].sum() > 0


def test_models_disagree_on_campaigns_but_agree_on_total(result):
    pivot = result.attribution.results.pivot_table(index="campaign_id", columns="model", values="credited_revenue_usd",
                                                   aggfunc="sum", fill_value=0)
    assert (pivot["first_touch"] - pivot["last_touch"]).abs().sum() > 0
    assert pivot.sum().max() - pivot.sum().min() < 1.0


def test_missing_cost_gives_unavailable_roi_not_zero(result, tables):
    roi = campaign_roi(tables, result.exceptions, result.attribution.results)
    omitted = set(result.exceptions.query("issue_type == 'campaign_cost_omitted'")["record_id"])
    rows = roi[roi["campaign_id"].isin(omitted)]
    assert len(rows) == len(omitted) > 0
    assert rows["roi_status"].eq("unavailable_missing_cost").all() and rows["roi_pct"].isna().all()


def test_currency_mismatch_quarantined_and_roi_arithmetic(result, tables):
    roi = campaign_roi(tables, result.exceptions, result.attribution.results)
    mism = result.exceptions.query("issue_type == 'currency_mismatch'")["related_id"]
    assert roi[roi["campaign_id"].isin(mism)]["quarantined_cost_records"].gt(0).all()
    calc = roi[roi["roi_status"] != "unavailable_missing_cost"]
    expected = (calc["attributed_revenue_usd"] - calc["spend_usd"]) / calc["spend_usd"] * 100
    assert np.allclose(calc["roi_pct"], expected, atol=0.01)


def test_validation_passes_and_catches_corruption(result):
    assert result.validation["passed"], result.validation["failed_checks"]
    broken = dict(result.tables)
    broken["Touchpoint"] = broken["Touchpoint"].copy()
    broken["Touchpoint"].loc[0, "campaign_id"] = "CMP-2026-999999"            # orphan foreign key
    broken["Opportunity"] = broken["Opportunity"].copy()
    i = broken["Opportunity"].index[broken["Opportunity"]["stage"] == "closed_won"][0]
    broken["Opportunity"].loc[i, "closed_at"] = "2001-01-01T00:00:00"         # closes before it was created
    failed = {c["check"] for c in validate_dataset(broken, result.exceptions)["failed_checks"]}
    assert "Touchpoint.campaign_id -> Campaign.campaign_id" in failed
    assert "Opportunity closed_at >= created_at" in failed


def test_chronological_split_and_reconciliation_reported(result, cfg):
    ch = result.evaluation["chronological_evaluation"]
    assert ch["cutoff"] == cfg.evaluation_train_cutoff
    assert ch["opportunities_created"]["train"] > 0 and ch["opportunities_created"]["evaluation"] > 0
    assert result.evaluation["reconciliation"]["passed"]
