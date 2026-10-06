# optmapper

Production of LEGEND optical maps on batch systems.

An optical map gives, for each position in a volume (e.g. the liquid argon), the
probability that an optical photon emitted there is detected by each optical
detector. Building one requires simulating billions of photons with
[remage](https://github.com/legend-exp/remage) and histogramming the result with
[reboost](https://github.com/legend-exp/reboost). `optmapper` takes a single
configuration file and runs the whole chain on one or many compute nodes.

## Overview

The package provides two programs:

- `optmapper`, the user facing program. It validates the configuration, builds
  the GDML geometry, submits the jobs and exits: it does not stay in the
  foreground and does not run as a daemon.
- `optmapper-work`, the worker program, started by the jobs on the compute
  nodes. Users do not normally call it.

Each node job:

1. composes the remage macro from a template and the configuration
2. runs remage with one process per CPU, `runs_per_node` times
3. after each remage run, creates intermediate maps from chunks of
   `stp_files_per_map` remage output files, `parallel_maps` at a time, then
   deletes the remage output files from the scratch area
4. merges all its intermediate maps into one node map

With more than one node, `optmapper` also submits a merge job, which depends on
all node jobs (`--dependency=afterany:...`) and merges the node maps into the
final map. If some node jobs fail, the merge job merges the node maps that exist
and prints a warning. With a single node, the node job writes the final map
directly.

On Slurm, jobs are submitted with `sbatch --wrap`: no job script is written to
disk.

## Installation

It is the user's responsibility to set up the software environment with
[pixi](https://pixi.sh). Create a production directory with a `pixi.toml` that
pins the software versions (see [`examples/l200/pixi.toml`](examples/l200)):

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

```console
> pixi install --frozen
```

## Usage

```console
> pixi run optmapper optmap.yaml
```

Options:

- `--dry-run`/`-n`: validate the configuration and print the `sbatch` commands
  without building the geometry or submitting anything
- `--force`: reuse an output directory that already contains a production
- `--verbose`/`-v`: more output

The jobs run the worker with `pixi run --as-is` in the same pixi environment
`optmapper` was started from. Set `execution.launcher` to change that.

The output directory contains:

| path                     | content                                              |
| ------------------------ | ---------------------------------------------------- |
| `<name>.lh5`             | the final optical map                                |
| `<name>.mac`             | the remage macro (written by the first node job)     |
| `optmapper-config.json`  | the resolved configuration, read by the workers      |
| `geom.gdml`              | the geometry (if built by `optmapper`)               |
| `geom-config.json`       | the geometry configuration passed to the generator   |
| `logs/`                  | geometry and job logs                                |
| `nodes/<name>-nodeN.lh5` | the node maps (only with more than one node)         |
| `stp/nodeN/`             | the remage output files (only with `keep_stp: true`) |

## Configuration

The configuration is a YAML or JSON file. Relative paths are relative to the
directory of the configuration file, and `$_` in any value is replaced by that
directory. A complete example, equivalent to the former `workflow.sb` but on 4
nodes, is in
[`examples/l200/optmap-l200cfg01.yaml`](examples/l200/optmap-l200cfg01.yaml):

```yaml
name: l200cfg01-lar-vuv
output_dir: optmap-l200cfg01

geometry:
  executable: legend-pygeom-l200
  config: geom-config.yaml
  env:
    LEGEND_METADATA: $_/legend-metadata

emission:
  gaussian: { mean: 9.68 eV, sigma: 0.22 eV }

statistics:
  nodes: 4
  runs_per_node: 5
  primaries_per_process: 8_000_000

optmap:
  volume: liquid_argon
  shape: cylinder
  range_in_m: [[-2, 2], [-2, 2], [-3, 3]]
  bins: [80, 80, 120]

execution:
  site: nersc
  slurm:
    node:
      mail-type: TIME_LIMIT,FAIL
      mail-user: someone@example.com
```

### `name`, `output_dir`

`name` is the production name, used for the output and job names. `output_dir`
is the output directory, on persistent storage.

### `geometry`

Official maps use a LEGEND geometry generator, like
[legend-simflow](https://github.com/legend-exp/legend-simflow) does:

- `executable`: the generator, e.g. `legend-pygeom-l200` or
  `legend-pygeom-l1000`
- `config` (optional): geometry configuration, a file name or an inline mapping.
  In a file, `$_` is replaced by the directory of that file.
- `optics_plugin` (optional): Python file passed to the generator's
  `--pygeom-optics-plugin` option, to change optical properties. It is also
  applied when generating the `emission.spectrum`.
- `env` (optional): environment variables for the generator, e.g.
  `LEGEND_METADATA` (legend-pygeom-l200) or `LEGEND1000_METADATA`
  (legend-pygeom-l1000). Environment variables in the values are expanded.

`optmapper` runs the generator once, before submitting the jobs.

Unofficial maps can use a GDML file instead:

```yaml
geometry:
  gdml: my-geometry.gdml
```

### `emission`

The energy spectrum of the optical photons, one of:

- `spectrum`: the dotted path of a function with signature
  `f(filename, output_macro)` that writes a G4GeneralParticleSource spectrum,
  like `pygeomoptics.lar.g4gps_lar_emissions_spectrum`,
  `pygeomoptics.pen.g4gps_pen_emissions_spectrum` or
  `pygeomoptics.fibers.g4gps_fiber_emissions_spectrum`
- `gaussian`: `mean` and `sigma`, as energies (`9.68 eV`) or, for the mean, as a
  wavelength (`128 nm`). Plain numbers are in eV.

### `statistics`

| key                     | default   | meaning                                       |
| ----------------------- | --------- | --------------------------------------------- |
| `nodes`                 | 1         | number of node jobs                           |
| `runs_per_node`         | 1         | remage runs per node job, one after the other |
| `primaries_per_process` | 1 000 000 | photons simulated by each remage process      |
| `processes_per_node`    | all CPUs  | remage processes per run (`remage --procs`)   |

The total number of photons is the product of the four values.

### `optmap`

| key                 | default        | meaning                                      |
| ------------------- | -------------- | -------------------------------------------- |
| `range_in_m`        | required       | `[[xmin, xmax], [ymin, ymax], [zmin, zmax]]` |
| `bins`              | required       | number of bins along x, y, z                 |
| `volume`            | `liquid_argon` | physical volume(s) to generate photons in    |
| `shape`             | `cylinder`     | `cylinder`, `box` or `none`                  |
| `inner_radius_in_m` | 0              | inner radius of the cylinder                 |
| `detectors`         | all            | detectors to make individual maps for        |

The map bounds also set the primary vertex confinement in the macro: photons are
generated in the intersection of `volume` with a box filling the map bounds
(`shape: box`) or with the cylinder inscribed in them (`shape: cylinder`,
requires equal x and y ranges). With `shape: none`, photons are generated in the
whole `volume`.

### `processing`

| key                 | default | meaning                                           |
| ------------------- | ------- | ------------------------------------------------- |
| `stp_files_per_map` | 32      | remage output files per intermediate map          |
| `parallel_maps`     | 8       | intermediate maps created at the same time        |
| `procs_per_map`     | 16      | processes used to create each intermediate map    |
| `merge_procs`       | 8       | processes used to merge maps                      |
| `bufsize`           | 50 000  | rows read at a time from the remage output files  |
| `keep_stp`          | false   | copy the remage output files to `output_dir/stp/` |

### `advanced`

- `pre_init_commands`: macro commands inserted before `/run/initialize`
- `commands`: macro commands inserted before `/run/beamOn`
- `template`: a custom macro template, see the
  [default template](src/optmapper/templates/optmap.mac) for the available
  placeholders
- `remage_args`: extra remage command line options, default
  `["--ignore-warnings"]`

### `execution`

| key           | meaning                                                            |
| ------------- | ------------------------------------------------------------------ |
| `site`        | site preset to start from (default `local`)                        |
| `scheduler`   | `slurm` or `local`                                                 |
| `scratch_dir` | fast temporary storage, default `output_dir/scratch`               |
| `launcher`    | command prefix to start the worker, default `pixi run --as-is ...` |
| `env`         | environment variables set in the worker                            |
| `slurm.node`  | `sbatch` options for the node jobs                                 |
| `slurm.merge` | `sbatch` options for the merge job                                 |

Environment variables in `scratch_dir` (like `$PSCRATCH`) are expanded on the
compute node. `sbatch` options are given without the leading `--`: `key: value`
becomes `--key=value`, `key: true` becomes `--key`, and `key: null` removes an
option set by the site preset.

## Execution sites

A site preset collects the options of a computing facility, merged with the
user's `execution` block. The presets shipped with the package are in
[`src/optmapper/sites/`](src/optmapper/sites):

- `nersc`: Perlmutter CPU nodes with the LEGEND allocation. Node jobs take a
  full node, the merge job runs in the `shared` QOS. Scratch is on `$PSCRATCH`.
- `slurm`: a generic Slurm cluster, one exclusive node per job
- `local`: run all jobs one after the other on the current machine, in the
  foreground. For tests and small productions.

To port the workflow to another Slurm facility, write a preset file like
[`nersc.yaml`](src/optmapper/sites/nersc.yaml) and pass its path:

```yaml
execution:
  site: /path/to/mysite.yaml
```

Other batch systems need a new `Scheduler` class in
[`schedulers.py`](src/optmapper/schedulers.py), which submits a command with
options and dependencies and returns a job ID.
