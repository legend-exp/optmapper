from __future__ import annotations

import pytest

from optmapper.config import Config, ConfigError
from optmapper.macro import confinement_commands, energy_commands, render_macro


def test_default_macro(base_config):
    macro = render_macro(Config.from_dict(base_config), 1234)
    assert "/run/initialize" in macro
    assert macro.strip().endswith("/run/beamOn 1234")
    assert "/gps/ene/type     Gauss" in macro
    assert "/RMG/Generator/Confinement/Geometrical/AddSolid Cylinder" in macro
    assert "$" not in macro


def test_cylinder(base_config):
    base_config["optmap"] = {
        "range_in_m": [[-2, 2], [-1, 3], [-3, 4]],
        "bins": [1, 1, 1],
        "inner_radius_in_m": 0.5,
    }
    cmds = confinement_commands(Config.from_dict(base_config).optmap)
    geo = "/RMG/Generator/Confinement/Geometrical"
    assert cmds[-6:] == [
        f"{geo}/CenterPositionX 0 m",
        f"{geo}/CenterPositionY 1 m",
        f"{geo}/CenterPositionZ 0.5 m",
        f"{geo}/Cylinder/InnerRadius 0.5 m",
        f"{geo}/Cylinder/OuterRadius 2 m",
        f"{geo}/Cylinder/Height 7 m",
    ]
    assert "/RMG/Generator/Confinement/Physical/AddVolume liquid_argon" in cmds


def test_box_and_volumes(base_config):
    base_config["optmap"] = {
        "range_in_m": [[-1, 1], [0, 1], [-3, 3]],
        "bins": [1, 1, 1],
        "shape": "box",
        "volume": ["lar_a", "lar_b"],
    }
    cmds = confinement_commands(Config.from_dict(base_config).optmap)
    assert "/RMG/Generator/Confinement/Physical/AddVolume lar_b" in cmds
    assert "/RMG/Generator/Confinement/Geometrical/AddSolid Box" in cmds
    assert cmds[-3:] == [
        "/RMG/Generator/Confinement/Geometrical/Box/XLength 2 m",
        "/RMG/Generator/Confinement/Geometrical/Box/YLength 1 m",
        "/RMG/Generator/Confinement/Geometrical/Box/ZLength 6 m",
    ]


def test_no_shape(base_config):
    base_config["optmap"] |= {"shape": "none"}
    cmds = confinement_commands(Config.from_dict(base_config).optmap)
    assert cmds == [
        "/RMG/Generator/Confine Volume",
        "/RMG/Generator/Confinement/Physical/AddVolume liquid_argon",
    ]


def test_gaussian(base_config):
    base_config["emission"] = {"gaussian": {"mean": 9.68, "sigma": "220 meV"}}
    cmds = energy_commands(Config.from_dict(base_config).emission)
    assert cmds[1:] == ["/gps/ene/mono     9.68 eV", "/gps/ene/sigma    0.22 eV"]

    base_config["emission"] = {"gaussian": {"mean": "128 nm", "sigma": "0.2 eV"}}
    cmds = energy_commands(Config.from_dict(base_config).emission)
    assert cmds[1].startswith("/gps/ene/mono     9.68")

    base_config["emission"] = {"gaussian": {"mean": "1 kg", "sigma": "0.2 eV"}}
    with pytest.raises(ConfigError, match="kilogram"):
        energy_commands(Config.from_dict(base_config).emission)


def test_pygeomoptics_spectrum(base_config):
    base_config["emission"] = {"spectrum": "pygeomoptics.lar.g4gps_lar_emissions_spectrum"}
    cmds = energy_commands(Config.from_dict(base_config).emission)
    assert "/gps/ene/type     Arb" in cmds
    assert sum(c.startswith("/gps/hist/point") for c in cmds) > 10


@pytest.mark.parametrize("spec", ["pygeomoptics.lar.nonexistent", "nomodule.func", "os.getcwd"])
def test_bad_spectrum(base_config, spec):
    base_config["emission"] = {"spectrum": spec}
    with pytest.raises(ConfigError):
        energy_commands(Config.from_dict(base_config).emission)


def test_advanced(tmp_path, base_config):
    base_config["advanced"] = {
        "pre_init_commands": ["/RMG/Processes/HadronicPhysics None"],
        "commands": ["/RMG/Output/ActivateOutputScheme Track"],
    }
    macro = render_macro(Config.from_dict(base_config), 1)
    assert macro.index("HadronicPhysics") < macro.index("/run/initialize")
    assert macro.index("/gps/ang/type") < macro.index("ActivateOutputScheme")
    assert macro.index("ActivateOutputScheme") < macro.index("/run/beamOn")

    template = tmp_path / "t.mac"
    template.write_text("${confinement_commands}\n${energy_commands}\n/run/beamOn ${n_events}\n")
    base_config["advanced"] = {"template": str(template)}
    macro = render_macro(Config.from_dict(base_config), 5)
    assert macro.startswith("/RMG/Generator/Confine Volume")

    template.write_text("${typo}\n")
    with pytest.raises(ConfigError, match="placeholder"):
        render_macro(Config.from_dict(base_config), 5)
