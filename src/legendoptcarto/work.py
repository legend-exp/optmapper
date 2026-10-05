"""Worker process, runs on the compute nodes."""

from __future__ import annotations

import argparse
import logging
import os
import re
import resource
import shlex
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

from .config import Config
from .log_utils import setup_log

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
    from reboost.optmap.create import merge_optical_maps  # noqa: PLC0415

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


def _create_map(cfg: Config, inputs: list[Path], output: Path) -> None:
    """Create a map in a separate process, as reboost spawns its own worker pool."""
    cmd = [
        sys.executable,
        "-m",
        "legendoptcarto.work",
        "create",
        "--config",
        str(cfg.resolved_config_file),
        "--",
        *map(str, inputs),
        str(output),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if res.returncode != 0:
        msg = f"map creation failed for {output.name}:\n{res.stdout}{res.stderr}"
        raise RuntimeError(msg)
    log.info("created %s from %d stp files", output.name, len(inputs))


def run_node(cfg: Config, index: int) -> None:
    """Simulate photons on this node and build the node optical map."""
    from .macro import render_macro  # noqa: PLC0415

    job_id = os.environ.get("SLURM_JOB_ID", str(os.getpid()))
    scratch_base = os.path.expandvars(
        cfg.execution.scratch_dir or str(Path(cfg.output_dir) / "scratch")
    )
    scratch = Path(scratch_base) / cfg.name / f"node{index:04d}-{job_id}"
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

    for run in range(cfg.statistics.runs_per_node):
        stp_dir = scratch / "stp" / f"run{run:03d}"
        stp_dir.mkdir(parents=True)

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

        chunks = [
            stp_files[i : i + proc.stp_files_per_map]
            for i in range(0, len(stp_files), proc.stp_files_per_map)
        ]
        with (
            _step(f"run {run}: creating {len(chunks)} map(s) from {len(stp_files)} stp files"),
            ThreadPoolExecutor(proc.parallel_maps) as pool,
        ):
            futures = [
                pool.submit(_create_map, cfg, chunk, map_dir / f"run{run:03d}-{k:04d}.lh5")
                for k, chunk in enumerate(chunks)
            ]
            for f in futures:
                f.result()

        if proc.keep_stp:
            dest = Path(cfg.output_dir) / "stp" / f"node{index:04d}"
            with _step(f"run {run}: copying stp files to {dest}"):
                dest.mkdir(parents=True, exist_ok=True)
                for f in stp_files:
                    shutil.copy2(f, dest)
        shutil.rmtree(stp_dir)

    output = cfg.node_map(index)
    with _step(f"merging intermediate maps into {output}"):
        merge_maps(cfg, sorted(map_dir.glob("*.lh5"), key=_natural_key), output, scratch)

    shutil.rmtree(scratch)
    log.info(
        "node %d done, max RSS of child processes: %.1f GiB",
        index,
        resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024**2,
    )


def run_merge(cfg: Config) -> None:
    """Merge the node maps into the final map, tolerating failed nodes."""
    nodes = [cfg.node_map(i) for i in range(cfg.statistics.nodes)]
    found = [p for p in nodes if p.is_file()]
    missing = [p for p in nodes if not p.is_file()]
    if missing:
        log.warning(
            "%d of %d node maps are missing (failed jobs?), merging the remaining ones: %s",
            len(missing),
            len(nodes),
            ", ".join(p.name for p in missing),
        )
    if not found:
        msg = "no node map found, nothing to merge"
        raise RuntimeError(msg)

    with _step(f"merging {len(found)} node maps into {cfg.final_map}"):
        merge_maps(cfg, found, cfg.final_map, cfg.final_map.parent)


def work_cli(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="legend-optcarto-work",
        description="%(prog)s: optical map production worker, usually started by legend-optcarto",
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

    merge_parser = subparsers.add_parser("merge", help="merge the node maps into the final map")
    _add_config(merge_parser)

    create_parser = subparsers.add_parser("create", help="create a map from stp files (internal)")
    _add_config(create_parser)
    create_parser.add_argument("input", nargs="+", help="input stp files")
    create_parser.add_argument("output", help="output map file")

    args = parser.parse_args(argv)
    setup_log(args.verbose)

    cfg = Config.load(args.config)
    os.environ.update(cfg.execution.env)

    if args.command == "node":
        run_node(cfg, args.index)
    elif args.command == "merge":
        run_merge(cfg)
    elif args.command == "create":
        from reboost.optmap.create import create_optical_maps  # noqa: PLC0415

        create_optical_maps(
            args.input,
            cfg.optmap.reboost_settings(),
            cfg.processing.bufsize,
            chfilter="*" if cfg.optmap.detectors is None else tuple(cfg.optmap.detectors),
            output_lh5_fn=args.output,
            n_procs=cfg.processing.procs_per_map,
            geom_fn=str(cfg.gdml_file),
        )


if __name__ == "__main__":
    work_cli()
