#!/usr/bin/env bash
# Print the slice key of the plan up to and including STAGE:
#   {plan.id}@{plan.version}/until-{STAGE}/{h}
# h hashes what the slice would compute: ob's version, its dry-run Snakefile (module
# commits, parameters, commands; minus its "Generated:" timestamp) and the
# env files it references. Modules are pinned by repo + commit in the plan,
# so the Snakefile carries their identity, and only the plan's stages up to
# STAGE. Also hashed: the blob ids of the repo files the slice's job runs from
# (VM script, hook, hapiq.lock, the job envs of the slice's envs), so edits
# elsewhere (docs, slices.lock, later stages) keep the cache. Uncommitted
# changes to INPUTS add their diff and mark the key "+dirty" (thoa/slices.py
# won't run those: the VM fetches HEAD).
#   thoa/slicekey.sh DATA
set -euo pipefail
STAGE=$1
OB=${OB:-.work/ob/bin/ob}  # thoa/slices.py installs the ob the VM runs there
d=$(mktemp -d)
trap 'rm -rf "$d"' EXIT

"$OB" run plan.yaml --dry --until "$STAGE" --out-dir "$d/out" >/dev/null
id=$(sed -n 's/^id: *//p' plan.yaml | tr -d '"')
ver=$(sed -n 's/^version: *//p' plan.yaml | tr -d '"')
INPUTS=(envs plan.yaml thoa/slice.sh thoa/hooks hapiq.lock)
JOB=(thoa/slice.sh thoa/hooks hapiq.lock envs/thoa/ob.yml)
for f in "$d"/out/.envs/*.yaml; do [ -e "$f" ] && JOB+=("envs/thoa/$(basename "$f" .yaml).yml"); done
h=$({
  "$OB" --version
  grep -v '^# Generated:' "$d/out/Snakefile"
  git ls-tree -r HEAD -- "${JOB[@]}"
  find "$d/out/.envs" -type f 2>/dev/null | sort | xargs -r cat
  git diff HEAD -- "${INPUTS[@]}"
} | sha256sum | cut -c1-16)
dirty=$(git status --porcelain -- "${INPUTS[@]}" | grep -q . && echo +dirty || true)
echo "$id@$ver/until-$STAGE/$h$dirty"
