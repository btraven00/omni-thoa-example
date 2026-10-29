#!/usr/bin/env python
"""DATA stage, implemented with hapiq.

Writes the four DATA outputs of the split-stages plan, equivalent to
omni-scrna/1-data@4e4f1a5, but the raw files come through hapiq: from the
thoa data lake when HAPIQ_LOCK names a lockfile that pins the dataset,
otherwise from upstream (GEO).

Only be1 is implemented: GSE243665, eight 10x samples as MEX files.
"""

import argparse
import shutil
import sys
from pathlib import Path

import anndata as ad
import hapiq
import numpy as np
import pandas as pd
import scipy.io
import scipy.sparse as sp
import yaml

# be1 as in the R module: GEO supplementary MEX files, one triplet per sample.
DATASETS = {
    "be1": {
        "source": "geo",
        "id": "GSE243665",
        "include_ext": [".mtx.gz", ".tsv.gz"],
        "qc_thresholds": {"nFeature": (200, 5000), "nCount": (None, 25000), "percent.mt": (None, 5)},
    },
}


def parse_args():
    p = argparse.ArgumentParser(description="DATA module (hapiq)")
    p.add_argument("--output_dir", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--dataset_name", required=True)
    for k in ("batch_var", "sample_var", "labels_var"):
        p.add_argument(f"--{k}")
    args = p.parse_args()
    # Same null handling as the R module.
    for k in ("batch_var", "sample_var", "labels_var"):
        v = getattr(args, k)
        if v is not None and v.lower() in ("none", "null", ""):
            setattr(args, k, None)
    return args


def make_unique(names):
    """R's make.unique: the second "A" becomes "A.1", the third "A.2"."""
    seen, out = {}, []
    taken = set(names)
    for n in names:
        if n not in seen:
            seen[n] = 0
            out.append(n)
            continue
        while True:
            seen[n] += 1
            cand = f"{n}.{seen[n]}"
            if cand not in taken:
                break
        taken.add(cand)
        out.append(cand)
    return out


def read_sample(files, sample):
    """One sample as DropletUtils::read10xCounts(col.names = TRUE) reads it."""
    feats = pd.read_csv(files["features"], sep="\t", header=None, dtype=str)
    barcodes = pd.read_csv(files["barcodes"], sep="\t", header=None, dtype=str)[0]
    m = scipy.io.mmread(files["matrix"]).T.tocsr()  # genes x cells -> cells x genes
    obs = pd.DataFrame({"Sample": sample, "Barcode": barcodes.values}, index=barcodes.values)
    var = pd.DataFrame({"ID": feats[0].values, "Symbol": feats[1].values, "Type": feats[2].values})
    return ad.AnnData(X=m, obs=obs, var=var)


def load_be1(raw_dir):
    # R: list.files(pattern = "\\.(mtx|tsv)\\.gz$"), samples in file order.
    files = sorted(p for p in raw_dir.rglob("*") if p.name.endswith((".mtx.gz", ".tsv.gz")))
    samples = {}
    for f in files:
        _, sample, kind = f.name.split("_", 2)  # GSE243665_<sample>_<kind>.<ext>.gz
        samples.setdefault(sample, {})[kind.split(".")[0]] = f
    parts = [read_sample(fs, s) for s, fs in samples.items()]
    # cbind(): features must agree across samples, as the R code assumes.
    for p in parts[1:]:
        if not p.var["ID"].equals(parts[0].var["ID"]):
            sys.exit("feature lists differ between samples")
    # As anndataR writes an SCE with only a counts assay: no X, a float64
    # CSR "counts" layer.
    adata = ad.AnnData(
        obs=pd.concat([p.obs for p in parts]),
        var=parts[0].var,
        layers={"counts": sp.vstack([p.X for p in parts]).tocsr().astype(np.float64)},
    )
    adata.var_names = make_unique(list(adata.var["Symbol"]))
    adata.obs["clusters.truth"] = adata.obs["Sample"]
    return adata[:, (adata.var["Type"] == "Gene Expression").values].copy()


def qc_frame(spec):
    rows = [(k, np.nan if lo is None else lo, np.nan if hi is None else hi) for k, (lo, hi) in spec.items()]
    df = pd.DataFrame(rows, columns=["metric", "min", "max"], index=[str(i + 1) for i in range(len(rows))])
    return df.astype({"min": "float64", "max": "float64"})  # R numeric


def main():
    args = parse_args()
    if args.dataset_name not in DATASETS:
        sys.exit(f"unknown dataset {args.dataset_name!r}; this module implements {sorted(DATASETS)}")
    spec = DATASETS[args.dataset_name]
    out = Path(args.output_dir)
    raw = out / "raw"

    # The fetch: from thoa if HAPIQ_LOCK pins it, else upstream.
    ds = hapiq.Dataset(spec["source"], spec["id"], include_ext=spec["include_ext"])
    res = ds.fetch_result(str(raw))
    print(f"hapiq: {len(res.files)} files into {raw}", file=sys.stderr)

    adata = load_be1(raw)
    shutil.rmtree(raw)

    adata.uns["qc_thresholds"] = qc_frame(spec["qc_thresholds"])

    cols = list(adata.obs.columns)
    for k in ("batch_var", "sample_var", "labels_var"):
        v = getattr(args, k)
        if v is not None and v not in cols:
            sys.exit(f"--{k}='{v}' not found in obs. Available columns: {', '.join(cols)}")
    truth_col = args.labels_var or "clusters.truth"
    adata = adata[adata.obs[truth_col].notna().values].copy()

    x = adata.layers["counts"].data
    if not np.all(x == np.round(x)):
        sys.exit("Dataset contains not integer counts")

    # anndataR keeps strings as strings; anndata would make them categoricals.
    adata.write_h5ad(out / f"{args.name}.h5ad", convert_strings_to_categoricals=False)
    truth = adata.obs[truth_col].astype(str)
    pd.DataFrame({"cell_id": adata.obs_names, "truths": truth.values, "truths_cl": ""}).to_csv(
        out / f"{args.name}.clusters_truth.tsv", sep="\t", index=False
    )
    (out / f"{args.name}.clusters_truth_num.txt").write_text(f"{truth.nunique()}\n")
    props = {k: getattr(args, k) for k in ("batch_var", "sample_var", "labels_var")}
    (out / f"{args.name}_properties.yaml").write_text(yaml.safe_dump(props, sort_keys=False))


if __name__ == "__main__":
    main()
