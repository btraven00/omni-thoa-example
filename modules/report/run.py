#!/usr/bin/env python
"""REPORT stage (gather): one HTML report per dataset, rendered with Quarto.

Gets every member of the dataset's group: the DATA truth table and, for each
FILT module, its kept cell and feature ids. Reports cells and genes kept per
FILT module and cell retention per truth label (the Sample for be1).
"""

import argparse
import gzip
import subprocess
import sys
from pathlib import Path

import pandas as pd


def parse_args():
    p = argparse.ArgumentParser(description="REPORT module (quarto)")
    p.add_argument("--output_dir", required=True)
    p.add_argument("--name", required=True)
    # gather: one flag per gathered output id, every member path after it
    p.add_argument("--rawdata_clusters_truth", nargs="+", required=True)
    p.add_argument("--filtered_cellids", nargs="+", required=True)
    p.add_argument("--filtered_featureids", nargs="+", required=True)
    # api >= 0.8: --output id=PATH
    p.add_argument("--output", dest="outputs", action="append", default=[], metavar="ID=PATH")
    args = p.parse_args()
    args.outputs = dict(o.split("=", 1) for o in args.outputs)
    return args


def read_ids(path):
    with gzip.open(path, "rt") as f:
        return [line.rstrip("\n") for line in f if line.strip()]


def member(path, suffix):
    """Module id of a member: outputs are named {module.id}<suffix>."""
    return Path(path).name.removesuffix(suffix)


def main():
    args = parse_args()
    out = Path(args.outputs.get("report_html") or Path(args.output_dir) / f"{args.name}_report.html")
    if len(args.rawdata_clusters_truth) != 1:
        sys.exit(f"expected one DATA member per group, got {len(args.rawdata_clusters_truth)}")
    truth_path = args.rawdata_clusters_truth[0]
    dataset = member(truth_path, ".clusters_truth.tsv")
    truth = pd.read_csv(truth_path, sep="\t", dtype=str, keep_default_na=False)

    features = {member(p, "_featureids.txt.gz"): read_ids(p) for p in args.filtered_featureids}
    summary, retention = [], {}
    for p in sorted(args.filtered_cellids):
        mod = member(p, "_cellids.txt.gz")
        kept = set(read_ids(p))
        summary.append({
            "FILT module": mod,
            "cells kept": len(kept),
            "of": len(truth),
            "% cells": round(100 * len(kept) / len(truth), 1),
            "genes kept": len(features.get(mod, [])),
        })
        retention[mod] = truth.assign(kept=truth["cell_id"].isin(kept)).groupby("truths")["kept"].mean().mul(100).round(1)

    by_label = pd.DataFrame({"cells": truth.groupby("truths").size()})
    for mod, pct in retention.items():
        by_label[f"% kept ({mod})"] = pct
    by_label.index.name = "truth label"

    qmd = out.with_suffix(".qmd")
    qmd.write_text(f"""---
title: "{dataset}: filtering report"
format:
  html:
    embed-resources: true
    toc: true
---

Dataset **{dataset}**: {len(truth)} cells in `{Path(truth_path).name}`,
{truth['truths'].nunique()} truth labels.

## Cells and genes kept

{pd.DataFrame(summary).to_markdown(index=False)}

## Cells kept per truth label

{by_label.reset_index().to_markdown(index=False)}

## Inputs

{chr(10).join(f"- `{p}`" for p in [truth_path, *args.filtered_cellids, *args.filtered_featureids])}
""")
    # No code cells: quarto only runs pandoc, so the env needs no jupyter.
    subprocess.run(["quarto", "render", qmd.name, "--to", "html"], cwd=qmd.parent, check=True)
    print(f"wrote {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
