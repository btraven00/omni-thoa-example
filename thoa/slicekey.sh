#!/usr/bin/env bash
# Print the slice key of the plan up to and including STAGE:
#   {plan.id}@{plan.version}/until-{STAGE}/{h}
# h hashes what the slice would compute: ob's dry-run Snakefile (module
# commits, parameters, commands; minus its "Generated:" timestamp) and the
# env files it references. This repo's own `url: .` modules are keyed by
# content, not commit: its HEAD sha is masked and the committed tree's blob
# ids (minus the lockfiles) hashed instead, so committing slices.lock doesn't
# invalidate the cache. A dirty tree adds `git diff HEAD` and marks "+dirty".
#   thoa/slicekey.sh DATA
set -euo pipefail
STAGE=$1
OB=${OB:-$HOME/lab/omnibenchmark/.venv/bin/ob}
d=$(mktemp -d)
trap 'rm -rf "$d"' EXIT

"$OB" run benchmark.yaml --dirty --dry --until "$STAGE" --out-dir "$d/out" >/dev/null
id=$(sed -n 's/^id: *//p' benchmark.yaml | tr -d '"')
ver=$(sed -n 's/^version: *//p' benchmark.yaml | tr -d '"')
head=$(git rev-parse HEAD)
h=$({
  grep -v '^# Generated:' "$d/out/Snakefile" | sed "s/$head/LOCAL/g; s/${head:0:7}/LOCAL/g"
  git ls-tree -r HEAD | grep -vP '\t(slices|hapiq)\.lock$'
  find "$d/out/.envs" -type f 2>/dev/null | sort | xargs -r cat
  git diff HEAD -- . ':!data' ':!hapiq.lock' ':!slices.lock'
} | sha256sum | cut -c1-16)
# slices.lock is this cache itself; hapiq.lock is only read with HAPIQ_LOCK set,
# which slice jobs don't do.
dirty=$(git status --porcelain -- . ':!data' ':!.work' ':!out*' ':!slices.lock' ':!hapiq.lock' | grep -q . && echo +dirty || true)
echo "$id@$ver/until-$STAGE/$h$dirty"
