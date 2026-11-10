"""Generate twice with the same seed and compare stable per-table checksums."""
import argparse
import gc
import hashlib
import pandas as pd
from attribution_engine.config import DatasetConfig
from attribution_engine.generator import generate_dataset


def digest(df: pd.DataFrame) -> str:
    h = hashlib.sha256()
    h.update("|".join(map(str, df.columns)).encode("utf-8"))
    h.update("|".join(map(str, df.dtypes)).encode("utf-8"))
    h.update(pd.util.hash_pandas_object(df, index=True, categorize=True).values.tobytes())
    return h.hexdigest()


parser = argparse.ArgumentParser()
parser.add_argument("--scale", choices=["starter", "full"], default="starter")
parser.add_argument("--seed", type=int, default=20261022)
args = parser.parse_args()
cfg = DatasetConfig.starter(args.seed) if args.scale == "starter" else DatasetConfig(seed=args.seed)

first = generate_dataset(cfg)
first_digests = {name: digest(df) for name, df in first.items()}
first_counts = {name: len(df) for name, df in first.items()}
del first
gc.collect()

second = generate_dataset(cfg)
second_digests = {name: digest(df) for name, df in second.items()}
second_counts = {name: len(df) for name, df in second.items()}

assert first_counts == second_counts, "Row-count mismatch between runs"
for name, checksum in first_digests.items():
    assert checksum == second_digests[name], f"Checksum mismatch: {name}"
print(f"PASS: all {args.scale} tables reproduce exactly with seed {args.seed}; row counts and checksums match.")
