"""Run the plan on thoa as slices, one job per stage, reusing what slices.lock has.

    python thoa/slices.py [--dry]      (a python with PyYAML; ob's venv has it)

For each stage in plan order: compute its slice key (thoa/slicekey.sh). If
slices.lock has the key and its results dataset still exists, reuse it.
Otherwise run the slice as a thoa job: upload the repo's files (modules are
cloned by ob from their pinned commits, so lineage records real commits),
stage every upstream slice's result files by id,
run thoa/slice.sh --until STAGE, split the job's output into a results
dataset and a logs dataset, and add the key to slices.lock.

ponytail: one job per stage and stages in plan order, which fits a linear
plan; a stage with modules on different envs needs one job per module (-m).
"""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import yaml

THOA = os.environ.get("THOA", str(Path.home() / "lab/thoa-go/thoa"))
LOCK = Path("slices.lock")
WORK = Path(".work/slices")
# Envs thoa's builder can't build (bioconda-first strict priority, URL
# channels dropped; thoa-go BUGS.txt 6, 9): build them in the job instead.
IN_JOB = {"scrapper"}
LOG_GLOBS = ["out/.logs/*", "out/job.log", "out/Snakefile", "out/.metadata/*", "*/performance.txt", "out/.hooks/*"]
OBCOST_SRC = Path(os.environ.get("OBCOST_SRC", Path.home() / "phd/obcost"))
OBCOST = Path("thoa/bin/obcost")  # shipped to the VM with thoa/, run by thoa/hooks/post-run
HEADER = """\
# slices.lock: slice key -> the thoa datasets that materialize it.
# Written by thoa/slices.py. Key: {plan.id}@{plan.version}/until-{STAGE}/{hash}
# (thoa/slicekey.sh). results: dataset over the slice's own output files (no
#   logs); thoa keeps no paths for it, so `files` (path -> file id) is the
#   record downstream jobs stage from. Ids are content addresses (md5 dedup).
# upstream: slices a job for this one stages. runs: one per job that made it.
"""


def sh(*cmd, stderr=subprocess.PIPE):
    return subprocess.run(cmd, check=True, text=True, stdout=subprocess.PIPE, stderr=stderr).stdout


def bundle():
    """The repo's files a slice job runs from (modules come from their pinned commits)."""
    files = sh("git", "ls-files", "--cached", "--others", "--exclude-standard").split("\n")
    skip = ("data/", "out", "slices.lock")  # slices.lock: the cache, not an input
    keep = sorted({f.split("/")[0] for f in files if f and not f.startswith(skip)})
    return keep


def exists(dataset):
    return subprocess.run([THOA, "ls", "-q", dataset], capture_output=True).returncode == 0


def view(output, args):
    """thoa view: new dataset id, {path: file id}."""
    lines = sh(THOA, "view", "-q", *args, output).strip().split("\n")
    return lines[0], dict(l.split("\t") for l in lines[1:])


def run_slice(stage, env, staged):
    in_job = env in IN_JOB
    WORK.mkdir(parents=True, exist_ok=True)
    tsv = WORK / f"{stage}-stage.tsv"
    tsv.write_text("".join(f"{p}\t{i}\n" for p, i in staged.items()))
    cmd = f"bash thoa/slice.sh --until {stage} -- --rerun-triggers mtime"
    args = [THOA, "run", "-env", f"envs/thoa/{'ob' if in_job else env}.yml",
            "-cmd", ("SLICE_BACKEND=conda " if in_job else "") + cmd,
            "-out", "out", "-c", "4", "-ram", "16", "-disk", "40"]
    if staged:
        args += ["-stage", str(tsv)]
    for f in bundle():
        args += ["-in", f]
    print(f"{stage}: running a thoa job ({'in-job env' if in_job else 'env ' + env}, {len(staged)} staged files)")
    for attempt in range(3):
        with open(WORK / f"{stage}.err", "w") as err:
            p = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=err)
        log = (WORK / f"{stage}.err").read_text()
        m = re.search(r"job (\S+) submitted \(env (\S+)\)", log)
        # The upload before submission hits transient 500s and timeouts; with
        # no job created yet, a retry is safe (files are deduped by md5).
        if p.returncode == 0 or m:
            break
        print(f"{stage}: failed before submitting ({log.strip().splitlines()[-1]}), retrying")
        time.sleep(30)
    if p.returncode or not m:
        sys.exit(f"{stage}: job failed, see {WORK / (stage + '.err')}")
    output = p.stdout.strip()
    results, files = view(output, [a for g in LOG_GLOBS for a in ("-e", g)])
    logs, _ = view(output, [a for g in LOG_GLOBS for a in ("-i", g)])
    run = {"job": m[1], "output": output, "logs": logs, "env": m[2],
           "backend": "conda (module env built in-job)" if in_job else "host",
           "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    try:  # costs are a side record: never lose the slice over them
        run.update(record_cost(stage, m[1], output))
    except subprocess.CalledProcessError as e:
        print(f"{stage}: obcost import failed: {(e.stderr or '').strip()}")
    return results, files, run


def record_cost(stage, job, output):
    """Import the VM's obcost rows (out/.hooks/) with thoa's cost for the job."""
    rec = json.loads(sh(THOA, "job", job))
    cost = {k: rec[k] for k in ("billed_cost_usd", "credits_debited") if rec.get(k) is not None}
    dest = WORK / f"{stage}-hooks"
    sh(THOA, "download", "-q", "-i", "out/.hooks/*", output, str(dest))
    attrs = ["-a", "remote=thoa", "-a", f"job={job}"]
    for k, v in cost.items():
        attrs += ["-a", f"{k}={v}"]
    sh(str(OBCOST), "import", *attrs, str(dest / "out" / ".hooks"))
    print(f"{stage}: obcost import, cost {cost or 'not yet billed'}")
    return cost


def main(dry=False):
    if not dry:  # a static linux binary; jobs run it as ob's post-run hook
        sh("go", "build", "-C", str(OBCOST_SRC), "-o", str(OBCOST.resolve()), ".")
    plan = yaml.safe_load(Path("plan.yaml").read_text())
    lock = yaml.safe_load(LOCK.read_text()) if LOCK.exists() else {}
    lock = lock or {}
    done = []  # keys of the slices so far, in plan order
    for st in plan["stages"]:
        stage = st["id"]
        envs = {m["software_environment"] for m in st["modules"]}
        if len(envs) != 1:
            sys.exit(f"{stage}: modules on {len(envs)} envs; one job per stage needs one")
        key = sh("thoa/slicekey.sh", stage, stderr=subprocess.DEVNULL).strip()
        if key in lock and exists(lock[key]["results"]):
            print(f"{stage}: reuse {key} (results {lock[key]['results']})")
        elif dry:
            print(f"{stage}: would run {key}")
        else:
            staged = {p: i for k in done for p, i in lock[k]["files"].items()}
            results, files, run = run_slice(stage, envs.pop(), staged)
            entry = lock.setdefault(key, {"upstream": list(done)})
            entry.update(results=results, files=files)
            entry.setdefault("runs", []).append(run)
            LOCK.write_text(HEADER + yaml.safe_dump(lock, sort_keys=False))
            print(f"{stage}: {key} -> results {results}, logs {run['logs']}")
        done.append(key)


if __name__ == "__main__":
    main(dry="--dry" in sys.argv[1:])
