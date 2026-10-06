"""Composition of the remage macro from a template and the production configuration."""

from __future__ import annotations

import importlib
import inspect
import string
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from importlib import resources
from pathlib import Path

import pint

from .config import Config, ConfigError, EmissionConfig, OptmapConfig

ureg = pint.get_application_registry()


def _quantity(value: str | float, default_unit: str, what: str) -> pint.Quantity:
    try:
        q = ureg.Quantity(value)
    except (pint.PintError, ValueError) as e:
        msg = f"cannot interpret {what} = {value!r} as a quantity: {e}"
        raise ConfigError(msg) from e
    return ureg.Quantity(q.m, default_unit) if q.dimensionless else q


def confinement_commands(optmap: OptmapConfig) -> list[str]:
    """Primary vertex confinement to the optical map volume(s) and bounds."""
    volumes = [optmap.volume] if isinstance(optmap.volume, str) else optmap.volume
    cmds = [
        "/RMG/Generator/Confine Volume",
        "/RMG/Generator/Confinement/ForceContainmentCheck",
    ]

    if optmap.shape != "none":
        cmds += ["/RMG/Generator/Confinement/SamplingMode IntersectPhysicalWithGeometrical"]
    cmds += [f"/RMG/Generator/Confinement/Physical/AddVolume {v}" for v in volumes]
    if optmap.shape == "none":
        return cmds

    geo = "/RMG/Generator/Confinement/Geometrical"
    center = [(lo + hi) / 2 for lo, hi in optmap.range_in_m]
    size = [hi - lo for lo, hi in optmap.range_in_m]
    cmds += [f"{geo}/AddSolid {optmap.shape.capitalize()}"]
    cmds += [f"{geo}/CenterPosition{ax} {c:g} m" for ax, c in zip("XYZ", center, strict=True)]

    if optmap.shape == "cylinder":
        cmds += [
            f"{geo}/Cylinder/InnerRadius {optmap.inner_radius_in_m:g} m",
            f"{geo}/Cylinder/OuterRadius {size[0] / 2:g} m",
            f"{geo}/Cylinder/Height {size[2]:g} m",
        ]
    else:
        cmds += [f"{geo}/Box/{ax}Length {s:g} m" for ax, s in zip("XYZ", size, strict=True)]
    return cmds


def load_spectrum_function(spec: str) -> Callable[[str, bool], None]:
    """Import a spectrum function from its dotted path, e.g. ``pygeomoptics.lar.g4gps_...``."""
    module_name, _, func_name = spec.rpartition(".")
    try:
        func = getattr(importlib.import_module(module_name), func_name)
    except (ImportError, AttributeError, ValueError) as e:
        msg = f"cannot import emission spectrum function '{spec}': {e}"
        raise ConfigError(msg) from e
    if not callable(func) or len(inspect.signature(func).parameters) != 2:
        msg = f"'{spec}' must be a function f(filename, output_macro), like pygeomoptics' g4gps_*"
        raise ConfigError(msg)
    return func


@contextmanager
def _optics_plugin(plugin: str | None) -> Iterator[None]:
    """Apply a pygeom-optics plugin, restoring the original material properties afterwards."""
    if plugin is None:
        yield
        return
    from pygeomoptics import store  # noqa: PLC0415

    store.load_user_material_code(plugin)
    try:
        yield
    finally:
        store.reset_all_to_original()


def energy_commands(emission: EmissionConfig, optics_plugin: str | None = None) -> list[str]:
    """GPS energy distribution of the optical photons.

    The pygeom-optics `optics_plugin`, if any, is applied while generating the spectrum, so
    that it sees the same material properties as the geometry.
    """
    if emission.gaussian is not None:
        try:
            with ureg.context("sp"):
                mean = _quantity(emission.gaussian["mean"], "eV", "gaussian mean").to("eV")
            sigma = _quantity(emission.gaussian["sigma"], "eV", "gaussian sigma").to("eV")
        except pint.DimensionalityError as e:
            msg = f"emission.gaussian: {e}"
            raise ConfigError(msg) from e
        return [
            "/gps/ene/type     Gauss",
            f"/gps/ene/mono     {mean.m:.6g} eV",
            f"/gps/ene/sigma    {sigma.m:.6g} eV",
        ]

    assert emission.spectrum is not None
    func = load_spectrum_function(emission.spectrum)
    with _optics_plugin(optics_plugin), tempfile.TemporaryDirectory() as tmpdir:
        fn = Path(tmpdir) / "spectrum.mac"
        func(str(fn), True)
        return fn.read_text().strip().splitlines()


def render_macro(cfg: Config, n_events: int) -> str:
    """Return the full remage macro for this production."""
    if cfg.advanced.template is not None:
        template = Path(cfg.advanced.template).read_text()
    else:
        template = (resources.files("optmapper") / "templates" / "optmap.mac").read_text()

    try:
        return string.Template(template).substitute(
            name=cfg.name,
            pre_init_commands="\n".join(cfg.advanced.pre_init_commands),
            confinement_commands="\n".join(confinement_commands(cfg.optmap)),
            energy_commands="\n".join(energy_commands(cfg.emission, cfg.geometry.optics_plugin)),
            commands="\n".join(cfg.advanced.commands),
            n_events=n_events,
        )
    except (KeyError, ValueError) as e:
        msg = f"invalid placeholder in macro template: {e}"
        raise ConfigError(msg) from e
