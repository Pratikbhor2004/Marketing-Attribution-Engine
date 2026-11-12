"""Command-line entry point.

    python -m attribution_engine.cli generate --scale starter|full [--seed N] [--output DIR]
    python -m attribution_engine.cli validate [--db data/generated/attribution.db]
    python -m attribution_engine.cli verify-reproducibility --scale starter|full [--seed N]

`python -m attribution_engine.cli --scale starter` (no sub-command) is kept as an alias for `generate`.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .config import DEFAULT_SEED, DatasetConfig


def _generate(args) -> int:
    from .pipeline import export, run_pipeline
    cfg = DatasetConfig.for_scale(args.scale, args.seed)
    t0 = time.time()
    print(f"[1/3] generating + detecting + attributing ({args.scale}, seed={cfg.seed}) ...", flush=True)
    result = run_pipeline(cfg)
    print(f"[2/3] exporting to {args.output} ...", flush=True)
    formats = tuple(args.formats.split(","))
    manifest = export(result, cfg, args.output, formats)
    v, e = result.validation, result.evaluation
    print(f"[3/3] done in {time.time() - t0:.1f}s", flush=True)
    print(json.dumps({
        "validation_passed": v["passed"], "checks": v["total_checks"], "failed": [c["check"] for c in v["failed_checks"]],
        "row_counts": manifest["row_counts"], "exceptions_detected": e["exceptions_detected"],
        "detection_f1": e["detection_vs_ground_truth"]["overall"]["f1"],
        "reconciliation_passed": e["reconciliation"]["passed"],
        "best_model_vs_ground_truth": e["model_comparison"].get("best_model_vs_ground_truth"),
    }, indent=2))
    return 0 if v["passed"] else 1


def _validate(args) -> int:
    from .detection import detect_exceptions
    from .pipeline import DERIVED_TABLES, SOURCE_TABLES, load_tables
    import pandas as pd
    db = Path(args.db)
    if not db.exists():
        print(f"{db} not found; run `generate` first", file=sys.stderr)
        return 2
    tables = load_tables(db, SOURCE_TABLES + DERIVED_TABLES)
    gt_path = db.parent.parent / "ground_truth" / "ground_truth.csv"
    gt = pd.read_csv(gt_path) if gt_path.exists() else None
    from .validation import validate_dataset
    report = validate_dataset(tables, detect_exceptions(tables), gt)
    print(json.dumps({"passed": report["passed"], "total_checks": report["total_checks"], "by_category": report["by_category"],
                      "failed_checks": report["failed_checks"]}, indent=2))
    return 0 if report["passed"] else 1


def _verify(args) -> int:
    from .pipeline import dataset_digests
    cfg = DatasetConfig.for_scale(args.scale, args.seed)
    counts_a, dig_a = dataset_digests(cfg)
    counts_b, dig_b = dataset_digests(cfg)
    mismatched = sorted(k for k in dig_a if dig_a[k] != dig_b.get(k))
    ok = counts_a == counts_b and not mismatched
    report = {"scale": args.scale, "seed": cfg.seed, "reproducible": ok, "tables_compared": len(dig_a),
              "row_counts": counts_a, "mismatched_tables": mismatched,
              "checksums": {k: {"run_1": dig_a[k], "run_2": dig_b[k]} for k in sorted(dig_a)}}
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(("PASS" if ok else "FAIL") + f": {len(dig_a)} tables, two runs with seed {cfg.seed} "
          + ("match exactly (row counts + SHA-256 content digests)." if ok else f"differ: {mismatched}"))
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="attribution-engine", description="Synthetic marketing attribution engine")
    sub = p.add_subparsers(dest="command")
    g = sub.add_parser("generate", help="generate, attribute, validate and export a dataset")
    g.add_argument("--scale", choices=["starter", "full"], default="starter")
    g.add_argument("--seed", type=int, default=DEFAULT_SEED)
    g.add_argument("--output", default="data/generated")
    g.add_argument("--formats", default="csv,json,sqlite,sql", help="comma list of csv,json,sqlite,sql")
    g.set_defaults(func=_generate)
    v = sub.add_parser("validate", help="re-run validation over an exported SQLite database")
    v.add_argument("--db", default="data/generated/attribution.db")
    v.set_defaults(func=_validate)
    r = sub.add_parser("verify-reproducibility", help="generate twice with one seed and compare checksums")
    r.add_argument("--scale", choices=["starter", "full"], default="starter")
    r.add_argument("--seed", type=int, default=DEFAULT_SEED)
    r.add_argument("--report", default="reports/reproducibility_report.json")
    r.set_defaults(func=_verify)
    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0].startswith("-") and argv[0] not in ("-h", "--help"):
        argv = ["generate"] + argv
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
