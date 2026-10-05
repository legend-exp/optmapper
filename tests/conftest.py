from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

FAKE_REMAGE = """\
#!{python}
# minimal remage stand-in: writes one stp file per process, like remage --procs N
import argparse
from pathlib import Path

import lh5
import numpy as np
from lgdo import Array, Table

p = argparse.ArgumentParser()
p.add_argument("--gdml-files")
p.add_argument("--output-file")
p.add_argument("--procs", type=int, default=1)
p.add_argument("--flat-output", action="store_true")
p.add_argument("--ignore-warnings", action="store_true")
p.add_argument("macro")
args = p.parse_args()

assert Path(args.gdml_files).is_file()
assert "/run/beamOn" in Path(args.macro).read_text()

rng = np.random.default_rng()
out = Path(args.output_file)
for i in range(args.procs):
    n = 100
    evtid = np.arange(n)
    loc = rng.uniform(-1, 1, size=(n, 3))
    vtx = Table({{
        "evtid": Array(evtid),
        "xloc": Array(loc[:, 0]),
        "yloc": Array(loc[:, 1]),
        "zloc": Array(loc[:, 2]),
        "n_part": Array(np.ones(n)),
        "time": Array(np.ones(n)),
    }})
    mask = rng.uniform(size=n) < 0.3
    m = int(mask.sum())
    opt = Table({{
        "evtid": Array(evtid[mask]),
        "det_uid": Array(rng.integers(1, 4, size=m)),
        "wavelength": Array(np.full(m, 400.0)),
        "time": Array(np.ones(m)),
    }})
    fn = out.with_name(f"{{out.stem}}_p{{i}}.lh5")
    lh5.write(vtx, name="vtx", lh5_file=fn, wo_mode="overwrite_file")
    lh5.write(opt, name="stp/optical", lh5_file=fn, wo_mode="overwrite")
"""

FAKE_SBATCH = """\
#!{python}
import json, os, sys
from pathlib import Path

log = Path(os.environ["FAKE_SBATCH_LOG"])
calls = log.read_text().splitlines() if log.exists() else []
with log.open("a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
print(f"{{1000 + len(calls)}};perlmutter")
"""


def _write_exe(path: Path, content: str) -> None:
    path.write_text(content.format(python=sys.executable))
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def fake_bin(tmp_path, monkeypatch):
    """Put fake remage and sbatch executables in PATH, return the sbatch call log."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _write_exe(bindir / "remage", FAKE_REMAGE)
    _write_exe(bindir / "sbatch", FAKE_SBATCH)
    # the worker must be found as well
    monkeypatch.setenv(
        "PATH", f"{bindir}{os.pathsep}{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}"
    )
    monkeypatch.delenv("PIXI_PROJECT_MANIFEST", raising=False)
    monkeypatch.delenv("SLURM_JOB_ID", raising=False)
    monkeypatch.delenv("SLURM_CPUS_ON_NODE", raising=False)
    log = tmp_path / "sbatch-calls.jsonl"
    monkeypatch.setenv("FAKE_SBATCH_LOG", str(log))
    return log


@pytest.fixture
def gdml_file():
    return Path(__file__).parent / "optmap_dets.gdml"


@pytest.fixture
def base_config(tmp_path, gdml_file):
    return {
        "name": "test-map",
        "output_dir": str(tmp_path / "out"),
        "geometry": {"gdml": str(gdml_file)},
        "emission": {"gaussian": {"mean": "128 nm", "sigma": "0.22 eV"}},
        "optmap": {"range_in_m": [[-1, 1], [-1, 1], [-1, 1]], "bins": [4, 4, 4]},
        "statistics": {"nodes": 1, "runs_per_node": 2, "processes_per_node": 3},
        "processing": {"stp_files_per_map": 2, "parallel_maps": 2, "procs_per_map": 1},
        "execution": {"site": "local"},
    }
