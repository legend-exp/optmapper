# Welcome to optmapper's documentation!

An optical map gives, for each position in a volume (e.g. the liquid argon), the
probability that an optical photon emitted there is detected by each optical
detector. Building one requires simulating billions of photons with
[remage](https://github.com/legend-exp/remage) and histogramming the result with
[reboost](https://github.com/legend-exp/reboost). _optmapper_ takes a single
configuration file and runs the whole chain on one or many compute nodes.

## Next steps

```{toctree}
:maxdepth: 1

User Manual <manual/index>
```

```{toctree}
:maxdepth: 1

API documentation <api/modules>
```

## See also

- [remage](https://remage.readthedocs.io/en/stable/): Modern _Geant4_
  application for HPGe and LAr experiments,
- [legend-pygeom-optics](https://legend-pygeom-optics.readthedocs.io/en/stable/):
  Package to handle optical properties in python,
- [legend-pygeom-l200](https://github.com/legend-exp/legend-pygeom-l200):
  Implementation of the LEGEND-200 experiment,
- [legend-pygeom-l1000](https://github.com/legend-exp/legend-pygeom-l1000):
  Implementation of the LEGEND-1000 experiment,
- [reboost](https://github.com/legend-exp/reboost/): Base tool to create/consume
  optical maps.
