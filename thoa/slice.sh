#!/usr/bin/env bash
# Runs on a thoa VM, in this repo at the slice's commit (thoa/slices.py's job
# command fetches it): one slice of the plan.
#   OB_URL=... OB_VERSION=... OBCOST_URL=... bash thoa/slice.sh [ob run flags, e.g. --until DATA]
# The job's thoa env is the slice's module env plus ob's deps (envs/thoa/,
# from thoa/envs.py), so ob runs with software_backend host: nothing nested.
# out/ starts with the upstream slice's output (staged by thoa) and the job
# collects all of it, so each slice's output dataset holds every slice so far:
# the next job needs that one dataset only.
set -euo pipefail

mkdir -p out thoa/bin
exec > >(tee -a out/job.log) 2>&1
echo "== staged inputs: $(find out -type f | wc -l) files"
echo "== $(date -Is) slice: $* (cwd $PWD)"
echo "== python: $(command -v python) ($(python -V 2>&1))"

# ob from a source archive of a pushed commit; its deps are in the env.
SETUPTOOLS_SCM_PRETEND_VERSION=$OB_VERSION pip install -q --no-deps "$OB_URL"
echo "== ob: $(ob --version 2>&1)"

# obcost records the run's costs as ob's post-run hook (thoa/hooks/post-run).
python -c 'import sys, urllib.request as u; u.urlretrieve(sys.argv[1], "thoa/bin/obcost")' "$OBCOST_URL"
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
echo "== $(date -Is) done, exit $rc"
exit $rc
