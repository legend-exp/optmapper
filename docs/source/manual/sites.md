# Execution sites

A site preset collects the options of a computing facility, merged with the
user's `execution` block. The presets shipped with the package are in
[`src/optmapper/sites/`](https://github.com/legend-exp/optmapper/tree/main/src/optmapper/sites):

- `nersc`: Perlmutter CPU nodes with the LEGEND allocation. Node jobs take a
  full node, the merge job runs in the `shared` QOS. Scratch is on `$PSCRATCH`.
- `slurm`: a generic Slurm cluster, one exclusive node per job
- `local`: run all jobs one after the other on the current machine, in the
  foreground. For tests and small productions.

To port the workflow to another Slurm facility, write a preset file like
[`nersc.yaml`](https://github.com/legend-exp/optmapper/blob/main/src/optmapper/sites/nersc.yaml)
and pass its path:

```yaml
execution:
  site: /path/to/mysite.yaml
```

Other batch systems need a new subclass of
{class}`optmapper.schedulers.Scheduler`, which submits a command with options
and dependencies and returns a job ID.
