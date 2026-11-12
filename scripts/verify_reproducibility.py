"""Generate twice with the same seed and compare row counts + SHA-256 content digests.

    python -m scripts.verify_reproducibility --scale full --seed 20261022

Thin wrapper around `python -m attribution_engine.cli verify-reproducibility`.
"""
import sys

from attribution_engine.cli import main

if __name__ == "__main__":
    sys.exit(main(["verify-reproducibility"] + sys.argv[1:]))
