from __future__ import annotations

import logging

import colorlog


def setup_log(level: int | None = None, multiproc: bool = False) -> None:
    """Setup a colored logger for this package.

    Parameters
    ----------
    level
        initial log level, or ``None`` to use the default.
    multiproc
        set to ``True`` to include process ID in log output (i.e. for multiprocessing setups)
    """
    fmt = "%(log_color)s%(name)s [%(levelname)s]"
    if multiproc:
        fmt += " (pid=%(process)s)"
    fmt += " %(message)s"

    handler = colorlog.StreamHandler()
    handler.setFormatter(colorlog.ColoredFormatter(fmt))

    logger = logging.getLogger("optmapper")
    logger.addHandler(handler)
    if level is not None:
        logger.setLevel(level)
