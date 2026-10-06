"""Production configuration: parsing, validation and site presets."""

from __future__ import annotations

import copy
import dataclasses
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import dbetto


class ConfigError(ValueError):
    """Invalid production configuration."""


def _check_keys(block: Mapping, allowed: set[str], where: str) -> None:
    unknown = set(block) - allowed
    if unknown:
        msg = f"unknown key(s) {sorted(unknown)} in '{where}', allowed: {sorted(allowed)}"
        raise ConfigError(msg)


def _resolve_path(path: str | os.PathLike | None, base: Path) -> str | None:
    """Make a path from the config absolute, relative paths are relative to `base`."""
    if path is None:
        return None
    p = Path(os.path.expandvars(os.fspath(path))).expanduser()
    return str(p if p.is_absolute() else (base / p).resolve())


def deep_merge(base: Mapping, override: Mapping) -> dict:
    """Recursively merge `override` into a copy of `base`."""
    out = copy.deepcopy(dict(base))
    for k, v in override.items():
        if isinstance(v, Mapping) and isinstance(out.get(k), Mapping):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_site(site: str | Mapping) -> dict:
    """Load an execution site preset.

    `site` is either the name of a preset shipped with this package (e.g. ``nersc``,
    ``slurm``, ``local``), the path to a YAML/JSON preset file or an inline mapping.
    """
    if isinstance(site, Mapping):
        return dict(site)
    preset = resources.files("optmapper") / "sites" / f"{site}.yaml"
    if preset.is_file():
        return dbetto.utils.load_dict(str(preset))
    if Path(site).is_file():
        return dbetto.utils.load_dict(site)
    available = sorted(
        Path(p.name).stem for p in (resources.files("optmapper") / "sites").iterdir()
    )
    msg = f"site preset '{site}' not found, available presets: {available} (or a file path)"
    raise ConfigError(msg)


@dataclass
class GeometryConfig:
    """Either a GDML file or a geometry generator invocation."""

    gdml: str | None = None
    executable: str | None = None
    config: str | dict | None = None
    optics_plugin: str | None = None
    metadata: str | None = None

    @classmethod
    def from_dict(cls, d: Mapping, base: Path) -> GeometryConfig:
        _check_keys(d, {f.name for f in dataclasses.fields(cls)}, "geometry")
        g = cls(**d)
        if (g.gdml is None) == (g.executable is None):
            msg = "geometry: specify exactly one of 'gdml' or 'executable'"
            raise ConfigError(msg)
        if g.gdml is not None and any(
            v is not None for v in (g.config, g.optics_plugin, g.metadata)
        ):
            msg = "geometry: 'config', 'optics_plugin' and 'metadata' require 'executable'"
            raise ConfigError(msg)
        g.gdml = _resolve_path(g.gdml, base)
        if not isinstance(g.config, Mapping):
            g.config = _resolve_path(g.config, base)
        else:
            g.config = dict(g.config)
        g.optics_plugin = _resolve_path(g.optics_plugin, base)
        g.metadata = _resolve_path(g.metadata, base)
        return g


@dataclass
class EmissionConfig:
    """Energy spectrum of the generated optical photons.

    Either `spectrum`, the dotted path of a function with signature
    ``f(filename: str, output_macro: bool)`` like
    :func:`pygeomoptics.lar.g4gps_lar_emissions_spectrum`, or a `gaussian` with a
    `mean` (energy or wavelength) and a `sigma` (energy).
    """

    spectrum: str | None = None
    gaussian: dict | None = None

    @classmethod
    def from_dict(cls, d: Mapping) -> EmissionConfig:
        _check_keys(d, {"spectrum", "gaussian"}, "emission")
        e = cls(**d)
        if (e.spectrum is None) == (e.gaussian is None):
            msg = "emission: specify exactly one of 'spectrum' or 'gaussian'"
            raise ConfigError(msg)
        if e.gaussian is not None:
            e.gaussian = dict(e.gaussian)
            _check_keys(e.gaussian, {"mean", "sigma"}, "emission.gaussian")
            if set(e.gaussian) != {"mean", "sigma"}:
                msg = "emission.gaussian: both 'mean' and 'sigma' are required"
                raise ConfigError(msg)
        return e


@dataclass
class StatisticsConfig:
    nodes: int = 1
    runs_per_node: int = 1
    primaries_per_process: int = 1_000_000
    processes_per_node: int | None = None
    """Number of remage processes per run, defaults to all CPUs available to the job."""

    @classmethod
    def from_dict(cls, d: Mapping) -> StatisticsConfig:
        _check_keys(d, {f.name for f in dataclasses.fields(cls)}, "statistics")
        s = cls(**d)
        for f in dataclasses.fields(s):
            v = getattr(s, f.name)
            if v is not None and (not isinstance(v, int) or v < 1):
                msg = f"statistics.{f.name} must be a positive integer, got {v!r}"
                raise ConfigError(msg)
        return s


@dataclass
class OptmapConfig:
    """Optical map binning and the primary vertex confinement derived from it."""

    range_in_m: list[list[float]]
    bins: list[int]
    volume: str | list[str] = "liquid_argon"
    shape: str = "cylinder"
    inner_radius_in_m: float = 0
    detectors: list | None = None

    @classmethod
    def from_dict(cls, d: Mapping) -> OptmapConfig:
        _check_keys(d, {f.name for f in dataclasses.fields(cls)}, "optmap")
        for k in ("range_in_m", "bins"):
            if k not in d:
                msg = f"optmap.{k} is required"
                raise ConfigError(msg)
        o = cls(**d)
        o.range_in_m = [[float(lo), float(hi)] for lo, hi in o.range_in_m]
        o.bins = [int(b) for b in o.bins]
        if len(o.range_in_m) != 3 or len(o.bins) != 3:
            msg = "optmap.range_in_m and optmap.bins must have three entries (x, y, z)"
            raise ConfigError(msg)
        if any(hi <= lo for lo, hi in o.range_in_m) or any(b < 1 for b in o.bins):
            msg = "optmap: empty range or invalid bin count"
            raise ConfigError(msg)
        if o.shape not in ("cylinder", "box", "none"):
            msg = f"optmap.shape must be one of cylinder, box, none, got {o.shape!r}"
            raise ConfigError(msg)
        if o.shape == "cylinder":
            (x0, x1), (y0, y1), _ = o.range_in_m
            if abs((x1 - x0) - (y1 - y0)) > 1e-9:
                msg = "optmap.shape cylinder requires equal x and y ranges"
                raise ConfigError(msg)
            if not 0 <= o.inner_radius_in_m < (x1 - x0) / 2:
                msg = "optmap.inner_radius_in_m must be >= 0 and smaller than the outer radius"
                raise ConfigError(msg)
        elif o.inner_radius_in_m != 0:
            msg = "optmap.inner_radius_in_m is only valid for shape cylinder"
            raise ConfigError(msg)
        return o

    def reboost_settings(self) -> dict:
        """Map settings in the format expected by :mod:`reboost.optmap`."""
        return {"range_in_m": self.range_in_m, "bins": self.bins}


@dataclass
class ProcessingConfig:
    stp_files_per_map: int = 32
    parallel_maps: int = 8
    procs_per_map: int = 16
    merge_procs: int = 8
    bufsize: int = 50_000
    keep_stp: bool = False

    @classmethod
    def from_dict(cls, d: Mapping) -> ProcessingConfig:
        _check_keys(d, {f.name for f in dataclasses.fields(cls)}, "processing")
        return cls(**d)


@dataclass
class AdvancedConfig:
    template: str | None = None
    """Custom macro template, see the default ``templates/optmap.mac``."""
    pre_init_commands: list[str] = field(default_factory=list)
    """Macro commands inserted before ``/run/initialize``."""
    commands: list[str] = field(default_factory=list)
    """Macro commands inserted before ``/run/beamOn``."""
    remage_args: list[str] = field(default_factory=lambda: ["--ignore-warnings"])

    @classmethod
    def from_dict(cls, d: Mapping, base: Path) -> AdvancedConfig:
        _check_keys(d, {f.name for f in dataclasses.fields(cls)}, "advanced")
        a = cls(**d)
        a.template = _resolve_path(a.template, base)
        return a


@dataclass
class ExecutionConfig:
    """Where and how the jobs run, the result of merging a site preset with user overrides."""

    scheduler: str = "local"
    scratch_dir: str | None = None
    """Fast temporary storage, environment variables are expanded on the worker."""
    launcher: list[str] | None = None
    """Command prefix to run the worker in the software environment (e.g. ``pixi run``)."""
    env: dict[str, str] = field(default_factory=dict)
    slurm: dict = field(default_factory=dict)
    """``sbatch`` options for the ``node`` and ``merge`` jobs."""

    @classmethod
    def from_dict(cls, d: Mapping) -> ExecutionConfig:
        d = dict(d)
        site = load_site(d.pop("site", "local"))
        merged = deep_merge(site, d)
        _check_keys(merged, {f.name for f in dataclasses.fields(cls)}, "execution")
        e = cls(**merged)
        if e.scheduler not in ("slurm", "local"):
            msg = f"execution.scheduler must be slurm or local, got {e.scheduler!r}"
            raise ConfigError(msg)
        _check_keys(e.slurm, {"node", "merge"}, "execution.slurm")
        e.env = {k: str(v) for k, v in e.env.items()}
        return e


@dataclass
class Config:
    name: str
    output_dir: str
    geometry: GeometryConfig
    emission: EmissionConfig
    optmap: OptmapConfig
    statistics: StatisticsConfig = field(default_factory=StatisticsConfig)
    processing: ProcessingConfig = field(default_factory=ProcessingConfig)
    advanced: AdvancedConfig = field(default_factory=AdvancedConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)

    @classmethod
    def from_dict(cls, d: Mapping, base: Path | None = None) -> Config:
        """Build a config from a mapping, relative paths are resolved relative to `base`."""
        base = Path.cwd() if base is None else Path(base).resolve()
        _check_keys(d, {f.name for f in dataclasses.fields(cls)}, "top level")
        for k in ("name", "output_dir", "geometry", "emission", "optmap"):
            if k not in d:
                msg = f"missing required key '{k}'"
                raise ConfigError(msg)
        if not str(d["name"]) or "/" in str(d["name"]):
            msg = f"invalid production name {d['name']!r}"
            raise ConfigError(msg)
        try:
            return cls(
                name=str(d["name"]),
                output_dir=str(_resolve_path(d["output_dir"], base)),
                geometry=GeometryConfig.from_dict(d["geometry"], base),
                emission=EmissionConfig.from_dict(d["emission"]),
                optmap=OptmapConfig.from_dict(d["optmap"]),
                statistics=StatisticsConfig.from_dict(d.get("statistics", {})),
                processing=ProcessingConfig.from_dict(d.get("processing", {})),
                advanced=AdvancedConfig.from_dict(d.get("advanced", {}), base),
                execution=ExecutionConfig.from_dict(d.get("execution", {})),
            )
        except TypeError as e:
            # e.g. a mapping where a list is expected
            raise ConfigError(str(e)) from e

    @classmethod
    def load(cls, path: str | os.PathLike) -> Config:
        """Load a user configuration file, relative paths are relative to its directory."""
        return cls.from_dict(dbetto.utils.load_dict(str(path)), Path(path).resolve().parent)

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        # the site preset has already been merged in, do not apply any on reload
        d["execution"]["site"] = {}
        return d

    def dump(self, path: str | os.PathLike) -> None:
        """Write the fully resolved configuration, to be read back by the workers."""
        with Path(path).open("w") as f:
            json.dump(self.to_dict(), f, indent=2)

    # file layout of a production

    @property
    def resolved_config_file(self) -> Path:
        return Path(self.output_dir) / "optmapper-config.json"

    @property
    def gdml_file(self) -> Path:
        if self.geometry.gdml is not None:
            return Path(self.geometry.gdml)
        return Path(self.output_dir) / "geom.gdml"

    @property
    def log_dir(self) -> Path:
        return Path(self.output_dir) / "logs"

    @property
    def final_map(self) -> Path:
        return Path(self.output_dir) / f"{self.name}.lh5"

    def node_map(self, index: int) -> Path:
        """Output map of a single node, the final map if there is only one node."""
        if self.statistics.nodes == 1:
            return self.final_map
        return Path(self.output_dir) / "nodes" / f"{self.name}-node{index:04d}.lh5"
