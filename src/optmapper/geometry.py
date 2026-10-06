"""Build the GDML geometry with a LEGEND geometry generator."""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
from pathlib import Path

import dbetto

from .config import Config, ConfigError

log = logging.getLogger(__name__)


def build_gdml(cfg: Config) -> None:
    """Run the geometry generator (e.g. ``legend-pygeom-l200``) to write ``cfg.gdml_file``.

    The geometry configuration is stored next to the GDML file, for the record.
    """
    geom = cfg.geometry
    assert geom.executable is not None
    if shutil.which(geom.executable) is None:
        msg = f"geometry generator '{geom.executable}' not found, is it installed?"
        raise ConfigError(msg)

    cmd = [geom.executable, "--verbose"]

    if geom.config is not None:
        if isinstance(geom.config, dict):
            gconfig = geom.config
        else:
            gconfig = dbetto.utils.load_dict(geom.config)
            # expand $_ to the directory holding the file, as dbetto does
            dbetto.Props.subst_vars(gconfig, var_values={"_": str(Path(geom.config).parent)})
        config_file = Path(cfg.output_dir) / "geom-config.json"
        dbetto.utils.write_dict(gconfig, str(config_file))
        cmd += ["--config", str(config_file)]

    if geom.optics_plugin is not None:
        cmd += ["--pygeom-optics-plugin", geom.optics_plugin]

    cmd += ["--", str(cfg.gdml_file)]

    env = os.environ | geom.env

    log_file = cfg.log_dir / "geometry.log"
    log.info("building geometry: %s (log in %s)", shlex.join(cmd), log_file)
    with log_file.open("w") as f:
        ret = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, env=env, check=False)
    if ret.returncode != 0:
        msg = f"geometry generation failed, see {log_file}"
        raise RuntimeError(msg)
