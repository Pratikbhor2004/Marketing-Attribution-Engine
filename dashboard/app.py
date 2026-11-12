"""Streamlit analyst dashboard.   streamlit run dashboard/app.py

Reads the exported SQLite store (ATTRIBUTION_DATA_DIR, default data/generated) and the separate
ground-truth folder. Synthetic data only.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from attribution_engine.analytics import campaign_roi, channel_roi  # noqa: E402
from attribution_engine.attribution import MODELS  # noqa: E402
from attribution_engine.pipeline import connect  # noqa: E402

DATA = Path(os.environ.get("ATTRIBUTION_DATA_DIR", "data/generated"))
DB = DATA / "attribution.db"
st.set_page_config(page_title="Marketing Attribution", layout="wide")
st.title("Marketing Attribution & Campaign ROI")
st.caption("Synthetic, reproducible dataset — no real customer or campaign data.")

if not DB.exists():
    st.warning("Dataset not found. From the project root run: `python -m attribution_engine.cli generate --scale starter`")
    st.stop()


@st.cache_data(show_spinner="Loading tables…")
def load(name: str, mtime: float) -> pd.DataFrame:
    with connect(DB) as conn:
        return pd.read_sql_query(f'SELECT * FROM "{name.lower()}"', conn)


mt = DB.stat().st_mtime
T = {n: load(n, mt) for n in ["Campaign", "Channel", "CostRecord", "FxRate", "ExceptionLog", "AttributionResult",
                              "UnattributedRevenue", "Opportunity", "Touchpoint", "AttributionRun"]}
evaluation = json.loads((DATA / "evaluation_summary.json").read_text(encoding="utf-8"))
runs = T["AttributionRun"]["run_id"].tolist()
c1, c2 = st.columns(2)
run_id = c1.selectbox("Attribution run", runs, index=0)
model = c2.selectbox("Attribution model", MODELS, index=MODELS.index("linear_multi_touch"))
res = T["AttributionResult"][T["AttributionResult"]["run_id"] == run_id]
if model not in set(res["model"]):
    st.info(f"Run {run_id} does not include {model}.")
    st.stop()
roi = campaign_roi(T, T["ExceptionLog"], res, model)
ch = channel_roi(roi, T["Channel"])

rec = evaluation["reconciliation"]
k = st.columns(5)
k[0].metric("Attributed revenue (USD)", f"{roi['attributed_revenue_usd'].sum():,.0f}")
k[1].metric("Valid spend (USD)", f"{roi['spend_usd'].sum():,.0f}")
known = roi[roi["roi_status"] != "unavailable_missing_cost"]
k[2].metric("Portfolio ROAS", f"{known['attributed_revenue_usd'].sum() / known['spend_usd'].sum():.2f}x")
k[3].metric("Unattributed revenue (USD)", f"{T['UnattributedRevenue'].query('run_id == @run_id')['revenue_usd'].sum():,.0f}")
k[4].metric("Exceptions detected", f"{len(T['ExceptionLog']):,}")

tabs = st.tabs(["Model comparison", "Campaign ROI", "Channel ROI", "Exceptions", "Identity gaps", "Explain a conversion", "Evaluation"])
with tabs[0]:
    share = (res.groupby(["model", "channel_id"])["credited_revenue_usd"].sum().reset_index()
             .merge(T["Channel"][["channel_id", "channel_name"]], on="channel_id"))
    st.plotly_chart(px.bar(share, x="channel_name", y="credited_revenue_usd", color="model", barmode="group",
                           title="Revenue credited to each channel, by model"), width="stretch")
    mc = evaluation["model_comparison"]
    if "distance_to_ground_truth" in mc:
        d = pd.DataFrame(mc["distance_to_ground_truth"]).T.reset_index(names="model")
        st.write(f"Closest to the generating rule: **{mc['best_model_vs_ground_truth']}** (lower distance is better)")
        st.dataframe(d, width="stretch", hide_index=True)
with tabs[1]:
    status = st.multiselect("ROI status", sorted(roi["roi_status"].unique()), default=sorted(roi["roi_status"].unique()))
    view = roi[roi["roi_status"].isin(status)].sort_values("roi_pct", ascending=False)
    st.dataframe(view[["campaign_id", "campaign_name", "market", "currency", "spend_usd", "quarantined_cost_usd",
                       "attributed_revenue_usd", "roas", "roi_pct", "roi_status"]], width="stretch", hide_index=True)
with tabs[2]:
    st.plotly_chart(px.bar(ch, x="channel_name", y="roas", title=f"Channel ROAS — {model}"), width="stretch")
    st.dataframe(ch, width="stretch", hide_index=True)
with tabs[3]:
    e = T["ExceptionLog"]
    st.plotly_chart(px.bar(e["issue_type"].value_counts().reset_index(), x="issue_type", y="count", title="Detected exceptions"),
                    width="stretch")
    st.dataframe(e, width="stretch", hide_index=True)
with tabs[4]:
    st.json(evaluation["identity_gaps"])
with tabs[5]:
    won = res["opportunity_id"].drop_duplicates().sort_values()
    opp = st.selectbox("Opportunity", won.head(2000))
    r = res[res["opportunity_id"] == opp]
    st.dataframe(r.pivot_table(index="campaign_id", columns="model", values="credited_revenue", aggfunc="sum"), width="stretch")
    o = T["Opportunity"].set_index("opportunity_id").loc[opp]
    tp = T["Touchpoint"][T["Touchpoint"]["contact_id"] == o["contact_id"]].sort_values("occurred_at").copy()
    tp["after_conversion"] = tp["occurred_at"] > o["closed_at"]
    st.caption(f"Contact {o['contact_id']} — converted {o['closed_at']}")
    st.dataframe(tp[["touchpoint_id", "occurred_at", "campaign_id", "channel_id", "touch_type", "is_offline", "after_conversion"]],
                 width="stretch", hide_index=True)
with tabs[6]:
    st.subheader("Detection vs ground truth")
    st.json(evaluation["detection_vs_ground_truth"]["overall"])
    st.subheader("Chronological split")
    st.json(evaluation["chronological_evaluation"])
    st.subheader("Reconciliation")
    st.json({"passed": rec["passed"], "spend": rec["spend"], "attributed_revenue_usd_by_model": rec["attributed_revenue_usd_by_model"]})
