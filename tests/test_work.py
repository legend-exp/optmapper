from __future__ import annotations

import json
import logging
import os
import stat
import subprocess
import sys

import pytest
from reboost.optmap.create import list_optical_maps

from optmapper.config import Config
from optmapper.work import (
    _create_map,
    _natural_key,
    _reboost_create_args,
    run_merge,
    run_nodes,
    srun_command,
    work_cli,
)

# srun stand-in: records its arguments, fails for node 1
FAKE_SRUN = """\
#!{python}
import json, os, sys
from pathlib import Path

with Path(os.environ["FAKE_SRUN_LOG"]).open("a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
sys.exit(1 if sys.argv[-1] == "1" else 0)
"""


def test_natural_key(tmp_path):
    files = [tmp_path / f"x_p{i}.lh5" for i in (10, 2, 1)]
    assert [f.name for f in sorted(files, key=_natural_key)] == [
        "x_p1.lh5",
        "x_p2.lh5",
        "x_p10.lh5",
    ]


@pytest.mark.usefixtures("fake_bin")
def test_merge_with_failed_nodes(tmp_path, base_config, caplog):
    base_config["statistics"]["nodes"] = 3
    cfg = Config.from_dict(base_config)
    out = tmp_path / "out"
    out.mkdir()
    cfg.dump(cfg.resolved_config_file)

    with pytest.raises(RuntimeError, match="all node jobs failed"):
        run_merge(cfg)

    # one node map from fake stp files
    stp = tmp_path / "stp.lh5"
    (tmp_path / "m.mac").write_text("/run/beamOn 1\n")
    subprocess.run(
        [
            *("remage", "--gdml-files", str(cfg.gdml_file)),
            *("--output-file", str(stp), "--", str(tmp_path / "m.mac")),
        ],
        check=True,
    )
    cfg.node_map(1).parent.mkdir(parents=True)
    _create_map(_reboost_create_args(cfg, tmp_path), [tmp_path / "stp_p0.lh5"], cfg.node_map(1))

    with caplog.at_level(logging.WARNING):
        work_cli(["merge", "--config", str(cfg.resolved_config_file)])
    assert "2 of 3 node maps are missing" in caplog.text
    assert list_optical_maps(str(cfg.final_map))[-1] == "all"
    assert not list(out.glob(".*part*"))


def test_srun_command(base_config, monkeypatch):
    base_config["execution"] = {"site": "nersc", "slurm": {"srun": {"cpu-bind": "cores"}}}
    cfg = Config.from_dict(base_config)
    monkeypatch.setenv("SLURM_CPUS_ON_NODE", "256")
    cmd = srun_command(cfg, 3, "/out/optmapper-config.json")
    assert cmd[0] == "srun"
    for opt in ("--nodes=1", "--ntasks=1", "--cpus-per-task=256", "--cpu-bind=cores"):
        assert opt in cmd
    assert f"--output={cfg.log_dir}/test-map-node0003-%j.log" in cmd
    assert cmd[-9:] == [
        *(sys.executable, "-m", "optmapper.work", "--verbose", "node"),
        *("--config", "/out/optmapper-config.json", "--index", "3"),
    ]


def test_run_nodes(tmp_path, base_config, monkeypatch):
    cfg = Config.from_dict(base_config)
    with pytest.raises(RuntimeError, match="requires a Slurm allocation"):
        run_nodes(cfg, 0, 3, "c.json")

    srun = tmp_path / "bin" / "srun"
    srun.parent.mkdir()
    srun.write_text(FAKE_SRUN.format(python=sys.executable))
    srun.chmod(srun.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "srun.jsonl"
    monkeypatch.setenv("PATH", f"{srun.parent}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_SRUN_LOG", str(log))
    monkeypatch.setenv("SLURM_JOB_NUM_NODES", "3")
    monkeypatch.setenv("SLURM_CPUS_ON_NODE", "8")

    # all steps run even if one fails
    with pytest.raises(RuntimeError, match="1 of 3 nodes failed: 1"):
        run_nodes(cfg, 0, 3, "c.json")
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert sorted(c[-1] for c in calls) == ["0", "1", "2"]
