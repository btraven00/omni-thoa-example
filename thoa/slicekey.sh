#!/usr/bin/env bash
# Print the slice key of the plan up to and including STAGE:
#   {plan.id}@{plan.version}/until-{STAGE}/{h}
# h hashes what the slice would compute: ob's dry-run Snakefile (module
# commits, parameters, commands; minus its "Generated:" timestamp) and the
# env files it references. Modules are pinned by repo + commit in the plan,
# so the Snakefile carries their identity. INPUTS are the repo files a slice
# job runs from (plan, envs, VM script): their blob ids are hashed, so commits
# elsewhere (docs, slices.lock) don't invalidate the cache, and uncommitted
# changes to them add their diff and mark the key "+dirty".
#   thoa/slicekey.sh DATA
set -euo pipefail
STAGE=$1
OB=${OB:-$HOME/lab/omnibenchmark/.venv/bin/ob}
d=$(mktemp -d)
trap 'rm -rf "$d"' EXIT

"$OB" run benchmark.yaml --dry --until "$STAGE" --out-dir "$d/out" >/dev/null
id=$(sed -n 's/^id: *//p' benchmark.yaml | tr -d '"')
ver=$(sed -n 's/^version: *//p' benchmark.yaml | tr -d '"')
INPUTS=(envs benchmark.yaml thoa/slice.sh)
h=$({
  grep -v '^# Generated:' "$d/out/Snakefile"
  git ls-tree -r HEAD -- "${INPUTS[@]}"
  find "$d/out/.envs" -type f 2>/dev/null | sort | xargs -r cat
  git diff HEAD -- "${INPUTS[@]}"
} | sha256sum | cut -c1-16)
dirty=$(git status --porcelain -- "${INPUTS[@]}" | grep -q . && echo +dirty || true)
echo "$id@$ver/until-$STAGE/$h$dirty"
