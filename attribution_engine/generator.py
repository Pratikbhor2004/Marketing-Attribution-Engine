from __future__ import annotations
from dataclasses import asdict
from pathlib import Path
import hashlib
import json
import sqlite3
import numpy as np
import pandas as pd

from .config import DatasetConfig

CHANNELS = ["Paid Search", "Paid Social", "Email", "Organic Search", "Affiliate", "Display", "Events", "Partner"]
CURRENCIES = ["INR", "USD", "EUR"]
DEVICES = ["mobile", "desktop", "tablet"]
STATUSES = ["new", "qualified", "converted", "disqualified"]


def _ids(prefix: str, n: int, year: int = 2026) -> list[str]:
    return [f"{prefix}-{year}-{i:06d}" for i in range(1, n + 1)]


def _date_strings(values) -> list[str]:
    return pd.to_datetime(values).strftime("%Y-%m-%dT%H:%M:%S").tolist()


def _cent_allocations(total: float, weights: pd.Series) -> dict[str, float]:
    """Allocate integer cents proportionally; the allocations sum exactly to total."""
    cents = int(round(float(total) * 100))
    counts = weights.astype(int)
    denominator = int(counts.sum())
    if denominator <= 0:
        return {}
    raw = {str(k): cents * int(v) // denominator for k, v in counts.items()}
    remainder = cents - sum(raw.values())
    # Stable deterministic remainder assignment.
    for key in sorted(raw)[:remainder]:
        raw[key] += 1
    return {k: v / 100 for k, v in raw.items()}


def generate_dataset(config: DatasetConfig) -> dict[str, pd.DataFrame]:
    """Generate deterministic source, lifecycle, attribution and labelled exception tables."""
    rng = np.random.default_rng(config.seed)
    start, end = pd.Timestamp(config.start_date), pd.Timestamp(config.end_date)
    days = max((end - start).days, 1)

    channel_df = pd.DataFrame({
        "channel_id": [f"CH-{i:03d}" for i in range(1, len(CHANNELS) + 1)],
        "channel_name": CHANNELS,
        "channel_group": ["digital", "digital", "digital", "digital", "digital", "digital", "offline", "partner"],
        "is_paid": [True, True, False, False, True, True, False, False],
    })

    n_campaigns = config.campaigns
    campaign_ids = _ids("CMP", n_campaigns)
    campaign_channels = rng.integers(0, len(CHANNELS), n_campaigns)
    currencies = rng.choice(CURRENCIES, n_campaigns, p=[0.82, 0.12, 0.06])
    budgets = np.round(rng.uniform(20_000, 2_000_000, n_campaigns), 2)
    campaign_starts = start + pd.to_timedelta(rng.integers(0, max(days - 60, 1), n_campaigns), unit="D")
    campaign_ends = campaign_starts + pd.to_timedelta(rng.integers(30, 180, n_campaigns), unit="D")
    creative_ids = [f"CR-{i:06d}" for i in range(1, n_campaigns + 1)]
    audience_ids = [f"AUD-{i:04d}" for i in range(1, 26)]
    campaign_df = pd.DataFrame({
        "campaign_id": campaign_ids,
        "campaign_name": [f"{CHANNELS[campaign_channels[i]]} Campaign {i+1:04d}" for i in range(n_campaigns)],
        "channel_id": [f"CH-{x+1:03d}" for x in campaign_channels],
        "creative_id": creative_ids,
        "audience_id": [audience_ids[i % len(audience_ids)] for i in range(n_campaigns)],
        "budget_amount": budgets,
        "currency": currencies,
        "start_at": _date_strings(campaign_starts),
        "end_at": _date_strings(campaign_ends),
        "status": rng.choice(["active", "completed", "paused"], n_campaigns, p=[0.35, 0.55, 0.10]),
    })
    creative_df = pd.DataFrame({
        "creative_id": creative_ids,
        "creative_name": [f"Creative {i:04d}" for i in range(1, n_campaigns + 1)],
        "format": rng.choice(["image", "video", "text", "landing_page"], n_campaigns),
        "version": rng.choice(["A", "B", "C"], n_campaigns),
    })
    audience_df = pd.DataFrame({
        "audience_id": audience_ids,
        "audience_name": [f"Audience Segment {i:02d}" for i in range(1, 26)],
        "segment_type": rng.choice(["prospect", "retargeting", "lookalike", "customer"], 25),
        "effective_from": start.strftime("%Y-%m-%d"),
        "effective_to": end.strftime("%Y-%m-%d"),
    })

    n_contacts = config.contacts
    contact_ids = [f"CON-{i:07d}" for i in range(1, n_contacts + 1)]
    contact_created = start + pd.to_timedelta(rng.integers(0, days, n_contacts), unit="D")
    contact_df = pd.DataFrame({
        "contact_id": contact_ids,
        "created_at": _date_strings(contact_created),
        "country": rng.choice(["IN", "US", "GB", "DE", "SG"], n_contacts, p=[.72, .12, .07, .05, .04]),
        "consent_status": rng.choice(["granted", "denied", "unknown"], n_contacts, p=[.78, .08, .14]),
        "lifecycle_state": rng.choice(["prospect", "customer", "inactive"], n_contacts, p=[.72, .20, .08]),
    })

    n_sessions = config.sessions
    session_contact_idx = rng.integers(0, n_contacts, n_sessions)
    session_ids = _ids("SES", n_sessions)
    session_dates = contact_created[session_contact_idx] + pd.to_timedelta(rng.integers(0, max(days * 86400, 1), n_sessions), unit="s")
    session_contact = np.array(contact_ids, dtype=object)[session_contact_idx]
    anon_count = min(max(1, n_sessions // 100), n_sessions) if n_sessions else 0
    anon_idx = rng.choice(n_sessions, size=anon_count, replace=False) if n_sessions else np.array([], dtype=int)
    session_contact[anon_idx] = None
    anon_set = set(anon_idx.tolist())
    session_df = pd.DataFrame({
        "session_id": session_ids,
        "contact_id": session_contact,
        "session_started_at": _date_strings(session_dates),
        "source": rng.choice(CHANNELS, n_sessions),
        "device": rng.choice(DEVICES, n_sessions),
        "identity_evidence": ["none" if i in anon_set else "authenticated_or_cookie_consent" for i in range(n_sessions)],
    })

    # Leads are built from identified sessions so contact/session joins are coherent.
    n_leads = config.leads
    identified_sessions = np.flatnonzero(pd.notna(session_contact))
    if n_leads and len(identified_sessions) == 0:
        raise ValueError("At least one identified session is required to generate leads")
    chosen_sessions = rng.choice(identified_sessions, size=n_leads, replace=True) if n_leads else np.array([], dtype=int)
    lead_ids = _ids("LEAD", n_leads)
    lead_created = session_dates[chosen_sessions] + pd.to_timedelta(rng.integers(0, 31 * 86400, n_leads), unit="s")
    lead_contact_ids = session_contact[chosen_sessions]
    lead_session_ids = np.array(session_ids, dtype=object)[chosen_sessions]
    lead_df = pd.DataFrame({
        "lead_id": lead_ids,
        "contact_id": lead_contact_ids,
        "session_id": lead_session_ids,
        "created_at": _date_strings(lead_created),
        "status": rng.choice(STATUSES, n_leads, p=[.25, .45, .20, .10]),
        "source_system": rng.choice(["web_form", "crm_import", "event_scan"], n_leads),
    })

    n_opps = config.opportunities
    if n_opps and n_leads == 0:
        raise ValueError("At least one lead is required to generate opportunities")
    opp_ids = _ids("OPP", n_opps)
    opp_lead_idx = rng.integers(0, n_leads, n_opps)
    opp_created = lead_created[opp_lead_idx] + pd.to_timedelta(rng.integers(0, 61 * 86400, n_opps), unit="s")
    opp_closed = opp_created + pd.to_timedelta(rng.integers(7, 121, n_opps), unit="D")
    opp_status = rng.choice(["closed_won", "closed_lost", "open"], n_opps, p=[.62, .23, .15])
    opp_amount = np.round(rng.lognormal(mean=10.0, sigma=.8, size=n_opps), 2)
    opp_currency = rng.choice(CURRENCIES, n_opps, p=[.82, .12, .06])
    opportunity_df = pd.DataFrame({
        "opportunity_id": opp_ids,
        "contact_id": np.array(lead_contact_ids, dtype=object)[opp_lead_idx],
        "lead_id": np.array(lead_ids, dtype=object)[opp_lead_idx],
        "created_at": _date_strings(opp_created),
        "closed_at": [d.strftime("%Y-%m-%dT%H:%M:%S") if s != "open" else None for d, s in zip(opp_closed, opp_status)],
        "stage": opp_status,
        "amount": opp_amount,
        "currency": opp_currency,
    })

    # Opportunity lifecycle history is append-only and separate from the current-state row.
    event_rows = []
    for opp in opportunity_df.itertuples(index=False):
        created = pd.Timestamp(opp.created_at)
        event_rows.append({"event_id": f"OPPEVT-{opp.opportunity_id}-001", "opportunity_id": opp.opportunity_id,
                           "from_stage": None, "to_stage": ("open" if opp.stage == "open" else "new"), "occurred_at": created.strftime("%Y-%m-%dT%H:%M:%S"),
                           "event_type": "created"})
        if opp.stage != "open":
            qualified = created + pd.Timedelta(days=2)
            event_rows.append({"event_id": f"OPPEVT-{opp.opportunity_id}-002", "opportunity_id": opp.opportunity_id,
                               "from_stage": "new", "to_stage": "qualified", "occurred_at": qualified.strftime("%Y-%m-%dT%H:%M:%S"),
                               "event_type": "stage_changed"})
            closed = pd.Timestamp(opp.closed_at)
            event_rows.append({"event_id": f"OPPEVT-{opp.opportunity_id}-003", "opportunity_id": opp.opportunity_id,
                               "from_stage": "qualified", "to_stage": opp.stage, "occurred_at": closed.strftime("%Y-%m-%dT%H:%M:%S"),
                               "event_type": "stage_changed"})
    opportunity_event_df = pd.DataFrame(event_rows)

    # Touchpoints are generated inside campaign active windows and after contact creation.
    n_touch = config.touchpoints
    tp_campaign_idx = rng.integers(0, n_campaigns, n_touch)
    tp_contact_idx = rng.integers(0, n_contacts, n_touch)
    campaign_duration = np.maximum((campaign_ends - campaign_starts).days.to_numpy(), 1)
    tp_offset_days = np.array([rng.integers(0, int(campaign_duration[i]) + 1) for i in tp_campaign_idx])
    tp_dates = campaign_starts[tp_campaign_idx] + pd.to_timedelta(tp_offset_days, unit="D") + pd.to_timedelta(rng.integers(0, 86400, n_touch), unit="s")
    tp_dates = pd.to_datetime(np.maximum(tp_dates.to_numpy(dtype="datetime64[ns]"), contact_created[tp_contact_idx].to_numpy(dtype="datetime64[ns]")))
    tp_order = pd.Series(tp_contact_idx).groupby(tp_contact_idx).cumcount().to_numpy() + 1
    touchpoint_ids = [f"TOUCH-{contact_ids[c]}-{k:06d}" for c, k in zip(tp_contact_idx, tp_order)]
    touchpoint_df = pd.DataFrame({
        "touchpoint_id": touchpoint_ids,
        "contact_id": np.array(contact_ids, dtype=object)[tp_contact_idx],
        "campaign_id": np.array(campaign_ids, dtype=object)[tp_campaign_idx],
        "occurred_at": _date_strings(tp_dates),
        "touch_type": rng.choice(["ad_click", "email_open", "web_visit", "event_attendance", "sales_call"], n_touch),
        "channel_id": [f"CH-{campaign_channels[c]+1:03d}" for c in tp_campaign_idx],
        "is_offline": rng.choice([False, True], n_touch, p=[.82, .18]),
        "issue_type": None,
    })

    cost_rows = []
    for i, cid in enumerate(campaign_ids):
        cost_rows.append({"cost_record_id": f"COST-{i+1:07d}", "campaign_id": cid,
                          "amount": round(float(budgets[i] * rng.uniform(.45, 1.0)), 2), "currency": currencies[i],
                          "incurred_at": campaign_starts[i].strftime("%Y-%m-%dT%H:%M:%S"),
                          "cost_type": "media_spend", "issue_type": None})
    cost_df = pd.DataFrame(cost_rows)

    won = opportunity_df[opportunity_df["stage"] == "closed_won"].copy()
    order_df = pd.DataFrame({
        "order_id": [f"ORD-{i:07d}" for i in range(1, len(won) + 1)],
        "opportunity_id": won["opportunity_id"].to_numpy(),
        "external_invoice_id": [f"INV-{i:07d}" for i in range(1, len(won) + 1)],
        "revenue_amount": won["amount"].to_numpy(),
        "currency": won["currency"].to_numpy(),
        "recognized_at": won["closed_at"].to_numpy(),
        "issue_type": None,
    })

    ground_truth = []
    if len(won) and n_campaigns:
        first_order = order_df.iloc[0].copy()
        duplicate = first_order.copy()
        duplicate["order_id"] = f"ORD-{len(order_df)+1:07d}"
        duplicate["issue_type"] = "duplicate_revenue"
        order_df = pd.concat([order_df, pd.DataFrame([duplicate])], ignore_index=True)
        ground_truth.append({"record_type": "Order", "record_id": duplicate["order_id"], "issue_type": "duplicate_revenue",
                             "expected_decision": "Detect duplicate external_invoice_id and exclude the duplicate from recognized revenue."})

        first_opp = opportunity_df.loc[opportunity_df["opportunity_id"] == first_order["opportunity_id"]].iloc[0]
        contact_id = first_opp["contact_id"]
        next_num = int((touchpoint_df["contact_id"] == contact_id).sum()) + 1
        late_tp = {"touchpoint_id": f"TOUCH-{contact_id}-{next_num:06d}", "contact_id": contact_id,
                   "campaign_id": campaign_ids[0], "occurred_at": (pd.Timestamp(first_opp["closed_at"]) + pd.Timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%S"),
                   "touch_type": "ad_click", "channel_id": campaign_df.iloc[0]["channel_id"], "is_offline": False,
                   "issue_type": "post_conversion_touch"}
        touchpoint_df = pd.concat([touchpoint_df, pd.DataFrame([late_tp])], ignore_index=True)
        ground_truth.append({"record_type": "Touchpoint", "record_id": late_tp["touchpoint_id"], "issue_type": "post_conversion_touch",
                             "expected_decision": "Exclude from all attribution models because occurred_at is after conversion."})

        mismatch_campaign = campaign_ids[min(1, n_campaigns - 1)]
        base_currency = campaign_df.loc[campaign_df.campaign_id == mismatch_campaign, "currency"].iloc[0]
        other_currency = next(c for c in CURRENCIES if c != base_currency)
        mismatch = {"cost_record_id": f"COST-{len(cost_df)+1:07d}", "campaign_id": mismatch_campaign,
                    "amount": 1234.56, "currency": other_currency, "incurred_at": end.strftime("%Y-%m-%dT00:00:00"),
                    "cost_type": "manual_adjustment", "issue_type": "currency_mismatch"}
        cost_df = pd.concat([cost_df, pd.DataFrame([mismatch])], ignore_index=True)
        ground_truth.append({"record_type": "CostRecord", "record_id": mismatch["cost_record_id"], "issue_type": "currency_mismatch",
                             "expected_decision": "Quarantine from campaign ROI; do not convert without an explicit FX rate."})

        omitted_campaign = campaign_ids[-1]
        cost_df = cost_df[cost_df["campaign_id"] != omitted_campaign].copy()
        ground_truth.append({"record_type": "Campaign", "record_id": omitted_campaign, "issue_type": "campaign_cost_omitted",
                             "expected_decision": "Mark ROI unavailable; missing spend must not be treated as zero."})

        anon_session = session_df.loc[session_df["identity_evidence"] == "none", "session_id"].iloc[0]
        anonymous_lead = lead_df.iloc[-1].copy()
        anonymous_lead["session_id"] = anon_session
        anonymous_lead["lead_id"] = f"LEAD-{len(lead_df)+1:06d}"
        anonymous_lead["source_system"] = "synthetic_anomaly"
        lead_df = pd.concat([lead_df, pd.DataFrame([anonymous_lead])], ignore_index=True)
        ground_truth.append({"record_type": "Lead", "record_id": anonymous_lead["lead_id"], "issue_type": "anonymous_session_without_evidence",
                             "expected_decision": "Reject identity resolution; retain lead with an explicitly unresolved session relationship."})

    bridge_df = campaign_df[["campaign_id", "audience_id", "start_at", "end_at"]].rename(
        columns={"start_at": "effective_from", "end_at": "effective_to"}).drop_duplicates()

    # Revenue source of truth: valid, non-duplicate Order rows only.
    valid_orders = order_df[order_df["issue_type"].isna()].copy()
    valid_orders = valid_orders.drop_duplicates(subset=["external_invoice_id"], keep="first")
    attribution_rows = []
    touchpoint_df["_occurred_ts"] = pd.to_datetime(touchpoint_df["occurred_at"], format="%Y-%m-%dT%H:%M:%S", errors="coerce")
    tp_by_contact = {k: g for k, g in touchpoint_df.groupby("contact_id", sort=False)}
    opp_by_id = opportunity_df.set_index("opportunity_id")
    for order in valid_orders.itertuples(index=False):
        opp = opp_by_id.loc[order.opportunity_id]
        touches = tp_by_contact.get(opp["contact_id"])
        if touches is None:
            continue
        eligible = touches[(touches["_occurred_ts"] <= pd.Timestamp(order.recognized_at)) &
                           (touches["issue_type"].isna())].sort_values("_occurred_ts")
        if eligible.empty:
            continue
        revenue = float(order.revenue_amount)
        first_campaign, last_campaign = eligible.iloc[0]["campaign_id"], eligible.iloc[-1]["campaign_id"]
        for model, campaign_id in [("first_touch", first_campaign), ("last_touch", last_campaign)]:
            attribution_rows.append({"attribution_id": f"ATR-{order.opportunity_id}-{model}", "opportunity_id": order.opportunity_id,
                                     "campaign_id": campaign_id, "model": model, "credited_revenue": revenue,
                                     "currency": order.currency, "eligible_touch_count": len(eligible)})
        weights = eligible.groupby("campaign_id").size()
        allocations = _cent_allocations(revenue, weights)
        for campaign_id, amount in allocations.items():
            attribution_rows.append({"attribution_id": f"ATR-{order.opportunity_id}-linear-{campaign_id}",
                                     "opportunity_id": order.opportunity_id, "campaign_id": campaign_id,
                                     "model": "linear_multi_touch", "credited_revenue": amount,
                                     "currency": order.currency, "eligible_touch_count": len(eligible)})
    touchpoint_df = touchpoint_df.drop(columns=["_occurred_ts"])
    attribution_df = pd.DataFrame(attribution_rows, columns=["attribution_id", "opportunity_id", "campaign_id", "model", "credited_revenue", "currency", "eligible_touch_count"])

    return {
        "Channel": channel_df, "Campaign": campaign_df, "Creative": creative_df, "Audience": audience_df,
        "Contact": contact_df, "WebSession": session_df, "Lead": lead_df, "Opportunity": opportunity_df,
        "OpportunityEvent": opportunity_event_df, "Touchpoint": touchpoint_df, "Order": order_df,
        "CostRecord": cost_df, "CampaignAudience": bridge_df, "AttributionResult": attribution_df,
        "GroundTruth": pd.DataFrame(ground_truth),
    }


def validate_dataset(tables: dict[str, pd.DataFrame]) -> dict:
    checks = []
    def check(name, passed, details):
        checks.append({"check": name, "passed": bool(passed), "details": str(details)})

    primary_keys = {
        "Campaign": "campaign_id", "Channel": "channel_id", "Creative": "creative_id", "Audience": "audience_id",
        "Contact": "contact_id", "Touchpoint": "touchpoint_id", "WebSession": "session_id", "Lead": "lead_id",
        "Opportunity": "opportunity_id", "OpportunityEvent": "event_id", "Order": "order_id", "CostRecord": "cost_record_id",
        "AttributionResult": "attribution_id",
    }
    for table, key in primary_keys.items():
        df = tables[table]
        check(f"{table} primary-key uniqueness", df[key].notna().all() and not df[key].duplicated().any(),
              f"rows={len(df)}; duplicate_keys={int(df[key].duplicated().sum())}")

    fk_specs = [
        ("Campaign", "channel_id", "Channel", "channel_id"), ("Campaign", "creative_id", "Creative", "creative_id"),
        ("Campaign", "audience_id", "Audience", "audience_id"), ("Touchpoint", "contact_id", "Contact", "contact_id"),
        ("Touchpoint", "campaign_id", "Campaign", "campaign_id"), ("Touchpoint", "channel_id", "Channel", "channel_id"),
        ("WebSession", "contact_id", "Contact", "contact_id"), ("Lead", "contact_id", "Contact", "contact_id"),
        ("Lead", "session_id", "WebSession", "session_id"), ("Opportunity", "contact_id", "Contact", "contact_id"),
        ("Opportunity", "lead_id", "Lead", "lead_id"), ("OpportunityEvent", "opportunity_id", "Opportunity", "opportunity_id"),
        ("Order", "opportunity_id", "Opportunity", "opportunity_id"), ("CostRecord", "campaign_id", "Campaign", "campaign_id"),
        ("AttributionResult", "opportunity_id", "Opportunity", "opportunity_id"), ("AttributionResult", "campaign_id", "Campaign", "campaign_id"),
        ("CampaignAudience", "campaign_id", "Campaign", "campaign_id"), ("CampaignAudience", "audience_id", "Audience", "audience_id"),
    ]
    for child, col, parent, pcol in fk_specs:
        vals = tables[child][col].dropna()
        valid = set(tables[parent][pcol])
        invalid = int((~vals.isin(valid)).sum())
        check(f"{child}.{col} -> {parent}.{pcol}", invalid == 0, f"invalid references={invalid}")

    check("Allowed channel values", tables["Channel"]["channel_name"].isin(CHANNELS).all(), "Channel enum")
    check("Opportunity amount non-negative", (tables["Opportunity"]["amount"] >= 0).all(), "amount >= 0")
    check("Campaign budget non-negative", (tables["Campaign"]["budget_amount"] >= 0).all(), "budget >= 0")
    bridge = tables["CampaignAudience"]
    check("CampaignAudience composite-key uniqueness", not bridge.duplicated(["campaign_id", "audience_id", "effective_from"]).any(), "campaign/audience/effective_from unique")
    check("CampaignAudience effective dates", (pd.to_datetime(bridge["effective_to"]) >= pd.to_datetime(bridge["effective_from"])).all(), "effective_to >= effective_from")
    check("Campaign date order", (pd.to_datetime(tables["Campaign"]["end_at"]) >= pd.to_datetime(tables["Campaign"]["start_at"])).all(), "end_at >= start_at")

    # Relationship integrity: anonymous-session anomaly is the only allowed contact/session mismatch.
    gt = tables["GroundTruth"]
    anomalous_leads = set(gt.loc[gt["issue_type"] == "anonymous_session_without_evidence", "record_id"])
    leads = tables["Lead"].merge(tables["WebSession"][["session_id", "contact_id", "identity_evidence"]], on="session_id", suffixes=("_lead", "_session"))
    mismatch = leads[(leads["contact_id_lead"] != leads["contact_id_session"]) & (~leads["lead_id"].isin(anomalous_leads))]
    check("Lead/session identity consistency", mismatch.empty, f"unlabelled mismatches={len(mismatch)}")
    bad_evidence = leads[(leads["identity_evidence"] == "none") & (~leads["lead_id"].isin(anomalous_leads))]
    check("No unsupported anonymous identity joins", bad_evidence.empty, f"unlabelled anonymous joins={len(bad_evidence)}")

    opps = tables["Opportunity"].merge(tables["Lead"][["lead_id", "contact_id", "created_at"]], on="lead_id", suffixes=("_opp", "_lead"))
    contact_mismatch = opps[opps["contact_id_opp"] != opps["contact_id_lead"]]
    check("Opportunity/lead contact consistency", contact_mismatch.empty, f"mismatches={len(contact_mismatch)}")
    check("Opportunity created after lead", (pd.to_datetime(opps["created_at_opp"]) >= pd.to_datetime(opps["created_at_lead"])).all(), "opportunity.created_at >= lead.created_at")
    closed = tables["Opportunity"].dropna(subset=["closed_at"])
    check("Opportunity close after creation", (pd.to_datetime(closed["closed_at"]) >= pd.to_datetime(closed["created_at"])).all(), "closed_at >= created_at")
    check("Open opportunities have no close date", tables["Opportunity"].loc[tables["Opportunity"]["stage"] == "open", "closed_at"].isna().all(), "open => closed_at null")

    touch = tables["Touchpoint"].merge(tables["Campaign"][["campaign_id", "channel_id"]], on="campaign_id", suffixes=("_touch", "_campaign"))
    check("Touchpoint channel matches campaign", (touch["channel_id_touch"] == touch["channel_id_campaign"]).all(), "touch channel equals campaign channel")
    labelled_late = set(gt.loc[gt["issue_type"] == "post_conversion_touch", "record_id"])
    check("Post-conversion touch is labelled", tables["Touchpoint"].loc[tables["Touchpoint"]["touchpoint_id"].isin(labelled_late), "issue_type"].eq("post_conversion_touch").all(), "late touch has explicit issue_type")
    if len(labelled_late):
        late_rows = tables["Touchpoint"][tables["Touchpoint"]["touchpoint_id"].isin(labelled_late)]
        late_opps = tables["Opportunity"][tables["Opportunity"]["contact_id"].isin(late_rows["contact_id"]) & tables["Opportunity"]["closed_at"].notna()]
        check("Late-touch anomaly occurs after a conversion", any(
            (pd.Timestamp(t.occurred_at) > pd.Timestamp(o.closed_at))
            for t in late_rows.itertuples(index=False) for o in late_opps.itertuples(index=False) if t.contact_id == o.contact_id
        ), "labelled late touch is after associated conversion")

    duplicate_invoices = tables["Order"][tables["Order"]["issue_type"].isna()].duplicated("external_invoice_id").sum()
    check("No unlabelled duplicate invoices", duplicate_invoices == 0, f"unlabelled duplicate invoice rows={duplicate_invoices}")
    order_opp = tables["Order"].merge(tables["Opportunity"][["opportunity_id", "stage", "amount", "currency"]], on="opportunity_id", suffixes=("_order", "_opp"))
    valid_order = order_opp[order_opp["issue_type"].isna()]
    check("Valid orders belong to closed-won opportunities", valid_order["stage"].eq("closed_won").all(), "valid order stage=closed_won")
    check("Order amount reconciles to opportunity", np.allclose(valid_order["revenue_amount"], valid_order["amount"], atol=0.01), "order revenue equals opportunity amount")
    check("Order currency matches opportunity", valid_order["currency_order"].eq(valid_order["currency_opp"]).all(), "order currency equals opportunity currency")

    # Attribution must exclude late/labelled touches and reconcile to valid order revenue by model.
    attr = tables["AttributionResult"]
    check("Attribution has non-negative revenue", (attr["credited_revenue"] >= 0).all(), "credited_revenue >= 0")
    if not attr.empty:
        check("Attribution currencies match order", attr.merge(valid_order[["opportunity_id", "currency_order"]], on="opportunity_id")["currency"].eq(
            attr.merge(valid_order[["opportunity_id", "currency_order"]], on="opportunity_id")["currency_order"]).all(), "attribution currency equals order currency")
        for model in ["first_touch", "last_touch", "linear_multi_touch"]:
            got = attr.loc[attr["model"] == model].groupby("opportunity_id")["credited_revenue"].sum().sort_index()
            expected = valid_order.drop_duplicates("opportunity_id").set_index("opportunity_id")["revenue_amount"].sort_index()
            common = expected.index.intersection(got.index)
            reconciles = np.allclose(expected.loc[common].to_numpy(), got.loc[common].to_numpy(), atol=0.01)
            check(f"{model} opportunity revenue reconciliation", reconciles, f"reconciled opportunities={len(common)}")

    # Lifecycle event sequence must be chronological and end at the current state.
    ev = tables["OpportunityEvent"].sort_values(["opportunity_id", "occurred_at"])
    event_ts = pd.to_datetime(ev["occurred_at"], format="%Y-%m-%dT%H:%M:%S")
    event_dates_ok = not event_ts.groupby(ev["opportunity_id"]).diff().lt(pd.Timedelta(0)).any()
    check("Opportunity event chronology", event_dates_ok, "events are ordered by occurred_at")
    final_events = ev.groupby("opportunity_id").tail(1).merge(tables["Opportunity"][["opportunity_id", "stage"]], on="opportunity_id")
    check("Lifecycle history matches current opportunity stage", final_events["to_stage"].eq(final_events["stage"]).all(), "latest event matches final state (open remains new)")

    required_issues = {"duplicate_revenue", "post_conversion_touch", "currency_mismatch", "campaign_cost_omitted", "anonymous_session_without_evidence"}
    actual_issues = set(gt["issue_type"].dropna())
    check("All five required anomaly classes", required_issues <= actual_issues, f"missing={sorted(required_issues - actual_issues)}")
    check("Ground truth remains separate", "GroundTruth" in tables and len(gt) >= 5, f"labelled exceptions={len(gt)}")
    return {"passed": all(x["passed"] for x in checks), "checks": checks, "row_counts": {k: len(v) for k, v in tables.items()}}


def export_dataset(tables: dict[str, pd.DataFrame], output_dir: str | Path, config: DatasetConfig):
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    ground = out.parent / "ground_truth"
    ground.mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        if name == "GroundTruth":
            df.to_csv(ground / "ground_truth.csv", index=False)
            (ground / "ground_truth.json").write_text(df.to_json(orient="records", indent=2), encoding="utf-8")
        else:
            df.to_csv(out / f"{name.lower()}.csv", index=False)
            (out / f"{name.lower()}.json").write_text(df.to_json(orient="records", indent=2), encoding="utf-8")
    report = validate_dataset(tables)
    (out / "validation_summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_sqlite(tables, out / "attribution.db")
    # Portable SQL seed file, excluding the separate ground-truth labels.
    with sqlite3.connect(out / "attribution.db") as conn:
        sql_dump = "\n".join(conn.iterdump())
    (out / "seed_data.sql").write_text(sql_dump + "\n", encoding="utf-8")

    manifest = {"config": asdict(config), "seed": config.seed, "provenance": "fully synthetic; no external personal data",
                "tables": {}, "ground_truth": {}}
    for folder, target in [(out, manifest["tables"]), (ground, manifest["ground_truth"])]:
        for p in sorted(folder.iterdir()):
            if p.is_file() and p.name not in {"source_manifest.json"}:
                target[p.name] = {"sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "bytes": p.stat().st_size}
    (out / "source_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return report


def write_sqlite(tables, db_path):
    if db_path.exists():
        db_path.unlink()
    with sqlite3.connect(db_path) as conn:
        for name, df in tables.items():
            if name == "GroundTruth":
                continue
            df.to_sql(name.lower(), conn, index=False, if_exists="replace")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_touch_contact_time ON touchpoint(contact_id, occurred_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_attr_campaign_model ON attributionresult(campaign_id, model)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_opp_contact_created ON opportunity(contact_id, created_at)")
