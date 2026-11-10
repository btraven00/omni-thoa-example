"""Write envs/thoa/<env>.yml: each software environment of the plan plus ob's
dependencies, so a thoa job can run ob with software_backend host inside it.

    python thoa/envs.py [plan.yaml]      (thoa/slices.py runs it)

ob itself is installed at job start (thoa/slice.sh, pip --no-deps), because its
version comes from git and a source archive has none.

The files are written in thoa's own normal form (name "env", bioconda added,
channels and dependencies sorted, pip block last), which is what thoa stores
as env_string. So `thoa run -env FILE` finds an existing environment by exact
text and reuses it instead of building a new one. Generated; don't edit, and
don't add comments (they'd break the match).
"""

import sys
from pathlib import Path

import yaml

# omnibenchmark main 2d601f30, pyproject [project].dependencies
OB_DEPS = [
    "click>=8.1.7", "python-dateutil>=2.9.0.post0", "filelock>=3.4.0",
    "spdx-license-list>=3.19", "PyYAML", "humanfriendly", "pydantic",
    "python-dotenv>=1.0.0", "dulwich>=0.22.0", "pathspec>=0.12.0",
    "rich>=13.0.0", "snakemake>=9.0", "copier>=9.0.0", "pydot>=3.0.2",
    "tqdm>=4.66.4",
]


def normal_form(env: dict) -> dict:
    """thoa's normalization of an environment (checked against stored envs)."""
    deps = env.get("dependencies") or []
    strs = sorted(d for d in deps if isinstance(d, str))
    pips = [{"pip": sorted(d["pip"])} for d in deps if isinstance(d, dict)]
    return {"name": "env",
            "channels": sorted(set(env.get("channels") or []) | {"bioconda"}),
            "dependencies": strs + pips}


def thoa_env(env: dict) -> dict:
    deps = list(env.get("dependencies") or [])
    names = {d.split("=")[0].split(" ")[0].split(">")[0] for d in deps if isinstance(d, str)}
    # ob runs on python 3.12-3.13; an env that pins its own python keeps it
    if "python" not in names:
        deps.insert(0, "python=3.12")
    if "pip" not in names:
        deps.append("pip")
    pip = next((d for d in deps if isinstance(d, dict) and "pip" in d), None)
    if pip is None:
        pip = {"pip": []}
        deps.append(pip)
    pip["pip"] = list(pip["pip"]) + OB_DEPS
    return {**env, "dependencies": deps}


def write(dst: Path, env: dict) -> None:
    text = yaml.safe_dump(env, sort_keys=False)
    if not dst.exists() or dst.read_text() != text:
        dst.write_text(text)
        print(f"updated {dst}")


def main(plan_path: str = "plan.yaml") -> None:
    plan = yaml.safe_load(Path(plan_path).read_text())
    out = Path("envs/thoa")
    out.mkdir(parents=True, exist_ok=True)
    for name, spec in plan["software_environments"].items():
        src = yaml.safe_load(Path(spec["conda"]).read_text())
        dst = out / f"{name}.yml"
        write(dst, normal_form(thoa_env(src)))
    # ob alone, for slices whose module envs snakemake builds in the job
    # (slice.sh SLICE_BACKEND=conda): thoa can't build every module env.
    dst = out / "ob.yml"
    write(dst, normal_form(thoa_env({"channels": ["conda-forge"]})))


if __name__ == "__main__":
    main(*sys.argv[1:])
