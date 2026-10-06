"""Job submission backends."""

from __future__ import annotations

import logging
import shlex
import subprocess
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class Job:
    name: str
    command: list[str]
    log_file: Path
    """Log file of the job, ``%j`` is replaced by the job ID."""
    options: Mapping = field(default_factory=dict)
    """Backend specific options, e.g. ``sbatch`` options for Slurm."""
    dependencies: Sequence[str] = ()
    """IDs of jobs that must have terminated (successfully or not) before this one starts."""


class Scheduler(ABC):
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run

    @abstractmethod
    def submit(self, job: Job) -> str:
        """Submit `job` and return its ID."""


class SlurmScheduler(Scheduler):
    """Submit jobs with ``sbatch --wrap``, no job script files are written."""

    @staticmethod
    def sbatch_command(job: Job) -> list[str]:
        cmd = [
            "sbatch",
            "--parsable",
            f"--job-name={job.name}",
            f"--output={job.log_file}",
            f"--error={job.log_file}",
        ]
        for k, v in job.options.items():
            if v is True:
                cmd.append(f"--{k}")
            elif v is not None and v is not False:
                cmd.append(f"--{k}={v}")
        if job.dependencies:
            cmd.append(f"--dependency=afterany:{':'.join(job.dependencies)}")
        cmd += ["--wrap", shlex.join(job.command)]
        return cmd

    def submit(self, job: Job) -> str:
        cmd = self.sbatch_command(job)
        if self.dry_run:
            log.info("would run: %s", shlex.join(cmd))
            return f"<{job.name}>"

        log.debug("running: %s", shlex.join(cmd))
        try:
            res = subprocess.run(cmd, check=True, capture_output=True, text=True)
        except FileNotFoundError as e:
            msg = "sbatch not found, is this a Slurm cluster?"
            raise RuntimeError(msg) from e
        except subprocess.CalledProcessError as e:
            msg = f"sbatch failed for job {job.name}: {e.stderr.strip()}"
            raise RuntimeError(msg) from e
        # --parsable prints "jobid[;cluster]"
        return res.stdout.strip().split(";")[0]


class LocalScheduler(Scheduler):
    """Run jobs one after the other in the foreground, on the current machine.

    Jobs are executed at submission time, so dependencies are always satisfied.
    Like for ``afterany`` in Slurm, a job runs even if its dependencies failed.
    """

    def __init__(self, dry_run: bool = False):
        super().__init__(dry_run)
        self.failed: list[str] = []
        self._count = 0

    def submit(self, job: Job) -> str:
        self._count += 1
        job_id = str(self._count)
        log_file = Path(str(job.log_file).replace("%j", job_id))
        if self.dry_run:
            log.info("would run: %s > %s", shlex.join(job.command), log_file)
            return job_id

        log.info("running job %s, log in %s", job.name, log_file)
        with log_file.open("w") as f:
            ret = subprocess.run(job.command, stdout=f, stderr=subprocess.STDOUT, check=False)
        if ret.returncode != 0:
            log.error("job %s failed with exit code %d", job.name, ret.returncode)
            self.failed.append(job.name)
        return job_id


def get_scheduler(name: str, dry_run: bool = False) -> Scheduler:
    return {"slurm": SlurmScheduler, "local": LocalScheduler}[name](dry_run=dry_run)
