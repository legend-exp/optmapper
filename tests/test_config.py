from __future__ import annotations

import pytest

from optmapper.config import Config, ConfigError, deep_merge, load_site


def test_load_relative_paths(tmp_path):
    cfg_file = tmp_path / "prod" / "config.yaml"
    cfg_file.parent.mkdir()
    cfg_file.write_text(
        """
name: l200-test
output_dir: out
geometry:
  executable: legend-pygeom-l200
  config: geom-config.yaml
  optics_plugin: plugin.py
  env:
    LEGEND_METADATA: $_/legend-metadata
    LEGEND1000_METADATA: $HOME/legend1000-metadata
emission:
  g4gps_spectrum_macro: lar-spectrum.mac
optmap:
  range_in_m: [[-2, 2], [-2, 2], [-3, 3]]
  bins: [80, 80, 120]
statistics:
  nodes: 4
"""
    )
    cfg = Config.load(cfg_file)
    assert cfg.output_dir == str(tmp_path / "prod" / "out")
    assert cfg.geometry.config == str(tmp_path / "prod" / "geom-config.yaml")
    assert cfg.geometry.optics_plugin == str(tmp_path / "prod" / "plugin.py")
    assert cfg.emission.g4gps_spectrum_macro == str(tmp_path / "prod" / "lar-spectrum.mac")
    assert cfg.geometry.env["LEGEND_METADATA"] == str(tmp_path / "prod" / "legend-metadata")
    assert not cfg.geometry.env["LEGEND1000_METADATA"].startswith("$")
    assert cfg.gdml_file == tmp_path / "prod" / "out" / "geom.gdml"
    assert cfg.node_map(3).name == "l200-test-node0003.lh5"
    assert cfg.final_map.name == "l200-test.lh5"
    assert cfg.optmap.reboost_settings() == {
        "range_in_m": [[-2, 2], [-2, 2], [-3, 3]],
        "bins": [80, 80, 120],
    }


def test_single_node_writes_final_map(base_config):
    cfg = Config.from_dict(base_config)
    assert cfg.node_map(0) == cfg.final_map


def test_roundtrip(tmp_path, base_config):
    base_config["execution"] = {"site": "nersc", "slurm": {"node": {"time": "01:00:00"}}}
    cfg = Config.from_dict(base_config)
    cfg.dump(tmp_path / "c.json")
    assert Config.load(tmp_path / "c.json") == cfg


def test_site_presets(base_config):
    base_config["execution"] = {
        "site": "nersc",
        "slurm": {"node": {"time": "08:00:00", "mail-user": "me@example.com", "qos": None}},
    }
    ex = Config.from_dict(base_config).execution
    assert ex.scheduler == "slurm"
    assert ex.slurm["node"]["time"] == "08:00:00"
    assert ex.slurm["node"]["mail-user"] == "me@example.com"
    assert ex.slurm["node"]["qos"] is None
    assert ex.slurm["node"]["account"] == "m2676"
    assert ex.slurm["merge"]["qos"] == "shared"

    assert load_site("local")["scheduler"] == "local"
    with pytest.raises(ConfigError, match="not found"):
        load_site("nonexistent-site")


def test_site_preset_from_file(tmp_path, base_config):
    site = tmp_path / "mysite.yaml"
    site.write_text("scheduler: slurm\nslurm:\n  node:\n    partition: long\n")
    base_config["execution"] = {"site": str(site)}
    assert Config.from_dict(base_config).execution.slurm["node"]["partition"] == "long"


def test_deep_merge():
    a = {"x": {"y": 1, "z": 2}, "w": [1]}
    assert deep_merge(a, {"x": {"y": 3}, "w": [2]}) == {"x": {"y": 3, "z": 2}, "w": [2]}
    assert a["x"]["y"] == 1


@pytest.mark.parametrize(
    ("patch", "match"),
    [
        ({"foo": 1}, "unknown key"),
        ({"geometry": {}}, "exactly one of"),
        ({"geometry": {"gdml": "a.gdml", "executable": "x"}}, "exactly one of"),
        ({"geometry": {"gdml": "a.gdml", "config": "c.yaml"}}, "require 'executable'"),
        ({"geometry": {"gdml": "a.gdml", "env": {"A": "b"}}}, "require 'executable'"),
        ({"emission": {"gaussian": {"mean": 1}}}, "both"),
        ({"emission": {}}, "exactly one of"),
        (
            {"emission": {"spectrum": "a.b", "g4gps_spectrum_macro": "s.mac"}},
            "exactly one of",
        ),
        ({"statistics": {"nodes": 0}}, "positive integer"),
        ({"optmap": {"range_in_m": [[0, 1], [0, 1]], "bins": [1, 1]}}, "three entries"),
        ({"optmap": {"range_in_m": [[0, 1], [0, 2], [0, 1]], "bins": [1, 1, 1]}}, "equal x"),
        (
            {"optmap": {"range_in_m": [[0, 1]] * 3, "bins": [1] * 3, "shape": "sphere"}},
            "shape",
        ),
        ({"optmap": {"range_in_m": [[1, 0]] * 3, "bins": [1] * 3}}, "empty range"),
        ({"execution": {"scheduler": "pbs"}}, "scheduler"),
        ({"execution": {"slurm": {"nodes": {}}}}, "unknown key"),
        ({"name": "a/b"}, "invalid production name"),
    ],
)
def test_invalid(base_config, patch, match):
    with pytest.raises(ConfigError, match=match):
        Config.from_dict(base_config | patch)


def test_missing_key(base_config):
    del base_config["optmap"]
    with pytest.raises(ConfigError, match="missing required key 'optmap'"):
        Config.from_dict(base_config)
