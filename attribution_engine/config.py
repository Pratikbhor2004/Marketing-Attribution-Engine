"""Generator and engine configuration.

One dataclass drives both the starter and the full dataset so that both are produced by
exactly the same code path; only the volumes differ.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

DEFAULT_SEED = 20261022


@dataclass(frozen=True)
class DatasetConfig:
    seed: int = DEFAULT_SEED
    # --- baseline volumes -------------------------------------------------------------
    campaigns: int = 500
    contacts: int = 50_000
    touchpoints: int = 200_000
    opportunities: int = 10_000
    sessions: int = 100_000
    leads: int = 15_000
    # --- calendar ---------------------------------------------------------------------
    start_date: str = "2025-01-01"
    end_date: str = "2026-10-31"          # data "as-of" date; later closes stay open
    evaluation_train_cutoff: str = "2026-07-01"
    # --- attribution rules ------------------------------------------------------------
    lookback_days: int = 365               # touches older than this before conversion are ineligible
    time_decay_half_life_days: float = 14.0
    position_first_weight: float = 0.4     # U-shaped / position-based
    position_last_weight: float = 0.4
    reporting_currency: str = "USD"
    # --- anomaly injection rates (share of the relevant baseline population) ---------
    anomaly_rates: dict = field(default_factory=lambda: {
        "duplicate_revenue": 0.004,                 # of valid orders
        "post_conversion_touch": 0.004,             # of won opportunities
        "currency_mismatch": 0.01,                  # of cost rows
        "campaign_cost_omitted": 0.02,              # of campaigns
        "anonymous_session_without_evidence": 0.002,  # of leads
    })
    min_anomalies_per_type: int = 2

    @classmethod
    def starter(cls, seed: int = DEFAULT_SEED) -> "DatasetConfig":
        return cls(seed=seed, campaigns=40, contacts=1_000, touchpoints=4_000,
                   opportunities=200, sessions=2_000, leads=300)

    @classmethod
    def for_scale(cls, scale: str, seed: int = DEFAULT_SEED) -> "DatasetConfig":
        if scale == "starter":
            return cls.starter(seed)
        if scale == "full":
            return cls(seed=seed)
        raise ValueError(f"Unknown scale {scale!r}; choose 'starter' or 'full'")

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class AttributionParams:
    """Parameters an analyst may change at attribution time without regenerating data."""
    lookback_days: int = 365
    time_decay_half_life_days: float = 14.0
    position_first_weight: float = 0.4
    position_last_weight: float = 0.4
    include_offline: bool = True
    reporting_currency: str = "USD"

    @classmethod
    def from_config(cls, cfg: DatasetConfig) -> "AttributionParams":
        return cls(lookback_days=cfg.lookback_days, time_decay_half_life_days=cfg.time_decay_half_life_days,
                   position_first_weight=cfg.position_first_weight, position_last_weight=cfg.position_last_weight,
                   reporting_currency=cfg.reporting_currency)

    def validate(self) -> None:
        if self.lookback_days < 1:
            raise ValueError("lookback_days must be >= 1")
        if self.time_decay_half_life_days <= 0:
            raise ValueError("time_decay_half_life_days must be > 0")
        if not (0 <= self.position_first_weight <= 1 and 0 <= self.position_last_weight <= 1):
            raise ValueError("position weights must be between 0 and 1")
        if self.position_first_weight + self.position_last_weight > 1:
            raise ValueError("position_first_weight + position_last_weight must be <= 1")
