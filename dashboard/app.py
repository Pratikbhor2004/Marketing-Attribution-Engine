from pathlib import Path
import pandas as pd
import streamlit as st
import plotly.express as px

st.set_page_config(page_title="Marketing Attribution",layout="wide")
st.title("Marketing Attribution & Campaign ROI")
st.caption("Synthetic, reproducible assessment dataset — not real customer or campaign data.")
DATA=Path("data/generated")
if not (DATA/"campaign_roi.csv").exists():
    st.warning("Dataset not found. From the project root, run: python -m attribution_engine.cli --scale starter")
    st.stop()
roi=pd.read_csv(DATA/"campaign_roi.csv")
attrs=pd.read_csv(DATA/"attributionresult.csv")
exceptions=pd.read_csv("data/ground_truth/ground_truth.csv") if Path("data/ground_truth/ground_truth.csv").exists() else pd.DataFrame()
model=st.selectbox("Attribution model",["linear_multi_touch","first_touch","last_touch"])
a=attrs[attrs.model==model]
rev=a.groupby("campaign_id",as_index=False).credited_revenue.sum()
view=roi.drop(columns=["attributed_revenue","roi_pct","roi_status"],errors="ignore").merge(rev,on="campaign_id",how="left")
view["attributed_revenue"]=view["credited_revenue"].fillna(0)
view["roi_pct"]=(view.attributed_revenue-view.spend)/view.spend*100
c1,c2,c3=st.columns(3)
c1.metric("Campaigns",f"{len(view):,}")
c2.metric("Attributed revenue",f"{view.attributed_revenue.sum():,.0f}")
c3.metric("Known exceptions",f"{len(exceptions):,}")
st.subheader("Campaign revenue")
st.plotly_chart(px.bar(view.nlargest(20,"attributed_revenue"),x="campaign_id",y="attributed_revenue",title="Top 20 campaigns by attributed revenue"),use_container_width=True)
st.subheader("Campaign ROI")
st.dataframe(view[["campaign_id","campaign_name","currency","spend","attributed_revenue","roi_pct"]].sort_values("roi_pct",ascending=False),use_container_width=True)
st.subheader("Known exception cases")
if len(exceptions): st.dataframe(exceptions,use_container_width=True)
