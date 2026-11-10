"""Run the plan on thoa as slices, one job per stage, reusing what slices.lock has.

    python thoa/slices.py [--dry] [--fetch DIR]      (see README.md)

For each stage in plan order: compute its slice key (thoa/slicekey.sh). If
slices.lock has the key and its output dataset still exists, reuse it.
Otherwise run the slice as a thoa job that uploads nothing: the VM fetches
this repo at HEAD (pushed), ob at OB_SRC's HEAD (pushed) and obcost from its
release, ob clones the modules from their pinned commits, and the job's one
input dataset is the previous slice's output. That output already holds every
slice before it (thoa/slice.sh keeps what was staged), so one dataset is
enough: thoa can't combine datasets for a job (thoa-go BUGS.txt 10, 11).

ponytail: one job per stage and stages in plan order, which fits a linear
plan; a stage with modules on different envs needs one job per module (-m),
and a fan-in of two slices needs thoa to stage two datasets.
"""

import argparse
import fnmatch
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

import yaml

import envs as thoa_envs

THOA = os.environ.get("THOA", str(Path.home() / "lab/thoa-go/thoa"))
LOCK = Path("slices.lock")
WORK = Path(".work/slices")
# Envs thoa's builder can't build (bioconda-first strict priority, URL
# channels dropped; thoa-go BUGS.txt 6, 9): build them in the job instead.
IN_JOB = {"scrapper"}
LOG_GLOBS = ["out/.logs/*", "out/job.log", "out/boot.log", "out/Snakefile", "out/.metadata/*", "*/performance.txt", "out/.hooks/*"]
# ob, from a pushed commit of OB_SRC (main + #407 + hooks): the VM installs
# the GitHub archive of that commit, and .work/ob the same tree, for slice keys.
OB_SRC = Path(os.environ.get("OB_SRC", Path.home() / "lab/omnibenchmark-integ"))
OB_REPO = os.environ.get("OB_REPO", "btraven00/omnibenchmark")
PY = os.environ.get("OB_PY", str(Path.home() / "lab/omnibenchmark/.pixi/envs/default/bin/python"))
VENV = Path(".work/ob")
OBCOST = os.environ.get("OBCOST", "obcost")  # local, for `obcost import`
OBCOST_URL = "https://github.com/btraven00/obcost/releases/download/v0.1.0/obcost-linux-amd64"
HEADER = """\
# slices.lock: slice key -> the thoa datasets that materialize it.
# Written by thoa/slices.py. Key: {plan.id}@{plan.version}/until-{STAGE}/{hash}
# (thoa/slicekey.sh). results: the job's output dataset, which holds this
#   slice and every one before it; files: its result files (path -> file id,
#   logs excluded). The next slice's job stages that dataset.
# upstream: the slices before this one. runs: one per job that made it.
"""


def sh(*cmd, stderr=subprocess.PIPE, env=None):
    return subprocess.run(cmd, check=True, text=True, stdout=subprocess.PIPE, stderr=stderr, env=env).stdout


def preflight(dry):
    if not Path("plan.yaml").exists():
        sys.exit("no plan.yaml here: run from the repo root")
    need = {THOA: "thoa-go binary (THOA)", OB_SRC: "ob source tree (OB_SRC)", PY: "python >= 3.12 (OB_PY)"}
    missing = [f"  {what}: {p}" for p, what in need.items() if not Path(p).exists()]
    if not dry and not shutil.which(OBCOST):
        missing.append(f"  obcost (OBCOST): go install github.com/btraven00/obcost@v0.1.0")
    if missing:
        sys.exit("missing:\n" + "\n".join(missing))
    p = subprocess.run([THOA, "ls"], capture_output=True, text=True)
    if p.returncode:  # e.g. not authenticated: set THOA_API_KEY
        sys.exit(p.stderr.strip())


def pushed(repo):
    """HEAD of git repo, which must be on a remote branch: the VM fetches it by commit."""
    sha = sh("git", "-C", str(repo), "rev-parse", "HEAD").strip()
    if not sh("git", "-C", str(repo), "branch", "-r", "--contains", sha).strip():
        sys.exit(f"{repo}: HEAD {sha[:8]} is not pushed; the VM fetches it from GitHub")
    return sha


def github(repo):
    """owner/name of repo's origin on GitHub."""
    url = sh("git", "-C", str(repo), "remote", "get-url", "origin").strip()
    return re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?$", url)[1]


def build():
    """ob from OB_SRC into .work/ob, versioned as the VM's will be."""
    rev = sh("git", "-C", str(OB_SRC), "rev-parse", "--short=8", "HEAD").strip()
    version = f"0.8.0.dev0+integ.{rev}"
    wheels = Path(".work/wheels")
    shutil.rmtree(wheels, ignore_errors=True)
    # ponytail: uncommitted changes in OB_SRC don't change the version; the VM wouldn't get them either
    sh(PY, "-m", "pip", "wheel", "-q", "--no-deps", "-w", str(wheels), str(OB_SRC),
       env={**os.environ, "SETUPTOOLS_SCM_PRETEND_VERSION": version})
    whl = str(next(wheels.glob("omnibenchmark-*.whl")))
    if not (VENV / "bin/ob").exists():
        print(f"installing ob into {VENV} (once)")
        sh(PY, "-m", "venv", str(VENV))
        sh(str(VENV / "bin/pip"), "install", "-q", whl)
    else:  # the VM installs it --no-deps too, on the env's deps (thoa/envs.py)
        sh(str(VENV / "bin/pip"), "install", "-q", "--force-reinstall", "--no-deps", whl)
    return version


# Unpacks a GitHub source archive into the cwd: the job's whole checkout.
FETCH = ('python -c \'import io, os, shutil, sys, tarfile, urllib.request as u; '
         't = tarfile.open(fileobj=io.BytesIO(u.urlopen(sys.argv[1]).read())); t.extractall(".src", filter="data"); '
         'shutil.copytree(os.path.join(".src", os.listdir(".src")[0]), ".", dirs_exist_ok=True); shutil.rmtree(".src")\'')


def exists(dataset):
    return subprocess.run([THOA, "ls", "-q", dataset], capture_output=True).returncode == 0


def results(job):
    """{path under the cwd: file id} of a job's outputs, logs excluded. From the
    job record's absolute output_context: the output dataset's own listed
    paths drift (out/ was dropped some hours after the job, 2026-11-10)."""
    rec = json.loads(sh(THOA, "job", job))
    cwd = rec["current_working_directory"].rstrip("/") + "/"
    rel = {p[len(cwd):]: i for p, i in rec["output_context"].items() if p.startswith(cwd)}
    return {p: i for p, i in rel.items() if not any(fnmatch.fnmatch(p, g) for g in LOG_GLOBS)}


def run_slice(stage, env, upstream, src):
    """upstream: (output dataset, job) of the previous slice, or None."""
    in_job = env in IN_JOB
    WORK.mkdir(parents=True, exist_ok=True)
    vars = {**src, **({"SLICE_BACKEND": "conda"} if in_job else {})}
    # thoa can start a job before its inputs are all staged (2 of 8, job
    # bd0ee8aa): wait for every upstream file, up to 30 min, then fail loudly.
    staged = results(upstream[1]) if upstream else {}
    want = len(staged)
    have = "$(find out -type f ! -name boot.log | wc -l)"
    # Staged files land in any order (the big ones last), so snakemake's mtime
    # trigger would rerun upstream rules: give them all one timestamp.
    wait = (f"for i in $(seq 360); do [ {have} -ge {want} ] && break; sleep 5; done; "
            f"echo staged {have}/{want}; [ {have} -ge {want} ] && "
            f"find out -type f ! -name boot.log -exec touch -d @1000000000 {{}} +")
    # the bootstrap logs into out/, so a job that fails before slice.sh leaves a trace
    cmd = (f"mkdir -p out && {{ set -x; {wait} && {FETCH} {shlex.quote(src['PLAN_URL'])}; }} > out/boot.log 2>&1 && "
           + " ".join(f"{k}={shlex.quote(v)}" for k, v in vars.items() if k != "PLAN_URL")
           + f" bash thoa/slice.sh --until {stage} -- --rerun-triggers mtime")
    args = [THOA, "run", "-env", f"envs/thoa/{'ob' if in_job else env}.yml",
            "-cmd", cmd, "-out", "out", "-c", "4", "-ram", "16", "-disk", "40"]
    if upstream:
        tsv = WORK / f"{stage}-stage.tsv"
        tsv.write_text("".join(f"{p}\t{i}\n" for p, i in sorted(staged.items())))
        args += ["-d", upstream[0], "-stage", str(tsv)]
    print(f"{stage}: running a thoa job ({'in-job env' if in_job else 'env ' + env}, "
          f"{want} files staged from {upstream[0] if upstream else 'nothing'})")
    for attempt in range(3):
        with open(WORK / f"{stage}.err", "w") as err:
            p = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=err)
        log = (WORK / f"{stage}.err").read_text()
        m = re.search(r"job (\S+) submitted \(env (\S+)\)", log)
        # transient 500s and timeouts before submission; no job yet, so a retry is safe
        if p.returncode == 0 or m:
            break
        print(f"{stage}: failed before submitting ({log.strip().splitlines()[-1]}), retrying")
        time.sleep(30)
    output = p.stdout.strip()  # thoa run prints it on failure too, if the job wrote any
    if p.returncode or not m:
        fail(stage, m and m[1], output)
    run = {"job": m[1], "output": output, "env": m[2], "plan_commit": src["PLAN_URL"].rsplit("/", 1)[1][:-7],
           "ob": src["OB_VERSION"], "backend": "conda (module env built in-job)" if in_job else "host",
           "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    try:  # costs are a side record: never lose the slice over them
        run.update(record_cost(stage, m[1], output))
    except subprocess.CalledProcessError as e:
        print(f"{stage}: obcost import failed: {(e.stderr or '').strip()}")
    return output, results(m[1]), run


def fail(stage, job, output):
    msg = f"{stage}: job failed; client log {WORK / (stage + '.err')}"
    if not output:
        sys.exit(msg + (f"; no outputs, see `thoa job {job}`" if job else ""))
    dest = WORK / f"{stage}-failed"
    subprocess.run([THOA, "download", "-q", "-i", "out/job.log", "-i", "out/boot.log", output, str(dest)])
    for name in ("boot.log", "job.log"):
        if (log := dest / "out" / name).exists():
            print(f"== {log}\n" + "".join(log.read_text().splitlines(True)[-30:]), file=sys.stderr)
            msg += f"; VM log {log}"
    sys.exit(msg)


def fetch(lock, key, dest):
    """The slice's output (it holds all slices before it), at its out/ paths under dest."""
    output = lock[key]["results"]
    got = WORK / "fetch" / output
    if not got.exists():  # downloaded once: each download spends the dataset's quota
        part = got.with_suffix(".part")
        sh(THOA, "download", "-q", *[a for g in LOG_GLOBS + [g[4:] for g in LOG_GLOBS] for a in ("-e", g)],
           output, str(part))
        part.rename(got)
    # listed paths drift between out/... and ... (see results)
    shutil.copytree(got / "out" if (got / "out").is_dir() else got, dest, dirs_exist_ok=True)
    print(f"fetched {key} -> {dest}")


def record_cost(stage, job, output):
    """Import the VM's obcost rows (out/.hooks/) with thoa's cost for the job."""
    # billing trails "completed", and obcost records a run once: wait up to 3 min
    for _ in range(18):
        rec = json.loads(sh(THOA, "job", job))
        cost = {k: rec[k] for k in ("billed_cost_usd", "credits_debited") if rec.get(k) is not None}
        if len(cost) == 2:
            break
        time.sleep(10)
    dest = WORK / f"{stage}-hooks"
    sh(THOA, "download", "-q", "-i", "out/.hooks/*", "-i", ".hooks/*", output, str(dest))
    attrs = ["-a", "remote=thoa", "-a", f"job={job}"]
    for k, v in cost.items():
        attrs += ["-a", f"{k}={v}"]
    hooks = dest / "out" / ".hooks"
    sh(OBCOST, "import", *attrs, str(hooks if hooks.is_dir() else dest / ".hooks"))
    print(f"{stage}: obcost import, cost {cost or 'not yet billed'}")
    return cost


def main(dry=False, fetch_to=None):
    preflight(dry)
    version = build()
    thoa_envs.main()  # envs/thoa/*.yml from the plan's envs; the key hashes them
    keyenv = {**os.environ, "OB": str(VENV / "bin/ob")}
    plan = yaml.safe_load(Path("plan.yaml").read_text())
    lock = yaml.safe_load(LOCK.read_text()) if LOCK.exists() else {}
    lock = lock or {}
    src = None  # what the VM fetches; checked before the first job
    done = []  # keys of the slices so far, in plan order
    for st in plan["stages"]:
        stage = st["id"]
        envs = {m["software_environment"] for m in st["modules"]}
        if len(envs) != 1:
            sys.exit(f"{stage}: modules on {len(envs)} envs; one job per stage needs one")
        try:
            key = sh("thoa/slicekey.sh", stage, env=keyenv).strip()
        except subprocess.CalledProcessError as e:
            sys.exit(f"{stage}: slice key failed:\n{e.stderr}")
        if key in lock and exists(lock[key]["results"]):
            print(f"{stage}: reuse {key} (results {lock[key]['results']})")
        elif dry:
            print(f"{stage}: would run {key}")
        else:
            if key.endswith("+dirty"):
                sys.exit(f"{stage}: {key}: commit and push first; the VM runs HEAD")
            if src is None:
                ob = pushed(OB_SRC)
                src = {"PLAN_URL": f"https://github.com/{github('.')}/archive/{pushed('.')}.tar.gz",
                       "OB_URL": f"https://github.com/{OB_REPO}/archive/{ob}.tar.gz",
                       "OB_VERSION": version, "OBCOST_URL": OBCOST_URL}
            upstream = (lock[done[-1]]["results"], lock[done[-1]]["runs"][-1]["job"]) if done else None
            results, result_files, run = run_slice(stage, envs.pop(), upstream, src)
            entry = lock.setdefault(key, {"upstream": list(done)})
            entry.update(results=results, files=result_files)
            entry.setdefault("runs", []).append(run)
            LOCK.write_text(HEADER + yaml.safe_dump(lock, sort_keys=False))
            print(f"{stage}: {key} -> {results}")
        done.append(key)
    if fetch_to:
        if done[-1] not in lock:
            sys.exit(f"--fetch: not run yet: {done[-1]}")
        fetch(lock, done[-1], Path(fetch_to))


if __name__ == "__main__":
    a = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    a.add_argument("--dry", action="store_true", help="print what would run or be reused; run nothing")
    a.add_argument("--fetch", metavar="DIR", help="then download the results of all slices into DIR")
    args = a.parse_args()
    main(args.dry, args.fetch)
