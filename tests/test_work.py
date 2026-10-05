from __future__ import annotations

import logging
import subprocess

import pytest
from reboost.optmap.create import list_optical_maps

from legendoptcarto.config import Config
from legendoptcarto.work import _natural_key, run_merge, work_cli


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

    with pytest.raises(RuntimeError, match="no node map found"):
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
    work_cli(
        [
            *("create", "--config", str(cfg.resolved_config_file)),
            *(str(tmp_path / "stp_p0.lh5"), str(cfg.node_map(1))),
        ]
    )

    with caplog.at_level(logging.WARNING):
        work_cli(["merge", "--config", str(cfg.resolved_config_file)])
    assert "2 of 3 node maps are missing" in caplog.text
    assert list_optical_maps(str(cfg.final_map))[-1] == "all"
    assert not list(out.glob(".*part*"))
