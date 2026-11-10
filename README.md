# omni-thoa-example

A small omnibenchmark plan (`plan.yaml`: DATA → FILT → NORM → FEAT → REPORT,
one module per stage) that runs
locally with `ob run`, or on thoa as cached slices.

## Run on thoa

```sh
export THOA_API_KEY=...
python thoa/slices.py --dry          # per stage: reuse a cached slice, or run one
python thoa/slices.py --fetch out    # run what's missing, then download all results into out/
obcost ls plan.yaml                  # runs with their thoa cost
```

`--dry --fetch out` downloads cached results and runs nothing. Run the
commands from the repo root, with a python that has PyYAML. Commit and push
first: a job runs the pushed `HEAD`, so the driver refuses `+dirty` keys.

### What happens

For each stage in plan order, `thoa/slices.py`:

1. computes the stage's **slice key** (`thoa/slicekey.sh`),
   `{id}@{version}/until-{STAGE}/{hash}`. The hash covers the ob version, ob's
   dry-run Snakefile up to that stage (module commits, parameters, commands)
   and the env files. It gets a `+dirty` suffix when `plan.yaml`, `envs/` or
   `thoa/slice.sh` have uncommitted changes.
2. looks the key up in **`slices.lock`**. If the key is there and its thoa
   dataset still exists, the stage is reused.
3. otherwise it runs **one thoa job**, which uploads nothing. The VM fetches
   this repo at `HEAD`, ob at `OB_SRC`'s `HEAD` (both as GitHub archives, so
   both must be pushed) and obcost from its release. The job's only input is
   the previous slice's output dataset, staged at the paths ob expects. The VM
   runs `ob run --until STAGE` (`thoa/slice.sh`) and keeps what was staged, so
   each output holds every slice so far. That's needed because thoa can't
   give a job files from two datasets. The output and its result files
   (path → file id) are recorded in `slices.lock`, which you commit.
4. imports the run's **costs**. ob's post-run hook runs `obcost` on the VM,
   and the driver adds thoa's billed cost to that record in your local obcost
   store.

Before ob runs, the job waits until every upstream file is staged (thoa can
start a job early) and gives them one timestamp, so snakemake doesn't rerun
upstream rules. Staging paths come from the upstream job's record, not from
its output dataset, whose listed paths drift (thoa-go BUGS.txt 12). When a
job fails, the driver prints the tail of the VM's `out/boot.log` and
`out/job.log`.

### Job size

A stage's job gets the most any of its modules asks for through ob's
`resources` in the plan (`cores`, `mem_mb`, `disk_mb`; a module's block
replaces its stage's), rounded up to whole GB and never below 4 cores, 16 GB
RAM and 40 GB disk (`FLOOR` in `thoa/slices.py`; disk also holds staged
inputs and in-job conda envs). `THOA_RES=8,32,60` sets cores, RAM GB and disk
GB for every job of a run. The plan's resources go into ob's Snakefile, so
changing them changes the slice keys; `THOA_RES` doesn't.

### Requirements

| What | Default | Override |
|---|---|---|
| thoa-go binary (`thoa`) | `~/lab/thoa-go/thoa` | `THOA` |
| ob source tree, pushed to `OB_REPO` (the VM installs its `HEAD`; `.work/ob` gets the same tree, for keys) | `~/lab/omnibenchmark-integ`, branch `integ/identity-hooks` (main + #407 + hooks) | `OB_SRC` |
| GitHub repo the VM fetches ob from | `btraven00/omnibenchmark` | `OB_REPO` |
| python ≥ 3.12 to install that ob locally | `~/lab/omnibenchmark/.pixi/envs/default/bin/python` | `OB_PY` |
| obcost, for importing costs locally: `go install github.com/btraven00/obcost@v0.1.0` | `obcost` on `PATH` | `OBCOST` |

The driver checks these and the thoa login before doing anything else.

### Rules for the plan

- Modules are pinned by `repository.url` and `commit` to a **pushed** commit;
  ob clones them on the VM.
- A stage's modules share one software environment, because each stage is one
  job. Stages run in plan order, each on top of the previous one: a stage that
  needs two slices' outputs (a fan-in) can't run until thoa can stage two datasets.
- `envs/thoa/*.yml` is generated from the plan's envs plus ob's dependencies
  (`thoa/envs.py`, run by the driver). Don't edit those files.
- An env that thoa's builder can't build is listed in `IN_JOB` in
  `thoa/slices.py`, and snakemake builds it inside the job instead. `scrapper`
  is the one here: its R channels don't survive thoa's builder (thoa-go
  BUGS.txt 6, 9).

### Files

| File | Role |
|---|---|
| `thoa/slices.py` | driver (local) |
| `thoa/slicekey.sh` | slice key |
| `thoa/slice.sh` | runs one slice on the VM |
| `thoa/envs.py` | writes `envs/thoa/*.yml` in thoa's normal form, so existing envs are reused |
| `thoa/hooks/post-run` | ob hook on the VM: obcost records the run |
| `slices.lock` | slice key → thoa datasets (`results`, `files`, `runs` with job, logs and cost) |

## Run locally

```sh
ob run plan.yaml --out-dir out
```
