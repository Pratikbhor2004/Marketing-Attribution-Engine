from attribution_engine.config import DatasetConfig
from attribution_engine.generator import generate_dataset, validate_dataset
from attribution_engine.analytics import campaign_roi, evaluation_summary


def test_seed_reproducibility():
    cfg = DatasetConfig.starter(12345)
    a = generate_dataset(cfg)
    b = generate_dataset(cfg)
    for table in a:
        assert a[table].equals(b[table]), table


def test_validation_and_expected_volumes():
    tables = generate_dataset(DatasetConfig.starter())
    report = validate_dataset(tables)
    assert report["passed"], report
    assert len(tables["Campaign"]) == 20
    assert len(tables["Contact"]) == 500
    assert len(tables["Touchpoint"]) >= 2_000
    assert "OpportunityEvent" in tables


def test_roi_missing_cost_and_foreign_currency_are_visible():
    tables = generate_dataset(DatasetConfig.starter())
    roi = campaign_roi(tables)
    assert "roi_status" in roi
    assert roi["roi_status"].isin([
        "calculated", "calculated_excluding_unconverted_revenue", "unavailable_missing_or_invalid_cost"
    ]).all()
    omitted_campaign = tables["GroundTruth"].loc[
        tables["GroundTruth"]["issue_type"] == "campaign_cost_omitted", "record_id"
    ].iloc[0]
    row = roi.loc[roi["campaign_id"] == omitted_campaign].iloc[0]
    assert row["roi_status"] == "unavailable_missing_or_invalid_cost"


def test_anomaly_labels_are_separate():
    tables = generate_dataset(DatasetConfig.starter())
    labels = set(tables["GroundTruth"]["issue_type"])
    assert {"duplicate_revenue", "post_conversion_touch", "currency_mismatch",
            "campaign_cost_omitted", "anonymous_session_without_evidence"} <= labels


def test_contact_relationships_and_revenue_reconciliation():
    tables = generate_dataset(DatasetConfig.starter())
    report = validate_dataset(tables)
    assert report["passed"], [c for c in report["checks"] if not c["passed"]]
    attrs = tables["AttributionResult"]
    pivot = attrs.pivot_table(index="campaign_id", columns="model", values="credited_revenue", aggfunc="sum", fill_value=0)
    assert (pivot["first_touch"] - pivot["last_touch"]).abs().sum() > 0
    for model in ["first_touch", "last_touch", "linear_multi_touch"]:
        total = attrs.loc[attrs["model"] == model, "credited_revenue"].sum()
        assert total > 0


def test_evaluation_cutoff_is_configurable():
    cfg = DatasetConfig.starter()
    tables = generate_dataset(cfg)
    summary = evaluation_summary(tables, "2026-01-01")
    assert summary["chronological_split"]["cutoff"] == "2026-01-01"
