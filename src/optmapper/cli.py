"""User facing command line interface, schedules the production jobs and exits."""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
from pathlib import Path

from . import _version
from .config import Config, ConfigError
from .log_utils import setup_log
from .schedulers import Job, LocalScheduler, get_scheduler
from .work import node_log_file

log = logging.getLogger(__name__)


def resolve_launcher(cfg: Config) -> list[str]:
    """Command prefix to run the worker in the current software environment.

    Defaults to ``pixi run --as-is`` with the manifest of the environment
    ``optmapper`` is running in, if any.
    """
    if cfg.execution.launcher is not None:
        return list(cfg.execution.launcher)
    manifest = os.environ.get("PIXI_PROJECT_MANIFEST")
    if manifest is None:
        return []
    launcher = ["pixi", "run", "--as-is", "--manifest-path", manifest]
    env_name = os.environ.get("PIXI_ENVIRONMENT_NAME", "default")
    if env_name != "default":
        launcher += ["--environment", env_name]
    return launcher


def submit(cfg: Config, dry_run: bool = False) -> list[str]:
    """Submit the node jobs and, if needed, the final merge job. Return the job IDs."""
    scheduler = get_scheduler(cfg.execution.scheduler, dry_run=dry_run)
    work = [
        *resolve_launcher(cfg),
        "optmapper-work",
        "--verbose",
    ]
    config = ["--config", str(cfg.resolved_config_file)]

    node_options = cfg.execution.slurm.get("node", {})
    n_nodes = cfg.statistics.nodes
    # the local scheduler has no allocation to run several nodes in
    per_job = cfg.statistics.nodes_per_job if cfg.execution.scheduler == "slurm" else 1

    node_ids = []
    for first in range(0, n_nodes, per_job):
        count = min(per_job, n_nodes - first)
        if count == 1:
            job = Job(
                name=f"{cfg.name}-node{first:04d}",
                command=[*work, "node", *config, "--index", str(first)],
                log_file=node_log_file(cfg, first),
                options=node_options,
            )
        else:
            last = first + count - 1
            job = Job(
                name=f"{cfg.name}-nodes{first:04d}-{last:04d}",
                command=[
                    *(*work, "nodes", *config),
                    *("--first-index", str(first), "--count", str(count)),
                ],
                log_file=cfg.log_dir / f"{cfg.name}-nodes{first:04d}-{last:04d}-%j.log",
                options={**node_options, "nodes": count, "ntasks": count},
            )
        node_ids.append(scheduler.submit(job))
        log.info("submitted job %s (%s)", node_ids[-1], job.name)

    ids = list(node_ids)
    if n_nodes > 1:
        job = Job(
            name=f"{cfg.name}-merge",
            command=[*work, "merge", *config],
            log_file=cfg.log_dir / f"{cfg.name}-merge-%j.log",
            options=cfg.execution.slurm.get("merge", {}),
            dependencies=node_ids,
        )
        ids.append(scheduler.submit(job))
        log.info("submitted job %s (%s), after all node jobs", ids[-1], job.name)

    if isinstance(scheduler, LocalScheduler) and scheduler.failed:
        msg = f"failed jobs: {', '.join(scheduler.failed)}"
        raise RuntimeError(msg)
    return ids


def optmapper_cli(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="optmapper",
        description="%(prog)s: produce LEGEND optical maps. Prepares the geometry, "
        "submits the production jobs according to the configuration and exits.",
    )
    parser.add_argument(
        "--version",
        action="version",
        help="""Print %(prog)s version and exit""",
        version=_version.__version__,
    )
    parser.add_argument("config", help="production configuration file (YAML or JSON)")
    parser.add_argument("--verbose", "-v", action="count", default=0, help="increase verbosity")
    parser.add_argument(
        "--dry-run",
        "-n",
        action="store_true",
        help="validate the configuration and show the jobs that would be submitted",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="allow reusing an output directory that already contains a production",
    )
    args = parser.parse_args(argv)
    # report the submitted jobs by default
    setup_log((logging.INFO, logging.DEBUG)[min(args.verbose, 1)])

    try:
        cfg = Config.load(args.config)
        _prepare(cfg, dry_run=args.dry_run, force=args.force)
        submit(cfg, dry_run=args.dry_run)
    except (ConfigError, RuntimeError) as e:
        log.error("%s", e)
        sys.exit(1)

    if not args.dry_run:
        log.info("optical map will be written to %s", cfg.final_map)


def _prepare(cfg: Config, dry_run: bool, force: bool) -> None:
    """Validate the configuration, build the geometry and store the resolved config."""
    from .macro import render_macro  # noqa: PLC0415

    if cfg.resolved_config_file.exists() and not force:
        msg = (
            f"{cfg.output_dir} already contains a production, choose another "
            "output_dir or use --force"
        )
        raise ConfigError(msg)

    # fail early on template or emission spectrum errors
    render_macro(cfg, cfg.statistics.primaries_per_process)

    if shutil.which("remage") is None:
        log.warning("remage not found in the current environment")

    if cfg.geometry.gdml is not None and not Path(cfg.geometry.gdml).is_file():
        msg = f"GDML file {cfg.geometry.gdml} not found"
        raise ConfigError(msg)

    s = cfg.statistics
    log.info(
        "production %s: %d node(s) x %d run(s) x %s processes x %.3g primaries",
        cfg.name,
        s.nodes,
        s.runs_per_node,
        s.processes_per_node or "all CPU",
        s.primaries_per_process,
    )
    if s.nodes_per_job > 1 and cfg.execution.scheduler == "slurm":
        log.info("up to %d nodes per job", s.nodes_per_job)

    if dry_run:
        return

    cfg.log_dir.mkdir(parents=True, exist_ok=True)
    if cfg.geometry.executable is not None:
        from .geometry import build_gdml  # noqa: PLC0415

        build_gdml(cfg)
    cfg.dump(cfg.resolved_config_file)
