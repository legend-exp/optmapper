from __future__ import annotations

import json
import os
import sys

import pytest

from legendoptcarto.config import Config, ConfigError
from legendoptcarto.geometry import build_gdml

FAKE_GENERATOR = """\
#!{python}
import json, os, sys
from pathlib import Path

Path(sys.argv[-1]).write_text("<gdml/>")
Path(sys.argv[-1]).with_suffix(".call.json").write_text(
    json.dumps({{"argv": sys.argv[1:], "metadata": os.environ.get("LEGEND_METADATA")}})
)
"""


def test_build_gdml(tmp_path, base_config, monkeypatch):
    exe = tmp_path / "bin" / "legend-pygeom-l200"
    exe.parent.mkdir()
    exe.write_text(FAKE_GENERATOR.format(python=sys.executable))
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", f"{exe.parent}{os.pathsep}{os.environ['PATH']}")

    (tmp_path / "geom-config.yaml").write_text("fiber_modules: detailed\nfile: $_/x.json\n")
    base_config["geometry"] = {
        "executable": "legend-pygeom-l200",
        "config": "geom-config.yaml",
        "optics_plugin": "plugin.py",
        "metadata": "legend-metadata",
    }
    cfg = Config.from_dict(base_config, tmp_path)
    cfg.log_dir.mkdir(parents=True)
    build_gdml(cfg)

    assert cfg.gdml_file.read_text() == "<gdml/>"
    call = json.loads(cfg.gdml_file.with_suffix(".call.json").read_text())
    stored = tmp_path / "out" / "geom-config.json"
    assert call["argv"] == [
        *("--verbose", "--config", str(stored)),
        *("--pygeom-optics-plugin", str(tmp_path / "plugin.py")),
        *("--", str(cfg.gdml_file)),
    ]
    assert call["metadata"] == str(tmp_path / "legend-metadata")
    assert json.loads(stored.read_text()) == {
        "fiber_modules": "detailed",
        "file": f"{tmp_path}/x.json",
    }

    # inline configuration
    cfg.geometry.config = {"public_geom": True}
    build_gdml(cfg)
    assert json.loads(stored.read_text()) == {"public_geom": True}

    cfg.geometry.executable = "legend-pygeom-nonexistent"
    with pytest.raises(ConfigError, match="not found"):
        build_gdml(cfg)
