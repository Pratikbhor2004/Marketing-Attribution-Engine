"""Deterministic synthetic data generator.

Generation order (parents before children):

  1. Reference/master data  : Channel, FxRate, Campaign, Creative, Audience, CampaignAudience, Contact
  2. Funnel records         : Lead -> Opportunity (planned dates)
  3. Behavioural events     : Touchpoint (journeys), WebSession
  4. Rule-derived outcomes  : Opportunity stage (won/lost/open), OpportunityEvent history,
                              Lead status, Contact lifecycle, Order, CostRecord
  5. Labelled anomalies     : injected as *new* rows (or a documented removal) and catalogued
                              only in the separate ground-truth output

Nothing in the input tables carries an anomaly label; the engine must *detect* exceptions
from business rules (see detection.py) and is scored against ground truth.
"""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np
import pandas as pd

from .config import DatasetConfig

ID_YEAR = 2026
TS_FMT = "%Y-%m-%dT%H:%M:%S"

# channel_id, name, group, is_paid, touch types, source system, cost type
CHANNELS = [
    ("CH-001", "Paid Search", "digital", True, ["ad_click"], "ad_platform", "media_spend"),
    ("CH-002", "Paid Social", "digital", True, ["ad_click", "video_view"], "ad_platform", "media_spend"),
    ("CH-003", "Email", "digital", False, ["email_open", "email_click"], "esp", "platform_fee"),
    ("CH-004", "Organic Search", "digital", False, ["organic_visit"], "web_analytics", "content_production"),
    ("CH-005", "Affiliate", "digital", True, ["affiliate_click"], "affiliate_network", "commission"),
    ("CH-006", "Display", "digital", True, ["ad_click", "ad_view"], "ad_platform", "media_spend"),
    ("CH-007", "Events", "offline", True, ["event_attendance", "booth_scan"], "event_app", "event_fee"),
    ("CH-008", "Partner", "partner", False, ["partner_referral", "sales_call"], "crm", "partner_fee"),
]
CHANNEL_NAMES = [c[1] for c in CHANNELS]
PAID_DIGITAL = {0, 1, 4, 5}                 # indexes of paid digital channels (platform claims)
OFFLINE_TOUCH_TYPES = {"event_attendance", "booth_scan", "sales_call"}
TOUCH_TYPES = sorted({t for c in CHANNELS for t in c[4]})

MARKETS = ["IN", "US", "EU"]
MARKET_CURRENCY = {"IN": "INR", "US": "USD", "EU": "EUR"}
MARKET_PROB = [0.60, 0.22, 0.18]
MARKET_COUNTRIES = {"IN": ["IN"], "US": ["US"], "EU": ["DE", "FR", "NL"]}
CURRENCIES = ["INR", "USD", "EUR"]
BASE_FX_TO_USD = {"INR": 0.0120, "USD": 1.0, "EUR": 1.08}

COMPANY_SIZES = ["smb", "mid_market", "enterprise"]
SIZE_PROB = [0.60, 0.28, 0.12]
SIZE_DEAL_USD = {"smb": 6_000, "mid_market": 22_000, "enterprise": 75_000}
SIZE_LOGIT = {"smb": 0.0, "mid_market": -0.15, "enterprise": -0.35}

# Documented outcome rule (ground-truth causal weights; never exported into input tables).
TRUE_CHANNEL_EFFECT = {"CH-001": 0.30, "CH-002": 0.10, "CH-003": 0.15, "CH-004": 0.22,
                       "CH-005": 0.05, "CH-006": 0.03, "CH-007": 0.45, "CH-008": 0.38}
WIN_INTERCEPT = -1.0
TOUCH_CAP_PER_CHANNEL = 3

LEAD_SOURCES = ["web_form", "event_scan", "crm_import"]
LEAD_LINK_METHOD = {"web_form": "session_form_submit", "event_scan": "event_badge_scan", "crm_import": "crm_import"}
OPP_STAGES = ["prospecting", "qualified", "proposal", "closed_won", "closed_lost"]
OPP_TRANSITIONS = {  # allowed lifecycle transitions (from -> set of to)
    None: {"prospecting"},
    "prospecting": {"qualified", "closed_lost"},
    "qualified": {"proposal", "closed_lost"},
    "proposal": {"closed_won", "closed_lost"},
    "closed_won": set(), "closed_lost": set(),
}
SESSION_EVIDENCE = ["form_submit", "login", "email_click_id", "cookie_match", "none"]
DEVICES = ["mobile", "desktop", "tablet"]


@dataclass
class GeneratedDataset:
    tables: dict[str, pd.DataFrame]
    ground_truth: dict[str, pd.DataFrame]


# ----------------------------------------------------------------------------------- helpers
def _seq_ids(prefix: str, n: int, start: int = 1) -> list[str]:
    return [f"{prefix}-{ID_YEAR}-{i:06d}" for i in range(start, start + n)]


def _fmt(values) -> np.ndarray:
    s = pd.Series(pd.to_datetime(values))
    out = s.dt.strftime(TS_FMT).to_numpy(dtype=object)
    out[s.isna().to_numpy()] = None
    return out


def _seconds(n_or_array) -> pd.TimedeltaIndex:
    return pd.to_timedelta(np.asarray(n_or_array, dtype="int64"), unit="s").as_unit("ns")


def _ns(x) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(x).as_unit("ns")


def _uniform_between(rng, lo: pd.DatetimeIndex, hi: pd.DatetimeIndex) -> pd.DatetimeIndex:
    lo_ns, hi_ns = _ns(lo).asi8, _ns(hi).asi8
    span = np.maximum(hi_ns - lo_ns, 0)
    return pd.DatetimeIndex((lo_ns + (rng.random(len(lo_ns)) * span).astype("int64"))).floor("s")


def _n_anomalies(cfg: DatasetConfig, issue: str, population: int) -> int:
    if population <= 0:
        return 0
    return int(min(population, max(cfg.min_anomalies_per_type, round(cfg.anomaly_rates[issue] * population))))


def fx_lookup(fx: pd.DataFrame, currencies, timestamps) -> np.ndarray:
    """Monthly rate_to_usd for each (currency, timestamp) pair."""
    table = {(r.currency, r.rate_month): r.rate_to_usd for r in fx.itertuples(index=False)}
    months = pd.to_datetime(pd.Series(timestamps)).dt.strftime("%Y-%m")
    last = fx.sort_values("rate_month").groupby("currency")["rate_to_usd"].last().to_dict()
    return np.array([table.get((c, m), last.get(c, np.nan)) for c, m in zip(currencies, months)], dtype=float)


# ---------------------------------------------------------------------------- master data
def _channels() -> pd.DataFrame:
    return pd.DataFrame({
        "channel_id": [c[0] for c in CHANNELS], "channel_name": CHANNEL_NAMES,
        "channel_group": [c[2] for c in CHANNELS], "is_paid": [c[3] for c in CHANNELS],
        "default_source_system": [c[5] for c in CHANNELS],
    })


def _fx_rates(rng, start, end) -> pd.DataFrame:
    months = pd.period_range(start, end + pd.DateOffset(months=14), freq="M")
    rows = []
    for cur in CURRENCIES:
        rate = BASE_FX_TO_USD[cur]
        for m in months:
            if cur != "USD":
                rate = rate * (1 + rng.normal(0, 0.008))
            rows.append({"fx_rate_id": f"FX-{cur}-{m.strftime('%Y-%m')}", "currency": cur,
                         "rate_month": m.strftime("%Y-%m"), "rate_to_usd": round(float(rate), 6),
                         "source": "synthetic_reference_rate"})
    return pd.DataFrame(rows)


def _campaigns(rng, cfg: DatasetConfig, start, end, fx):
    n = cfg.campaigns
    pairs = [(m, c) for m in range(len(MARKETS)) for c in range(len(CHANNELS))]
    n_ever = min(len(pairs), n // 2)
    market = np.empty(n, dtype=int)
    channel = np.empty(n, dtype=int)
    evergreen = np.zeros(n, dtype=bool)
    for i in range(n_ever):
        market[i], channel[i] = pairs[i]
        evergreen[i] = True
    rest = n - n_ever
    market[n_ever:] = rng.choice(len(MARKETS), rest, p=MARKET_PROB)
    channel[n_ever:] = rng.choice(len(CHANNELS), rest, p=[.20, .17, .12, .08, .08, .10, .15, .10])
    span_days = (end - start).days
    starts = pd.DatetimeIndex(np.where(evergreen, start.value,
                                       (start + pd.to_timedelta(rng.integers(0, max(span_days - 45, 1), n), unit="D")).asi8))
    durations = rng.integers(30, 181, n)
    ends = pd.DatetimeIndex(np.where(evergreen, pd.Timestamp(f"{end.year}-12-31T23:59:59").value,
                                     (starts + pd.to_timedelta(durations, unit="D") - pd.Timedelta(seconds=1)).asi8))
    objective = rng.choice(["awareness", "lead_generation", "conversion"], n, p=[.25, .45, .30])
    # Budgets scale with expected deal flow per campaign so starter and full runs have comparable ROI.
    spend_scale = 0.35 * (cfg.opportunities / max(n, 1)) / 20
    budget_usd = spend_scale * np.where(evergreen, rng.uniform(250_000, 900_000, n), rng.uniform(15_000, 350_000, n))
    currency = np.array([MARKET_CURRENCY[MARKETS[m]] for m in market])
    budget_local = np.round(budget_usd / np.array([BASE_FX_TO_USD[c] for c in currency]), 2)
    ids = _seq_ids("CMP", n)
    status = np.where(ends < end, "completed", "active")
    campaign = pd.DataFrame({
        "campaign_id": ids,
        "campaign_name": [f"{MARKETS[market[i]]} {CHANNEL_NAMES[channel[i]]} - "
                          f"{'Always-On' if evergreen[i] else objective[i].replace('_', ' ').title()} {i + 1:04d}"
                          for i in range(n)],
        "channel_id": [CHANNELS[c][0] for c in channel],
        "market": [MARKETS[m] for m in market],
        "currency": currency,
        "objective": objective,
        "budget_amount": budget_local,
        "start_at": _fmt(starts), "end_at": _fmt(ends),
        "is_evergreen": evergreen,
        "status": status,
    })

    # Creatives: Campaign 1:N Creative (offline channels have a single physical kit).
    cr_rows = []
    for i in range(n):
        offline = CHANNELS[channel[i]][2] != "digital"
        k = 1 if offline else int(rng.integers(1, 4))
        for v in range(k):
            fmt = "event_kit" if offline else rng.choice(["image", "video", "text", "landing_page"])
            cr_rows.append({"campaign_id": ids[i], "format": fmt, "version": "ABC"[v], "launched_at": campaign.start_at[i]})
    creative = pd.DataFrame(cr_rows)
    creative.insert(0, "creative_id", _seq_ids("CRV", len(creative)))
    creative.insert(2, "creative_name", [f"{r.campaign_id} {r.format} v{r.version}" for r in creative.itertuples()])

    audience = pd.DataFrame({
        "audience_id": [f"AUD-{ID_YEAR}-{i:06d}" for i in range(1, 26)],
        "audience_name": [f"Audience Segment {i:02d}" for i in range(1, 26)],
        "segment_type": rng.choice(["prospect", "retargeting", "lookalike", "customer"], 25),
        "effective_from": start.strftime("%Y-%m-%d"),
        "effective_to": None,
    })
    # Campaign N:M Audience bridge with effective dates (later audiences added mid-flight).
    ca_rows = []
    for i in range(n):
        k = int(rng.integers(1, 4))
        auds = rng.choice(len(audience), k, replace=False)
        for j, a in enumerate(auds):
            eff_from = starts[i] if j == 0 else starts[i] + (ends[i] - starts[i]) * float(rng.uniform(.1, .6))
            ca_rows.append({"campaign_id": ids[i], "audience_id": audience.audience_id[a],
                            "effective_from": pd.Timestamp(eff_from).floor("s").strftime(TS_FMT),
                            "effective_to": campaign.end_at[i]})
    campaign_audience = pd.DataFrame(ca_rows)
    campaign_audience.insert(0, "campaign_audience_id", _seq_ids("CAUD", len(campaign_audience)))
    meta = {"market": market, "channel": channel, "starts": starts, "ends": ends, "evergreen": evergreen}
    return campaign, creative, audience, campaign_audience, meta


def _assign_campaigns(rng, t: pd.DatetimeIndex, market_idx: np.ndarray, cmeta: dict) -> np.ndarray:
    """Pick, for every touch, a campaign that is active at that instant (90%+ same market)."""
    n = len(t)
    out = np.full(n, -1, dtype=int)
    target = np.where(rng.random(n) < 0.08, rng.integers(0, len(MARKETS), n), market_idx)
    pools = [np.flatnonzero(cmeta["market"] == m) for m in range(len(MARKETS))]
    cs, ce, tn = _ns(cmeta["starts"]).asi8, _ns(cmeta["ends"]).asi8, _ns(t).asi8
    remaining = np.arange(n)
    for _ in range(40):
        if not len(remaining):
            break
        cand = np.empty(len(remaining), dtype=int)
        for m, pool in enumerate(pools):
            sel = target[remaining] == m
            if sel.any():
                cand[sel] = pool[rng.integers(0, len(pool), int(sel.sum()))]
        ok = (cs[cand] <= tn[remaining]) & (tn[remaining] <= ce[cand])
        out[remaining[ok]] = cand[ok]
        remaining = remaining[~ok]
    if len(remaining):  # fall back to an always-on campaign of the target market
        ever = np.flatnonzero(cmeta["evergreen"])
        for i in remaining:
            options = ever[cmeta["market"][ever] == target[i]]
            out[i] = (options if len(options) else ever)[rng.integers(0, len(options) or len(ever))]
    return out


# ------------------------------------------------------------------------------- generator
def generate_dataset(cfg: DatasetConfig) -> GeneratedDataset:
    rng = np.random.default_rng(cfg.seed)
    start, end = pd.Timestamp(cfg.start_date).as_unit("ns"), pd.Timestamp(cfg.end_date).as_unit("ns")
    if cfg.opportunities > cfg.leads:
        raise ValueError("opportunities cannot exceed leads (each opportunity converts one lead)")

    channel = _channels()
    fx = _fx_rates(rng, start, end)
    campaign, creative, audience, campaign_audience, cmeta = _campaigns(rng, cfg, start, end, fx)

    # ---- Contacts (created_at is re-derived later from first observed activity) --------
    n_c = cfg.contacts
    c_market = rng.choice(len(MARKETS), n_c, p=MARKET_PROB)
    c_size = rng.choice(len(COMPANY_SIZES), n_c, p=SIZE_PROB)
    c_created = _uniform_between(rng, pd.DatetimeIndex([start] * n_c), pd.DatetimeIndex([end - pd.Timedelta(days=21)] * n_c))
    c_country = np.array([MARKET_COUNTRIES[MARKETS[m]][rng.integers(0, len(MARKET_COUNTRIES[MARKETS[m]]))] for m in c_market])

    # ---- Leads ---------------------------------------------------------------------------
    n_l = cfg.leads
    l_contact = rng.integers(0, n_c, n_l)
    l_created = c_created[l_contact] + _seconds(rng.exponential(45 * 86400, n_l))
    late = l_created > end - pd.Timedelta(days=1)
    if late.any():
        l_created = pd.DatetimeIndex(np.where(late, _uniform_between(rng, c_created[l_contact], pd.DatetimeIndex([end - pd.Timedelta(days=1)] * n_l)).asi8, l_created.asi8))
    l_created = l_created.floor("s")
    l_source = rng.choice(LEAD_SOURCES, n_l, p=[.65, .15, .20])

    # ---- Opportunities (planned dates; outcome decided later by rule) ----------------
    n_o = cfg.opportunities
    o_lead = np.sort(rng.choice(n_l, n_o, replace=False))
    o_contact = l_contact[o_lead]
    o_created = l_created[o_lead] + _seconds(rng.gamma(2.0, 7 * 86400, n_o))
    late = o_created > end - pd.Timedelta(hours=1)
    if late.any():
        o_created = pd.DatetimeIndex(np.where(late, _uniform_between(rng, l_created[o_lead], pd.DatetimeIndex([end - pd.Timedelta(hours=1)] * n_o)).asi8, o_created.asi8))
    o_created = o_created.floor("s")
    cycle_days = np.clip(rng.lognormal(np.log(45), 0.85, n_o), 7, 540)  # long conversion windows
    o_planned_close = (o_created + _seconds(cycle_days * 86400)).floor("s")
    o_open = o_planned_close > end
    o_size = np.array(COMPANY_SIZES)[c_size[o_contact]]
    o_currency = np.array([MARKET_CURRENCY[MARKETS[m]] for m in c_market[o_contact]])
    amount_usd = np.array([SIZE_DEAL_USD[s] for s in o_size]) * rng.lognormal(0, 0.45, n_o)
    o_amount = np.round(amount_usd / fx_lookup(fx, o_currency, o_created), 2)

    # ---- Touchpoints: opportunity journeys, lead journeys, background noise -------------
    n_t = cfg.touchpoints
    k_opp = rng.poisson(5, n_o) + 1
    k_opp[rng.random(n_o) < 0.03] = 0                               # unattributable journeys
    lead_has_opp = np.zeros(n_l, dtype=bool)
    lead_has_opp[o_lead] = True
    lead_only = np.flatnonzero(~lead_has_opp)
    k_lead = rng.poisson(1.5, len(lead_only)) + 1
    budget = 0.85 * n_t
    if k_opp.sum() + k_lead.sum() > budget:
        scale = budget / (k_opp.sum() + k_lead.sum())
        k_opp, k_lead = np.floor(k_opp * scale).astype(int), np.floor(k_lead * scale).astype(int)
    n_noise = n_t - int(k_opp.sum()) - int(k_lead.sum())

    jo = np.repeat(np.arange(n_o), k_opp)
    journey_len = np.clip(rng.lognormal(np.log(35), 1.0, n_o), 1, 700)    # tail exceeds lookback
    j_start = o_created - _seconds(journey_len * 86400)
    j_end = pd.DatetimeIndex(np.minimum(o_planned_close.asi8, (end - pd.Timedelta(hours=1)).value))
    t_opp = _uniform_between(rng, j_start[jo], j_end[jo])
    jl = np.repeat(lead_only, k_lead)
    t_lead = _uniform_between(rng, l_created[jl] - pd.Timedelta(days=60),
                              pd.DatetimeIndex(np.minimum((l_created[jl] + pd.Timedelta(days=30)).asi8, (end - pd.Timedelta(hours=1)).value)))
    noise_contact = rng.integers(0, n_c, n_noise)
    t_noise = _uniform_between(rng, c_created[noise_contact], pd.DatetimeIndex([end - pd.Timedelta(hours=1)] * n_noise))
    t_contact = np.concatenate([o_contact[jo], l_contact[jl], noise_contact])
    t_time = pd.DatetimeIndex(np.concatenate([t_opp.asi8, t_lead.asi8, t_noise.asi8]))
    floor_ts = start + pd.Timedelta(days=3)
    t_time = pd.DatetimeIndex(np.where(t_time < floor_ts, (floor_ts + _seconds(rng.integers(0, 7 * 86400, len(t_time)))).asi8, t_time.asi8))
    t_camp = _assign_campaigns(rng, t_time, c_market[t_contact], cmeta)
    t_chan = cmeta["channel"][t_camp]
    t_type = np.array([CHANNELS[c][4][rng.integers(0, len(CHANNELS[c][4]))] for c in t_chan], dtype=object)
    creatives_by_campaign = creative.groupby("campaign_id")["creative_id"].apply(list).to_dict()
    camp_ids = campaign.campaign_id.to_numpy()
    t_creative = np.array([None if t_type[i] in OFFLINE_TOUCH_TYPES else
                           creatives_by_campaign[camp_ids[t_camp[i]]][rng.integers(0, len(creatives_by_campaign[camp_ids[t_camp[i]]]))]
                           for i in range(len(t_time))], dtype=object)

    # Contact.created_at is derived: never later than the first observed touch.
    first_touch = pd.Series(t_time.asi8).groupby(t_contact).min()
    created_ns = c_created.asi8.copy()
    idx = first_touch.index.to_numpy()
    created_ns[idx] = np.minimum(created_ns[idx], first_touch.to_numpy() - 3600 * 10**9)
    c_created = pd.DatetimeIndex(created_ns).floor("s")

    touch = pd.DataFrame({"contact_idx": t_contact, "campaign_idx": t_camp, "channel_idx": t_chan,
                          "ts": t_time, "touch_type": t_type, "creative_id": t_creative})
    touch = touch.sort_values(["contact_idx", "ts"], kind="mergesort").reset_index(drop=True)

    # ---- Rule-derived outcome: win probability from pre-close channel exposure ---------
    closed_idx = np.flatnonzero(~o_open)
    opp_frame = pd.DataFrame({"opp_idx": closed_idx, "contact_idx": o_contact[closed_idx], "close": o_planned_close[closed_idx]})
    exposure = opp_frame.merge(touch[["contact_idx", "channel_idx", "ts"]], on="contact_idx")
    exposure = exposure[exposure.ts <= exposure.close]
    counts = exposure.groupby(["opp_idx", "channel_idx"]).size().clip(upper=TOUCH_CAP_PER_CHANNEL).unstack(fill_value=0)
    counts = counts.reindex(index=closed_idx, columns=range(len(CHANNELS)), fill_value=0)
    beta = np.array([TRUE_CHANNEL_EFFECT[c[0]] for c in CHANNELS])
    contrib = counts.to_numpy() * beta
    logit = WIN_INTERCEPT + contrib.sum(axis=1) + np.array([SIZE_LOGIT[s] for s in o_size[closed_idx]])
    p_win = 1 / (1 + np.exp(-logit))
    won_closed = rng.random(len(closed_idx)) < p_win
    o_stage = np.empty(n_o, dtype=object)
    o_stage[closed_idx] = np.where(won_closed, "closed_won", "closed_lost")
    elapsed = (end - o_created).total_seconds().to_numpy() / np.maximum((o_planned_close - o_created).total_seconds().to_numpy(), 1)
    open_idx = np.flatnonzero(o_open)
    o_stage[open_idx] = np.where(elapsed[open_idx] < .15, "prospecting", np.where(elapsed[open_idx] < .6, "qualified", "proposal"))
    o_closed_at = pd.DatetimeIndex(np.where(o_open, pd.NaT.value, o_planned_close.asi8))
    lost_skips_proposal = rng.random(n_o) < 0.5

    # ---- IDs ------------------------------------------------------------------------
    contact_ids = np.array(_seq_ids("CON", n_c), dtype=object)
    lead_order = np.argsort(l_created.asi8, kind="mergesort")
    lead_ids = np.empty(n_l, dtype=object)
    lead_ids[lead_order] = _seq_ids("LEAD", n_l)
    opp_order = np.argsort(o_created.asi8, kind="mergesort")
    opp_ids = np.empty(n_o, dtype=object)
    opp_ids[opp_order] = _seq_ids("OPP", n_o)

    touch["contact_id"] = contact_ids[touch.contact_idx]
    seq = touch.groupby("contact_idx").cumcount() + 1
    touch["touchpoint_id"] = [f"TOUCH-{c}-{k:06d}" for c, k in zip(touch.contact_id, seq)]

    # Platform-reported conversions: ad platforms claim the last paid-digital touch before close.
    won_idx = closed_idx[won_closed]
    claims = pd.DataFrame({"opp_idx": won_idx, "contact_idx": o_contact[won_idx], "close": o_planned_close[won_idx]})
    paid = touch[touch.channel_idx.isin(PAID_DIGITAL)].reset_index().merge(claims, on="contact_idx")
    paid = paid[paid.ts <= paid.close].sort_values("ts").groupby("opp_idx").tail(1)
    paid = paid[rng.random(len(paid)) < 0.7]
    touch["reported_conversion_id"] = None
    touch.loc[paid["index"].to_numpy(), "reported_conversion_id"] = opp_ids[paid.opp_idx.to_numpy()]

    # ---- Ground-truth channel contribution (evaluation label only) ----------------------
    share = contrib[won_closed] / np.where(contrib[won_closed].sum(axis=1, keepdims=True) > 0, contrib[won_closed].sum(axis=1, keepdims=True), 1)
    won_usd = o_amount[won_idx] * fx_lookup(fx, o_currency[won_idx], o_planned_close[won_idx])
    true_rev = (share * won_usd[:, None]).sum(axis=0)
    gt_channel = pd.DataFrame({"channel_id": [c[0] for c in CHANNELS], "channel_name": CHANNEL_NAMES,
                               "true_effect_logit": beta, "true_credited_revenue_usd": np.round(true_rev, 2)})
    gt_channel["true_share"] = (gt_channel.true_credited_revenue_usd / gt_channel.true_credited_revenue_usd.sum()).round(6)
    gt_channel["unattributable_won_revenue_usd"] = round(float(won_usd[contrib[won_closed].sum(axis=1) == 0].sum()), 2)

    # ---- Web sessions ---------------------------------------------------------------
    n_s = cfg.sessions
    web_leads = np.flatnonzero(l_source == "web_form")
    n_anon = int(round(0.06 * n_s))
    n_other = max(n_s - len(web_leads) - n_anon, 0)
    s_lead_start = pd.DatetimeIndex(np.maximum((l_created[web_leads] - _seconds(rng.integers(120, 45 * 60, len(web_leads)))).asi8,
                                               c_created[l_contact[web_leads]].asi8))
    other_contact = rng.integers(0, n_c, n_other)
    s_other_start = _uniform_between(rng, c_created[other_contact], pd.DatetimeIndex([end - pd.Timedelta(minutes=30)] * n_other))
    s_anon_start = _uniform_between(rng, pd.DatetimeIndex([start] * n_anon), pd.DatetimeIndex([end - pd.Timedelta(minutes=30)] * n_anon))
    s_contact = np.concatenate([l_contact[web_leads], other_contact, np.full(n_anon, -1)])
    s_start = pd.DatetimeIndex(np.concatenate([s_lead_start.asi8, s_other_start.asi8, s_anon_start.asi8])).floor("s")
    s_evidence = np.concatenate([np.full(len(web_leads), "form_submit", dtype=object),
                                 rng.choice(["login", "email_click_id", "cookie_match"], n_other, p=[.4, .35, .25]).astype(object),
                                 np.full(n_anon, "none", dtype=object)])
    s_lead_ref = np.concatenate([web_leads, np.full(n_other + n_anon, -1)])
    s_order = np.argsort(s_start.asi8, kind="mergesort")
    session_ids_sorted = np.array(_seq_ids("SES", len(s_start)), dtype=object)
    s_ids = np.empty(len(s_start), dtype=object)
    s_ids[s_order] = session_ids_sorted
    duration = _seconds(np.clip(rng.lognormal(np.log(240), 1.0, len(s_start)), 10, 4 * 3600))
    session = pd.DataFrame({
        "session_id": s_ids,
        "contact_id": np.where(s_contact >= 0, contact_ids[np.maximum(s_contact, 0)], None),
        "anonymous_id": [f"ANON-{x:010d}" for x in rng.integers(0, 10**10, len(s_start))],
        "started_at": _fmt(s_start), "ended_at": _fmt((s_start + duration).floor("s")),
        "page_views": rng.poisson(3, len(s_start)) + 1,
        "traffic_channel_id": rng.choice(channel.channel_id[:6], len(s_start), p=[.25, .18, .15, .30, .05, .07]),
        "device": rng.choice(DEVICES, len(s_start), p=[.55, .38, .07]),
        "identity_evidence": s_evidence,
    }).iloc[s_order].reset_index(drop=True)
    lead_session = np.full(n_l, None, dtype=object)
    lead_session[web_leads] = s_ids[: len(web_leads)]

    # ---- Leads (status derived by rule) -------------------------------------------------
    touches_after_lead = (pd.DataFrame({"lead_idx": np.arange(n_l), "contact_idx": l_contact, "lc": l_created})
                          .merge(touch[["contact_idx", "ts"]], on="contact_idx"))
    touches_after_lead = touches_after_lead[touches_after_lead.ts >= touches_after_lead.lc].groupby("lead_idx").size()
    n_after = touches_after_lead.reindex(range(n_l), fill_value=0).to_numpy()
    lead_age = (end - l_created).days.to_numpy()
    l_status = np.where(lead_has_opp, "converted", np.where(lead_age > 120, "disqualified", np.where(n_after >= 1, "qualified", "new")))
    lead = pd.DataFrame({
        "lead_id": lead_ids, "contact_id": contact_ids[l_contact], "session_id": lead_session,
        "created_at": _fmt(l_created), "source_system": l_source,
        "identity_link_method": [LEAD_LINK_METHOD[s] for s in l_source], "status": l_status,
    }).iloc[lead_order].reset_index(drop=True)

    # ---- Opportunities + append-only lifecycle history -----------------------------------
    opportunity = pd.DataFrame({
        "opportunity_id": opp_ids, "contact_id": contact_ids[o_contact], "lead_id": lead_ids[o_lead],
        "created_at": _fmt(o_created), "closed_at": _fmt(o_closed_at), "stage": o_stage,
        "amount": o_amount, "currency": o_currency, "company_size": o_size,
    }).iloc[opp_order].reset_index(drop=True)
    cyc = (o_planned_close - o_created)
    t_qual = (o_created + cyc * 0.15).floor("s")
    t_prop = (o_created + cyc * 0.60).floor("s")
    ev = []
    for i in range(n_o):
        path = [("created", None, "prospecting", o_created[i])]
        st = o_stage[i]
        if st in ("qualified", "proposal", "closed_won", "closed_lost"):
            path.append(("stage_changed", "prospecting", "qualified", t_qual[i]))
        if st in ("proposal", "closed_won") or (st == "closed_lost" and not lost_skips_proposal[i]):
            path.append(("stage_changed", "qualified", "proposal", t_prop[i]))
        if st in ("closed_won", "closed_lost"):
            path.append(("closed", path[-1][2], st, o_planned_close[i]))
        for k, (etype, frm, to, ts) in enumerate(path, 1):
            ev.append((f"OPPEVT-{opp_ids[i]}-{k:02d}", opp_ids[i], k, etype, frm, to, ts, "crm_sync"))
    opportunity_event = pd.DataFrame(ev, columns=["event_id", "opportunity_id", "sequence", "event_type",
                                                  "from_stage", "to_stage", "occurred_at", "actor"])
    opportunity_event["occurred_at"] = _fmt(opportunity_event["occurred_at"])
    opportunity_event = opportunity_event.sort_values(["opportunity_id", "sequence"]).reset_index(drop=True)

    # ---- Orders (closed revenue; one per won opportunity) ---------------------------------
    won_sorted = opportunity[opportunity.stage == "closed_won"].reset_index(drop=True)
    recognized = pd.to_datetime(won_sorted.closed_at) + _seconds(rng.integers(0, 72 * 3600, len(won_sorted)))
    order = pd.DataFrame({
        "order_id": _seq_ids("ORD", len(won_sorted)), "opportunity_id": won_sorted.opportunity_id,
        "external_invoice_id": _seq_ids("INV", len(won_sorted)), "revenue_amount": won_sorted.amount,
        "currency": won_sorted.currency, "recognized_at": _fmt(pd.DatetimeIndex(recognized).floor("s")),
        "source_system": "billing",
    })

    # ---- Cost records: monthly spend per campaign, derived from budget and flight dates ------
    cost_rows = []
    for i, c in enumerate(campaign.itertuples(index=False)):
        cs = cmeta["starts"][i]
        ce = min(cmeta["ends"][i], end)
        total_days = max((cmeta["ends"][i] - cs).days, 1) if not c.is_evergreen else max((pd.Timestamp(f"{end.year}-12-31") - cs).days, 1)
        for p in pd.period_range(cs, ce, freq="M"):
            m_start, m_end = max(p.start_time, cs), min(p.end_time, ce)
            days = max((m_end - m_start).days + 1, 1)
            amount = c.budget_amount * days / total_days * rng.uniform(.75, 1.02)
            cost_rows.append((c.campaign_id, p.strftime("%Y-%m"), m_start.floor("s").strftime(TS_FMT),
                              round(float(amount), 2), c.currency, CHANNELS[cmeta["channel"][i]][6], "finance_ledger"))
        if rng.random() < 0.08:
            cost_rows.append((c.campaign_id, cs.strftime("%Y-%m"), cs.floor("s").strftime(TS_FMT),
                              round(float(c.budget_amount * .05), 2), c.currency, "agency_fee", "finance_ledger"))
    cost = pd.DataFrame(cost_rows, columns=["campaign_id", "period_month", "incurred_at", "amount", "currency", "cost_type", "source_system"])
    cost.insert(0, "cost_record_id", _seq_ids("COST", len(cost)))

    touchpoint = pd.DataFrame({
        "touchpoint_id": touch.touchpoint_id, "contact_id": touch.contact_id,
        "campaign_id": camp_ids[touch.campaign_idx], "channel_id": channel.channel_id.to_numpy()[touch.channel_idx],
        "creative_id": touch.creative_id, "occurred_at": _fmt(touch.ts), "touch_type": touch.touch_type,
        "is_offline": touch.touch_type.isin(OFFLINE_TOUCH_TYPES).to_numpy(),
        "source_system": [CHANNELS[c][5] for c in touch.channel_idx],
        "reported_conversion_id": touch.reported_conversion_id,
    })

    # ---- Contact lifecycle (derived) -----------------------------------------------------
    last_touch = pd.Series(_ns(touch.ts).asi8).groupby(touch.contact_idx.to_numpy()).max()
    last_act = np.full(n_c, c_created.asi8, dtype="int64")
    last_act[last_touch.index.to_numpy()] = np.maximum(last_act[last_touch.index.to_numpy()], last_touch.to_numpy())
    has_lead = np.zeros(n_c, bool); has_lead[l_contact] = True
    has_open = np.zeros(n_c, bool); has_open[o_contact[o_open]] = True
    has_won = np.zeros(n_c, bool); has_won[o_contact[o_stage == "closed_won"]] = True
    inactive = last_act < (end - pd.Timedelta(days=180)).value
    lifecycle = np.where(has_won, "customer", np.where(has_open, "opportunity", np.where(inactive, "inactive", np.where(has_lead, "lead", "prospect"))))
    contact = pd.DataFrame({
        "contact_id": contact_ids, "created_at": _fmt(c_created), "country": c_country,
        "market": [MARKETS[m] for m in c_market], "company_size": np.array(COMPANY_SIZES)[c_size],
        "consent_status": rng.choice(["granted", "denied", "unknown"], n_c, p=[.80, .07, .13]),
        "lifecycle_state": lifecycle,
    })

    tables = {"Channel": channel, "FxRate": fx, "Campaign": campaign, "Creative": creative, "Audience": audience,
              "CampaignAudience": campaign_audience, "Contact": contact, "WebSession": session, "Lead": lead,
              "Opportunity": opportunity, "OpportunityEvent": opportunity_event, "Touchpoint": touchpoint,
              "Order": order, "CostRecord": cost}
    gt_rows = _inject_anomalies(rng, cfg, tables, end)
    ground_truth = pd.DataFrame(gt_rows, columns=["ground_truth_id", "issue_type", "record_type", "record_id",
                                                  "related_id", "expected_decision", "injection_note"])
    return GeneratedDataset(tables=tables, ground_truth={"ground_truth": ground_truth, "channel_contribution": gt_channel})


# ----------------------------------------------------------------------- anomaly injection
EXPECTED_DECISION = {
    "duplicate_revenue": "exclude_from_recognized_revenue",
    "post_conversion_touch": "exclude_from_attribution",
    "currency_mismatch": "quarantine_cost_from_roi",
    "campaign_cost_omitted": "roi_unavailable_missing_cost",
    "anonymous_session_without_evidence": "reject_identity_link",
}


def _inject_anomalies(rng, cfg: DatasetConfig, t: dict[str, pd.DataFrame], end: pd.Timestamp) -> list[dict]:
    gt: list[dict] = []

    def label(issue, rtype, rid, related, note):
        gt.append({"ground_truth_id": f"GT-{ID_YEAR}-{len(gt) + 1:06d}", "issue_type": issue, "record_type": rtype,
                   "record_id": rid, "related_id": related, "expected_decision": EXPECTED_DECISION[issue], "injection_note": note})

    campaign, order, cost, touch, lead, session, opp = (t["Campaign"], t["Order"], t["CostRecord"], t["Touchpoint"],
                                                        t["Lead"], t["WebSession"], t["Opportunity"])

    # 1. Revenue counted more than once: re-imported invoice (A) or second invoice for same deal (B).
    k = _n_anomalies(cfg, "duplicate_revenue", len(order))
    picks = rng.choice(len(order), k, replace=False)
    new_orders, n_ord, n_inv = [], len(order), len(order)
    for j, p in enumerate(sorted(picks)):
        src = order.iloc[p].to_dict()
        n_ord += 1
        dup = dict(src, order_id=f"ORD-{ID_YEAR}-{n_ord:06d}")
        dup["recognized_at"] = (pd.Timestamp(src["recognized_at"]) + pd.Timedelta(days=int(rng.integers(1, 15)))).strftime(TS_FMT)
        if j % 2 == 0:
            dup["source_system"] = "billing_reimport"
            note = "same external_invoice_id re-imported"
        else:
            n_inv += 1
            dup["external_invoice_id"] = f"INV-{ID_YEAR}-{n_inv:06d}"
            dup["source_system"] = "manual_invoice"
            note = "second invoice booked for an already-invoiced opportunity"
        new_orders.append(dup)
        label("duplicate_revenue", "Order", dup["order_id"], src["order_id"], note)
    t["Order"] = pd.concat([order, pd.DataFrame(new_orders)], ignore_index=True)

    # 2. Touchpoint after conversion that an ad platform claims as converting.
    won = opp[(opp.stage == "closed_won") & (pd.to_datetime(opp.closed_at) < end - pd.Timedelta(days=25))]
    k = _n_anomalies(cfg, "post_conversion_touch", len(won))
    ever_paid = campaign[campaign.is_evergreen & campaign.channel_id.isin([CHANNELS[i][0] for i in PAID_DIGITAL])]
    contact_market = t["Contact"].set_index("contact_id")["market"]
    next_seq = touch.groupby("contact_id").size()
    creatives = t["Creative"].groupby("campaign_id")["creative_id"].first()
    new_touch = []
    for _, o in won.iloc[np.sort(rng.choice(len(won), k, replace=False))].iterrows():
        options = ever_paid[ever_paid.market == contact_market[o.contact_id]]
        c = (options if len(options) else ever_paid).iloc[int(rng.integers(0, len(options) or len(ever_paid)))]
        seq = int(next_seq.get(o.contact_id, 0)) + 1
        next_seq[o.contact_id] = seq
        ts = pd.Timestamp(o.closed_at) + pd.Timedelta(seconds=int(rng.integers(3600, 20 * 86400)))
        row = {"touchpoint_id": f"TOUCH-{o.contact_id}-{seq:06d}", "contact_id": o.contact_id, "campaign_id": c.campaign_id,
               "channel_id": c.channel_id, "creative_id": creatives[c.campaign_id], "occurred_at": ts.strftime(TS_FMT),
               "touch_type": "ad_click", "is_offline": False, "source_system": "ad_platform",
               "reported_conversion_id": o.opportunity_id}
        new_touch.append(row)
        label("post_conversion_touch", "Touchpoint", row["touchpoint_id"], o.opportunity_id,
              "ad platform claims conversion for a click after the opportunity closed")
    t["Touchpoint"] = pd.concat([touch, pd.DataFrame(new_touch)], ignore_index=True)

    # 3/4. Campaign costs omitted (cost rows removed) and spend currency mismatch (new rows).
    used = set(t["Touchpoint"].campaign_id)
    candidates = campaign[~campaign.is_evergreen & campaign.campaign_id.isin(used)].campaign_id.to_numpy()
    k_omit = _n_anomalies(cfg, "campaign_cost_omitted", len(campaign))
    omitted = set(rng.choice(candidates, min(k_omit, len(candidates)), replace=False))
    for cid in sorted(omitted):
        label("campaign_cost_omitted", "Campaign", cid, None, f"{int((cost.campaign_id == cid).sum())} cost rows withheld from ledger extract")
    cost = cost[~cost.campaign_id.isin(omitted)].reset_index(drop=True)
    k_mis = _n_anomalies(cfg, "currency_mismatch", len(cost))
    eligible = cost[~cost.campaign_id.isin(omitted)]
    mis_rows, n_cost = [], len(t["CostRecord"])
    for _, src in eligible.iloc[np.sort(rng.choice(len(eligible), k_mis, replace=False))].iterrows():
        n_cost += 1
        wrong = [c for c in CURRENCIES if c != src.currency][int(rng.integers(0, 2))]
        row = {"cost_record_id": f"COST-{ID_YEAR}-{n_cost:06d}", "campaign_id": src.campaign_id, "period_month": src.period_month,
               "incurred_at": src.incurred_at, "amount": round(float(src.amount * rng.uniform(.05, .25)), 2), "currency": wrong,
               "cost_type": "manual_adjustment", "source_system": "spreadsheet_upload"}
        mis_rows.append(row)
        label("currency_mismatch", "CostRecord", row["cost_record_id"], src.campaign_id,
              f"adjustment booked in {wrong}; campaign is contracted in {src.currency}")
    t["CostRecord"] = pd.concat([cost, pd.DataFrame(mis_rows)], ignore_index=True)

    # 5. Lead linked to an anonymous session that carries no identity evidence.
    anon = session[session.identity_evidence == "none"]
    k = _n_anomalies(cfg, "anonymous_session_without_evidence", len(lead))
    contacts = t["Contact"]
    c_created = pd.to_datetime(contacts.created_at)
    new_leads = []
    for j, (_, s) in enumerate(anon.iloc[np.sort(rng.choice(len(anon), min(k, len(anon)), replace=False))].iterrows()):
        s_start = pd.Timestamp(s.started_at)
        pool = contacts.contact_id[c_created <= s_start].to_numpy()
        if not len(pool):
            continue
        lid = f"LEAD-{ID_YEAR}-{len(lead) + j + 1:06d}"
        new_leads.append({"lead_id": lid, "contact_id": pool[int(rng.integers(0, len(pool)))], "session_id": s.session_id,
                          "created_at": (s_start + pd.Timedelta(minutes=5)).strftime(TS_FMT), "source_system": "web_form",
                          "identity_link_method": "probabilistic_device_match", "status": "new"})
        label("anonymous_session_without_evidence", "Lead", lid, s.session_id,
              "lead stitched to an anonymous session by device fingerprint only")
    t["Lead"] = pd.concat([lead, pd.DataFrame(new_leads)], ignore_index=True)
    return gt
