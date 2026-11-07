#!/usr/bin/env bash
# Print the slice key of the plan up to and including STAGE:
#   {plan.id}@{plan.version}/until-{STAGE}/{h}
# h hashes what the slice would compute: ob's dry-run Snakefile (module
# commits, parameters, commands; minus its "Generated:" timestamp) and the
# env files it references. This repo's own `url: .` modules are keyed by
# content, not commit: its HEAD sha is masked and the blob ids of what a
# slice job executes (INPUTS) are hashed instead, so commits elsewhere (docs,
# slices.lock) don't invalidate the cache. Uncommitted changes to INPUTS add
# their diff and mark the key "+dirty".
#   thoa/slicekey.sh DATA
set -euo pipefail
STAGE=$1
OB=${OB:-$HOME/lab/omnibenchmark/.venv/bin/ob}
d=$(mktemp -d)
trap 'rm -rf "$d"' EXIT

"$OB" run benchmark.yaml --dirty --dry --until "$STAGE" --out-dir "$d/out" >/dev/null
id=$(sed -n 's/^id: *//p' benchmark.yaml | tr -d '"')
ver=$(sed -n 's/^version: *//p' benchmark.yaml | tr -d '"')
INPUTS=(modules envs omnibenchmark.yaml benchmark.yaml thoa/slice.sh)
head=$(git rev-parse HEAD)
h=$({
  grep -v '^# Generated:' "$d/out/Snakefile" | sed "s/$head/LOCAL/g; s/${head:0:7}/LOCAL/g"
  git ls-tree -r HEAD -- "${INPUTS[@]}"
  find "$d/out/.envs" -type f 2>/dev/null | sort | xargs -r cat
  git diff HEAD -- "${INPUTS[@]}"
} | sha256sum | cut -c1-16)
dirty=$(git status --porcelain -- "${INPUTS[@]}" | grep -q . && echo +dirty || true)
echo "$id@$ver/until-$STAGE/$h$dirty"
