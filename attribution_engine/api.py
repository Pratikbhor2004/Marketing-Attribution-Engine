from pathlib import Path
import pandas as pd
from fastapi import FastAPI, HTTPException, Query

app=FastAPI(title="Marketing Attribution Engine API",version="1.0.0",
            description="Read-only API over generated attribution and ROI outputs.")
DATA=Path("data/generated")

@app.get("/health")
def health():
    return {"status":"ok","data_directory":str(DATA),"available":DATA.exists()}

def load_csv(name):
    path=DATA/f"{name}.csv"
    if not path.exists():
        raise HTTPException(404,"Dataset not found. Run the generator first.")
    return pd.read_csv(path)

@app.get("/campaigns")
def campaigns(limit:int=Query(100,ge=1,le=1000)):
    df=load_csv("campaign")
    return df.head(limit).where(pd.notna(df),None).to_dict("records")

@app.get("/roi")
def roi(model:str="linear_multi_touch",limit:int=Query(100,ge=1,le=1000)):
    if model not in {"linear_multi_touch","first_touch","last_touch"}:
        raise HTTPException(400,"Choose first_touch, last_touch or linear_multi_touch.")
    attrs=load_csv("attributionresult")
    attrs=attrs[attrs.model==model]
    campaigns=load_csv("campaign")
    costs=load_csv("costrecord")
    rev=attrs.groupby(["campaign_id","currency"],as_index=False).credited_revenue.sum()
    costs=costs.merge(campaigns[["campaign_id","currency"]],on="campaign_id",suffixes=("","_campaign"))
    costs=costs[(costs.currency==costs.currency_campaign) & costs.issue_type.isna()]
    spend=costs.groupby(["campaign_id","currency"],as_index=False).amount.sum().rename(columns={"amount":"spend"})
    result=campaigns.merge(rev,on=["campaign_id","currency"],how="left").merge(spend,on=["campaign_id","currency"],how="left")
    result["attributed_revenue"]=result["credited_revenue"].fillna(0)
    result["roi_pct"]=((result["attributed_revenue"]-result["spend"])/result["spend"]*100).where(result["spend"].notna() & (result["spend"]>0))
    result["roi_status"]=result["spend"].isna().map({True:"unavailable_missing_or_invalid_cost",False:"calculated"})
    return result.head(limit).where(pd.notna(result.head(limit)),None).to_dict("records")

@app.get("/exceptions")
def exceptions():
    path=Path("data/ground_truth/ground_truth.csv")
    if not path.exists():
        raise HTTPException(404,"Ground truth not found. Run the generator first.")
    df=pd.read_csv(path)
    return df.where(pd.notna(df),None).to_dict("records")
