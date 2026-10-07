# Installation

It is the user's responsibility to set up the software environment with
[pixi](https://pixi.sh). Create a production directory with a `pixi.toml` that
pins the software versions (see
[`examples/l200/pixi.toml`](https://github.com/legend-exp/optmapper/tree/main/examples/l200)):

```toml
[workspace]
channels = ["conda-forge"]
name = "optmap-l200"
platforms = ["linux-64"]

[dependencies]
python = "3.12.*"
legend-pygeom-l200 = "==0.11.0"
reboost = "==1.4.0"
remage = ">=0.1.0,<1.2"

[pypi-dependencies]
optmapper = { git = "https://github.com/legend-exp/optmapper" }
```

and install the environment:

```shell
$ pixi install --frozen
```
