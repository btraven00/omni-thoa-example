#!/usr/bin/env bash
# Runs on a thoa VM, in the uploaded copy of this repo: one slice of the plan.
#   bash thoa/slice.sh [ob run flags, e.g. --until DATA]
# The job's thoa env is the slice's module env plus ob's deps (envs/thoa/,
# from thoa/envs.py), so ob runs with software_backend host: nothing nested.
# The job collects out/ into its output dataset, paths kept. The data is
# out/<STAGE>.*/**; logs are out/.logs, out/job.log, out/Snakefile, and a
# slice key pins only the data files' ids (slices.lock), so logs never feed
# a downstream job.
set -euo pipefail
# omnibenchmark main + run hooks (design 016, btraven00 fork feat/hooks)
OB=06803e1884e34832a15f7a86a3adf103339221f5

mkdir -p out
# Files already in out/ were staged from upstream slices; drop them at the end
# so this job's output dataset holds only what this slice made.
staged=$(mktemp); find out -type f > "$staged"
exec > >(tee -a out/job.log) 2>&1
echo "== staged inputs: $(wc -l < "$staged") files"
echo "== $(date -Is) slice: $* (cwd $PWD)"
echo "== python: $(command -v python) ($(python -V 2>&1))"

# ob versions itself from git; a source archive has none. Its deps are in the env.
SETUPTOOLS_SCM_PRETEND_VERSION=0.7.0.post0 pip install -q --no-deps \
  "omnibenchmark @ https://github.com/btraven00/omnibenchmark/archive/$OB.tar.gz"

# obcost records the run's costs as ob's post-run hook (thoa/hooks/post-run);
# uploads lose the executable bit.
chmod +x thoa/hooks/post-run thoa/bin/obcost
export OB_HOOKS_PATH=$PWD/thoa/hooks

# SLICE_BACKEND=host (default): the job's thoa env is the module env, run
# modules in it. SLICE_BACKEND=conda: for module envs thoa's builder can't
# build (bioconda-first strict priority, URL channels dropped; thoa-go
# BUGS.txt 6, 9), keep the plan's conda backend and let snakemake build them
# here with the VM's base conda, in the env file's channel order.
# The slice key hashes the committed plan, not this copy.
if [ "${SLICE_BACKEND:-host}" = conda ]; then
  PATH=$PATH:/opt/conda/bin
  export CONDA_CHANNEL_PRIORITY=flexible
  echo "== conda backend: $(command -v conda) $(conda --version 2>&1)"
else
  sed -i 's/^software_backend: .*/software_backend: "host"/' plan.yaml
fi

rc=0
ob run plan.yaml --cores "$(nproc)" --out-dir out "$@" || rc=$?
# Built envs and module clones are GBs and not results; keep them out of the dataset.
rm -rf out/.snakemake out/.modules out/.envs
xargs -r -d '\n' rm -f < "$staged"
echo "== $(date -Is) done, exit $rc"
exit $rc
