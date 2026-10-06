from __future__ import annotations

import logging


def setup_log(verbosity: int = 0) -> None:
    """Log to the terminal, `verbosity` 0, 1 and 2 means warning, info and debug."""
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        level=logging.WARNING,
    )
    level = (logging.WARNING, logging.INFO, logging.DEBUG)[min(verbosity, 2)]
    logging.getLogger("optmapper").setLevel(level)
    # reboost is chatty, only show its info messages from -vv on
    logging.getLogger("reboost").setLevel(logging.INFO if verbosity >= 2 else logging.WARNING)
