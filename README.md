# optmapper

![GitHub tag (latest by date)](https://img.shields.io/github/v/tag/legend-exp/optmapper?logo=git)
[![GitHub Workflow Status](https://img.shields.io/github/checks-status/legend-exp/optmapper/main?label=main%20branch&logo=github)](https://github.com/legend-exp/optmapper/actions)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit&logoColor=white)](https://github.com/pre-commit/pre-commit)
[![Codecov](https://img.shields.io/codecov/c/github/legend-exp/optmapper?logo=codecov)](https://app.codecov.io/gh/legend-exp/optmapper)
![GitHub issues](https://img.shields.io/github/issues/legend-exp/optmapper?logo=github)
![GitHub pull requests](https://img.shields.io/github/issues-pr/legend-exp/optmapper?logo=github)
![License](https://img.shields.io/github/license/legend-exp/optmapper)
[![Read the Docs](https://img.shields.io/readthedocs/optmapper?logo=readthedocs)](https://optmapper.readthedocs.io)

_optmapper_ produces LEGEND optical maps on batch systems. An optical map gives,
for each position in a volume (e.g. the liquid argon), the probability that an
optical photon emitted there is detected by each optical detector. Building one
requires simulating billions of photons with
[remage](https://remage.readthedocs.io/en/stable/) and histogramming the result
with [reboost](https://reboost.readthedocs.io/en/stable/). _optmapper_ takes a
single configuration file and runs the whole chain on one or many compute nodes.

For more information see our dedicated
[documentation](https://optmapper.readthedocs.io/en/stable/)!
