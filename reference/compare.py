"""Compare DATA outputs of the hapiq module with the R reference.

    python reference/compare.py <reference_dir> <candidate_dir> [name]

Text outputs must be byte-identical. The h5ad must hold the same cells, genes,
obs/var columns, uns.qc_thresholds and counts (exactly); the HDF5 bytes may
differ, since anndataR and anndata write different metadata.
"""

import sys
from pathlib import Path

import anndata as ad
import numpy as np
import pandas as pd


def main(ref, cand, name="be1"):
    ref, cand = Path(ref), Path(cand)
    bad = []
    for suffix in (".clusters_truth.tsv", ".clusters_truth_num.txt", "_properties.yaml"):
        a, b = (ref / f"{name}{suffix}").read_bytes(), (cand / f"{name}{suffix}").read_bytes()
        print(f"{'ok ' if a == b else 'DIFF'} {name}{suffix}")
        bad += [] if a == b else [suffix]

    r, c = ad.read_h5ad(ref / f"{name}.h5ad"), ad.read_h5ad(cand / f"{name}.h5ad")
    checks = {
        "X absent": r.X is None and c.X is None,
        "layers": sorted(r.layers) == sorted(c.layers),
        "obs_names": r.obs_names.equals(c.obs_names),
        "var_names": r.var_names.equals(c.var_names),
        "obs": r.obs.astype(str).equals(c.obs.astype(str)) and list(r.obs) == list(c.obs),
        "var": r.var.astype(str).equals(c.var.astype(str)) and list(r.var) == list(c.var),
        "uns keys": sorted(r.uns) == sorted(c.uns),
        "uns.qc_thresholds": pd.DataFrame.equals(r.uns["qc_thresholds"], c.uns["qc_thresholds"]),
    }
    rc, cc = r.layers["counts"], c.layers["counts"]
    checks["counts dtype"] = rc.dtype == cc.dtype
    checks["counts"] = rc.shape == cc.shape and (rc != cc).nnz == 0
    for k, v in checks.items():
        print(f"{'ok ' if v else 'DIFF'} h5ad {k}")
        bad += [] if v else [k]
    if not checks["counts"] and rc.shape == cc.shape:
        print("   differing entries:", (rc != cc).nnz, "max abs diff:", np.abs(rc - cc).max())
    print("SAME" if not bad else f"DIFFERENT: {', '.join(bad)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
