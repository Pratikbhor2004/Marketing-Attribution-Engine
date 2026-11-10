from dataclasses import dataclass

@dataclass(frozen=True)
class DatasetConfig:
    seed: int = 20261022
    campaigns: int = 500
    contacts: int = 50_000
    touchpoints: int = 200_000
    opportunities: int = 10_000
    sessions: int = 100_000
    leads: int = 15_000
    evaluation_train_cutoff: str = "2026-07-01"
    start_date: str = "2025-01-01"
    end_date: str = "2026-10-31"

    @classmethod
    def starter(cls, seed: int = 20261022):
        return cls(seed=seed, campaigns=20, contacts=500, touchpoints=2_000,
                   opportunities=100, sessions=1_000, leads=150)
