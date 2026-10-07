# Usage

```shell
$ pixi run optmapper optmap.yaml
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
