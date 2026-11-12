import re

import pandas as pd

from attribution_engine.config import DatasetConfig
from attribution_engine.generator import generate_dataset
from attribution_engine.pipeline import table_digest


def test_starter_volumes_match_config(tables, result, cfg):
    assert len(tables["Campaign"]) == cfg.campaigns
    assert len(tables["Contact"]) == cfg.contacts
    assert len(tables["Opportunity"]) == cfg.opportunities
    assert len(tables["WebSession"]) == cfg.sessions
    injected = (result.dataset.ground_truth["ground_truth"]["record_type"] == "Touchpoint").sum()
    assert len(tables["Touchpoint"]) == cfg.touchpoints + injected      # baseline volume + labelled extras


def test_id_conventions(tables):
    assert tables["Campaign"]["campaign_id"].iloc[0] == "CMP-2026-000001"
    assert tables["Campaign"]["campaign_id"].str.match(r"^CMP-2026-\d{6}$").all()
    assert tables["WebSession"]["session_id"].str.match(r"^SES-2026-\d{6}$").all()
    assert tables["Lead"]["lead_id"].str.match(r"^LEAD-2026-\d{6}$").all()
    assert tables["Opportunity"]["opportunity_id"].str.match(r"^OPP-2026-\d{6}$").all()
    tp = tables["Touchpoint"]
    assert all(re.fullmatch(rf"TOUCH-{c}-\d{{6}}", t) for t, c in zip(tp["touchpoint_id"], tp["contact_id"]))


def test_same_seed_is_reproducible_and_other_seed_differs():
    a = generate_dataset(DatasetConfig.starter(7))
    b = generate_dataset(DatasetConfig.starter(7))
    c = generate_dataset(DatasetConfig.starter(8))
    for name in a.tables:
        assert table_digest(a.tables[name]) == table_digest(b.tables[name]), name
    assert table_digest(a.tables["Touchpoint"]) != table_digest(c.tables["Touchpoint"])


def test_no_labels_leak_into_inputs(tables):
    for name, df in tables.items():
        assert not {"issue_type", "expected_decision", "ground_truth"} & set(df.columns), name


def test_ground_truth_covers_all_five_classes(result):
    gt = result.dataset.ground_truth["ground_truth"]
    assert set(gt["issue_type"]) == {"duplicate_revenue", "post_conversion_touch", "currency_mismatch",
                                     "campaign_cost_omitted", "anonymous_session_without_evidence"}
    assert gt["ground_truth_id"].is_unique


def test_coverage_long_windows_offline_missing_ids_multi_campaign(tables):
    tp = tables["Touchpoint"]
    assert tp["is_offline"].mean() > 0.05                                   # offline touches
    assert tables["WebSession"]["contact_id"].isna().any()                  # anonymous sessions
    assert tables["Lead"]["session_id"].isna().any()                        # leads without a session id
    assert (tp.groupby("contact_id")["campaign_id"].nunique() > 1).any()    # multi-campaign journeys
    opp = tables["Opportunity"].dropna(subset=["closed_at"])
    cycle = (pd.to_datetime(opp["closed_at"]) - pd.to_datetime(opp["created_at"])).dt.days
    assert cycle.max() > 120                                                # long conversion windows


def test_outcomes_follow_rule_not_coin_flip(tables):
    """Won deals had more pre-close exposure than lost deals: the outcome is derived from touches."""
    opp = tables["Opportunity"]
    tp = tables["Touchpoint"]
    closed = opp[opp["stage"].isin(["closed_won", "closed_lost"])].merge(tp[["contact_id", "occurred_at"]], on="contact_id")
    closed = closed[closed["occurred_at"] <= closed["closed_at"]]
    exposure = closed.groupby(["opportunity_id", "stage"]).size().reset_index(name="n").groupby("stage")["n"].mean()
    assert exposure["closed_won"] > exposure["closed_lost"]
