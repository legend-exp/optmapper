# Architecture

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
