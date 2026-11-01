#!/usr/bin/env python3
"""Render CLAUSE-specific manifests from a prepared, indexed RAGFlow bundle."""

import argparse
from pathlib import Path
from dcc_preprocess import bundle_lock, load_config
from dcc_harness.preprocessing.clause import render_clause

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--variant", choices=("l1", "l2"))
    args = parser.parse_args()
    with bundle_lock(args.bundle):
        rows = render_clause(
            args.bundle, load_config(args.config.resolve()), variant=args.variant
        )
    print(f"Rendered {len(rows)} CLAUSE RAGFlow tasks")
