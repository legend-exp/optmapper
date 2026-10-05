from __future__ import annotations

import json
import shlex

import pytest
from reboost.optmap.create import list_optical_maps

from legendoptcarto.cli import optcarto_cli, resolve_launcher
from legendoptcarto.config import Config


def _write(tmp_path, cfg):
    fn = tmp_path / "config.json"
    fn.write_text(json.dumps(cfg))
    return str(fn)


def test_slurm_submission(tmp_path, base_config, fake_bin, monkeypatch):
    monkeypatch.setenv("PIXI_PROJECT_MANIFEST", "/prod/pixi.toml")
    base_config["statistics"]["nodes"] = 3
    base_config["execution"] = {
        "site": "nersc",
        "slurm": {"node": {"mail-user": "me@example.com", "exclusive": True}},
    }
    optcarto_cli([_write(tmp_path, base_config)])

    calls = [json.loads(line) for line in fake_bin.read_text().splitlines()]
    assert len(calls) == 4

    out = tmp_path / "out"
    resolved = out / "optcarto-config.json"
    for i, call in enumerate(calls[:3]):
        assert "--parsable" in call
        assert "--account=m2676" in call
        assert "--qos=regular" in call
        assert "--exclusive" in call
        assert "--mail-user=me@example.com" in call
        assert f"--job-name=test-map-node{i:04d}" in call
        assert f"--output={out}/logs/test-map-node{i:04d}-%j.log" in call
        assert not any(a.startswith("--dependency") for a in call)
        assert call[-2] == "--wrap"
        assert shlex.split(call[-1]) == [
            *("pixi", "run", "--as-is", "--manifest-path", "/prod/pixi.toml"),
            *("legend-optcarto-work", "--verbose", "node", "--config", str(resolved)),
            *("--index", str(i)),
        ]

    merge = calls[3]
    assert "--dependency=afterany:1000:1001:1002" in merge
    assert "--qos=shared" in merge
    assert shlex.split(merge[-1])[-3:] == ["merge", "--config", str(resolved)]

    # only the resolved config and the log directory, no job scripts
    assert sorted(p.name for p in out.iterdir()) == ["logs", "optcarto-config.json"]
    assert Config.load(resolved).statistics.nodes == 3

    # refuse to overwrite a production
    with pytest.raises(SystemExit):
        optcarto_cli([_write(tmp_path, base_config)])
    optcarto_cli([_write(tmp_path, base_config), "--force"])


def test_single_node_has_no_merge_job(tmp_path, base_config, fake_bin):
    base_config["execution"] = {"site": "slurm"}
    optcarto_cli([_write(tmp_path, base_config)])
    calls = fake_bin.read_text().splitlines()
    assert len(calls) == 1
    assert "--exclusive" in json.loads(calls[0])


def test_dry_run(tmp_path, base_config, fake_bin):
    base_config["execution"] = {"site": "nersc"}
    optcarto_cli([_write(tmp_path, base_config), "--dry-run"])
    assert not fake_bin.exists()
    assert not (tmp_path / "out").exists()


def test_invalid_config(tmp_path, base_config, fake_bin):
    base_config["emission"] = {"spectrum": "pygeomoptics.nonexistent"}
    with pytest.raises(SystemExit):
        optcarto_cli([_write(tmp_path, base_config)])
    assert not fake_bin.exists()


def test_launcher(base_config, monkeypatch):
    monkeypatch.delenv("PIXI_PROJECT_MANIFEST", raising=False)
    cfg = Config.from_dict(base_config)
    assert resolve_launcher(cfg) == []

    monkeypatch.setenv("PIXI_PROJECT_MANIFEST", "/a/pixi.toml")
    monkeypatch.setenv("PIXI_ENVIRONMENT_NAME", "prod")
    assert resolve_launcher(cfg)[-2:] == ["--environment", "prod"]

    cfg.execution.launcher = ["srun", "-n1"]
    assert resolve_launcher(cfg) == ["srun", "-n1"]


@pytest.mark.usefixtures("fake_bin")
@pytest.mark.parametrize("nodes", [1, 2])
def test_local_production(tmp_path, base_config, nodes):
    base_config["statistics"]["nodes"] = nodes
    base_config["processing"]["keep_stp"] = True
    optcarto_cli([_write(tmp_path, base_config)])

    out = tmp_path / "out"
    final = out / "test-map.lh5"
    assert final.is_file()
    assert list_optical_maps(str(final)) == [
        "channels/S01",
        "channels/S02",
        "channels/S03",
        "all",
    ]
    assert (out / "test-map.mac").is_file()
    # 2 runs x 3 processes per node
    assert len(list((out / "stp").glob("node*/*.lh5"))) == 6 * nodes
    assert not (out / "scratch" / "test-map").exists() or not any(
        (out / "scratch" / "test-map").iterdir()
    )
    if nodes > 1:
        assert len(list((out / "nodes").glob("*.lh5"))) == nodes
        assert len(list((out / "logs").glob("test-map-merge-*.log"))) == 1
