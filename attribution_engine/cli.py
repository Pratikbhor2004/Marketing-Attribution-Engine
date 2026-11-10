import argparse, json, hashlib
from pathlib import Path
from .config import DatasetConfig
from .generator import generate_dataset, export_dataset, validate_dataset
from .analytics import campaign_roi, evaluation_summary

def main():
    parser=argparse.ArgumentParser(description="Generate and validate synthetic marketing attribution data.")
    parser.add_argument("--scale",choices=["starter","full"],default="starter")
    parser.add_argument("--seed",type=int,default=20261022)
    parser.add_argument("--output",default="data/generated")
    args=parser.parse_args()
    cfg=DatasetConfig.starter(args.seed) if args.scale=="starter" else DatasetConfig(seed=args.seed)
    tables=generate_dataset(cfg)
    report=export_dataset(tables,args.output,cfg)
    roi=campaign_roi(tables)
    roi.to_csv(Path(args.output)/"campaign_roi.csv",index=False)
    summary=evaluation_summary(tables, cfg.evaluation_train_cutoff)
    Path(args.output,"evaluation_summary.json").write_text(json.dumps(summary,indent=2))
    # Refresh manifest after all CLI-level analytical deliverables are written.
    manifest_path = Path(args.output, "source_manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for p in sorted(Path(args.output).iterdir()):
        if p.is_file() and p.name != "source_manifest.json":
            manifest["tables"][p.name] = {"sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "bytes": p.stat().st_size}
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({"validation_passed":report["passed"],"row_counts":report["row_counts"],"evaluation":summary},indent=2))
    if not report["passed"]:
        raise SystemExit("Validation failed; inspect validation_summary.json")

if __name__=="__main__":
    main()
