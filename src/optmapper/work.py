"""Worker process, runs on the compute nodes."""

from __future__ import annotations

import argparse
import logging
import os
import re
import resource
import shlex
import shutil
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import dbetto

from .config import Config
from .log_utils import setup_log
from .schedulers import slurm_options

log = logging.getLogger(__name__)


@contextmanager
def _step(what: str):
    log.info("%s...", what)
    start = time.monotonic()
    yield
    log.info("%s: done in %.0f s", what, time.monotonic() - start)


def _natural_key(path: Path) -> list:
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", path.name)]


def _available_cpus() -> int:
    if "SLURM_CPUS_ON_NODE" in os.environ:
        return int(os.environ["SLURM_CPUS_ON_NODE"])
    return len(os.sched_getaffinity(0))


def merge_maps(cfg: Config, inputs: list[Path], output: Path, workdir: Path) -> None:
    """Merge optical maps into `output`, temporary files are written to `workdir`."""
    from reboost.log_utils import setup_log as reboost_setup_log  # noqa: PLC0415
    from reboost.optmap.create import merge_optical_maps  # noqa: PLC0415

    # the merge runs in this process, so log like ``reboost-optmap --verbose`` would
    if not logging.getLogger("reboost").handlers:
        reboost_setup_log(logging.INFO)

    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = workdir / f".{output.name}.part"
    tmp.unlink(missing_ok=True)
    if len(inputs) == 1:
        shutil.copyfile(inputs[0], tmp)
    else:
        merge_optical_maps(
            [str(p) for p in inputs],
            str(tmp),
            cfg.optmap.reboost_settings(),
            n_procs=cfg.processing.merge_procs,
        )
    shutil.move(tmp, output)


def _reboost_create_args(cfg: Config, workdir: Path) -> list[str]:
    """Options of ``reboost-optmap create``, its input files are written to `workdir`."""
    settings = workdir / "optmap-settings.json"
    dbetto.utils.write_dict(cfg.optmap.reboost_settings(), str(settings))
    args = [
        # log the progress, to see how far it got if it is killed
        *("--verbose", "--bufsize", str(cfg.processing.bufsize), "create"),
        *("--settings", str(settings), "--geom", str(cfg.gdml_file)),
        *("--n-procs", str(cfg.processing.procs_per_map)),
    ]
    if cfg.optmap.detectors is not None:
        detectors = workdir / "optmap-detectors.json"
        dbetto.utils.write_dict(cfg.optmap.detectors, str(detectors))
        args += ["--detectors", str(detectors)]
    return args


def _exit_status(returncode: int) -> str:
    """Describe the exit code of a `subprocess`, which is negative if killed by a signal."""
    if returncode >= 0:
        return f"exit code {returncode}"
    try:
        name = signal.Signals(-returncode).name
    except ValueError:
        name = f"signal {-returncode}"
    msg = f"killed by {name}"
    if -returncode == signal.SIGKILL:
        msg += " (out of memory?)"
    return msg


def _create_map(create_args: list[str], inputs: list[Path], output: Path) -> None:
    """Create a map with ``reboost-optmap``, in a separate process as it spawns its own pool.

    The output is forwarded line by line to the log as it is written, so that it is not lost
    if a process is killed (e.g. out of memory) before the map creation finishes.
    """
    cmd = ["reboost-optmap", *create_args, "--", *map(str, inputs), str(output)]
    # by default, Python block-buffers output to a pipe, which would be lost on SIGKILL
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    with subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env
    ) as proc:
        for line in proc.stdout:  # type: ignore[union-attr]
            log.info("%s: %s", output.stem, line.rstrip())
    if proc.returncode != 0:
        msg = (
            f"map creation failed for {output.name}: {_exit_status(proc.returncode)}, "
            "see its output above"
        )
        raise RuntimeError(msg)
    log.info("created %s from %d stp files", output.name, len(inputs))


def run_node(cfg: Config, index: int) -> None:
    """Simulate photons on this node and build the node optical map."""
    from .macro import render_macro  # noqa: PLC0415

    job_id = os.environ.get("SLURM_JOB_ID", str(os.getpid()))
    scratch = cfg.scratch_dir / f"node{index:04d}-{job_id}"
    # a requeued Slurm job keeps its ID, do not pick up files from the previous attempt
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True)
    os.environ.setdefault("NUMBA_CACHE_DIR", str(scratch / ".numba-cache"))

    nprocs = cfg.statistics.processes_per_node or _available_cpus()
    n_primaries = cfg.statistics.primaries_per_process
    log.info(
        "node %d (job %s): %d run(s) x %d remage processes x %.3g primaries, scratch in %s",
        index,
        job_id,
        cfg.statistics.runs_per_node,
        nprocs,
        n_primaries,
        scratch,
    )

    macro = scratch / f"{cfg.name}.mac"
    macro.write_text(render_macro(cfg, n_primaries))
    if index == 0:
        shutil.copyfile(macro, Path(cfg.output_dir) / macro.name)

    proc = cfg.processing
    map_dir = scratch / "maps"
    map_dir.mkdir()
    create_args = _reboost_create_args(cfg, scratch)

    stp_dir = scratch / "stp"
    stp_dir.mkdir()
    for run in range(cfg.statistics.runs_per_node):
        cmd = [
            "remage",
            *cfg.advanced.remage_args,
            "--gdml-files",
            str(cfg.gdml_file),
            "--output-file",
            str(stp_dir / f"{cfg.name}-node{index:04d}-run{run:03d}-tier_stp.lh5"),
            "--procs",
            str(nprocs),
            "--flat-output",
            "--",
            str(macro),
        ]
        with _step(f"run {run}: remage simulation"):
            log.debug("running: %s", shlex.join(cmd))
            subprocess.run(cmd, check=True)

    stp_files = sorted(stp_dir.glob("*.lh5"), key=_natural_key)
    if not stp_files:
        msg = f"remage did not produce any output file in {stp_dir}"
        raise RuntimeError(msg)

    # split the stp files into parallel_maps contiguous chunks of about equal size
    n_maps = min(proc.parallel_maps, len(stp_files))
    chunks = [
        stp_files[k * len(stp_files) // n_maps : (k + 1) * len(stp_files) // n_maps]
        for k in range(n_maps)
    ]
    with (
        _step(f"creating {len(chunks)} map(s) from {len(stp_files)} stp files"),
        ThreadPoolExecutor(n_maps) as pool,
    ):
        futures = [
            pool.submit(_create_map, create_args, chunk, map_dir / f"{k:04d}.lh5")
            for k, chunk in enumerate(chunks)
        ]
        for f in futures:
            f.result()

    dest = cfg.stp_dir(index)
    if dest is not None:
        # moving is cheap if the destination is on the scratch file system
        with _step(f"moving stp files to {dest}"):
            dest.mkdir(parents=True, exist_ok=True)
            for stp_file in stp_files:
                shutil.move(stp_file, dest / stp_file.name)

    output = cfg.node_map(index)
    with _step(f"merging intermediate maps into {output}"):
        merge_maps(cfg, sorted(map_dir.glob("*.lh5"), key=_natural_key), output, scratch)

    shutil.rmtree(scratch)
    log.info(
        "node %d done, max RSS of child processes: %.1f GiB",
        index,
        resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024**2,
    )


def node_log_file(cfg: Config, index: int) -> Path:
    """Log file of a node, ``%j`` is replaced by the job ID."""
    return cfg.log_dir / f"{cfg.name}-node{index:04d}-%j.log"


def srun_command(cfg: Config, index: int, config_file: str) -> list[str]:
    """``srun`` command running node `index` as a job step on one node of the allocation."""
    options = {
        "nodes": 1,
        "ntasks": 1,
        # srun does not inherit --cpus-per-task from sbatch, give the step the whole node
        "cpus-per-task": os.environ["SLURM_CPUS_ON_NODE"],
        "job-name": f"{cfg.name}-node{index:04d}",
        "output": node_log_file(cfg, index),
        "error": node_log_file(cfg, index),
        **cfg.execution.slurm.get("srun", {}),
    }
    return [
        "srun",
        *slurm_options(options),
        # the environment is inherited by the step, no need for the launcher
        *(sys.executable, "-m", "optmapper.work", "--verbose", "node"),
        *("--config", config_file, "--index", str(index)),
    ]


def run_nodes(cfg: Config, first: int, count: int, config_file: str) -> None:
    """Run nodes `first` to `first + count - 1` in parallel, one ``srun`` step per node.

    All steps are waited for, failed nodes do not stop the other ones.
    """
    n_alloc = os.environ.get("SLURM_JOB_NUM_NODES")
    if n_alloc is None:
        msg = "running several nodes requires a Slurm allocation (with srun)"
        raise RuntimeError(msg)
    if int(n_alloc) != count:
        log.warning("allocation has %s nodes, running %d node steps", n_alloc, count)

    steps = {}
    for index in range(first, first + count):
        cmd = srun_command(cfg, index, config_file)
        log.debug("running: %s", shlex.join(cmd))
        steps[index] = subprocess.Popen(cmd)
    log.info("started %d node steps, logs in %s", count, cfg.log_dir)

    failed = []
    for index, step in steps.items():
        if step.wait() != 0:
            log.error("node %d failed with exit code %d", index, step.returncode)
            failed.append(index)
        else:
            log.info("node %d done", index)
    if failed:
        msg = f"{len(failed)} of {count} nodes failed: {', '.join(map(str, failed))}"
        raise RuntimeError(msg)


def run_merge(cfg: Config) -> None:
    """Merge the node maps into the final map, tolerating failed nodes."""
    nodes = [cfg.node_map(i) for i in range(cfg.statistics.nodes)]
    found = [p for p in nodes if p.is_file()]
    missing = [p for p in nodes if not p.is_file()]
    if not found:
        msg = f"all node jobs failed, no node map to merge: see the node job logs in {cfg.log_dir}"
        raise RuntimeError(msg)
    if missing:
        log.warning(
            "%d of %d node maps are missing (failed jobs?), see the logs in %s: %s",
            len(missing),
            len(nodes),
            cfg.log_dir,
            ", ".join(p.name for p in missing),
        )

    with _step(f"merging {len(found)} node maps into {cfg.final_map}"):
        merge_maps(cfg, found, cfg.final_map, cfg.final_map.parent)


def work_cli(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="optmapper-work",
        description="%(prog)s: optical map production worker, usually started by optmapper",
    )
    parser.add_argument("--verbose", "-v", action="count", default=0, help="increase verbosity")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def _add_config(p):
        p.add_argument(
            "--config", required=True, help="resolved production configuration file (JSON)"
        )

    node_parser = subparsers.add_parser("node", help="run the full production on one node")
    _add_config(node_parser)
    node_parser.add_argument("--index", type=int, required=True, help="node index")

    nodes_parser = subparsers.add_parser(
        "nodes", help="run several nodes in parallel in a multi-node Slurm allocation"
    )
    _add_config(nodes_parser)
    nodes_parser.add_argument("--first-index", type=int, required=True, help="first node index")
    nodes_parser.add_argument("--count", type=int, required=True, help="number of nodes")

    merge_parser = subparsers.add_parser("merge", help="merge the node maps into the final map")
    _add_config(merge_parser)

    args = parser.parse_args(argv)
    setup_log((None, logging.INFO, logging.DEBUG)[min(args.verbose, 2)])

    cfg = Config.load(args.config)
    os.environ.update(cfg.execution.env)

    if args.command == "node":
        run_node(cfg, args.index)
    elif args.command == "nodes":
        try:
            run_nodes(cfg, args.first_index, args.count, args.config)
        except RuntimeError as e:
            log.error("%s", e)
            sys.exit(1)
    elif args.command == "merge":
        run_merge(cfg)


if __name__ == "__main__":
    work_cli()
