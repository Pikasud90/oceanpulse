"""Dual rotating logging: everything to one file, warnings and above to another.

Keeping a warnings-only file means dropped records and upstream failures are
greppable without wading through a megabyte of routine poll chatter.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

_CONFIGURED = False

_FORMAT = "%(asctime)s %(levelname)-7s %(name)-28s %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


def setup_logging(log_dir: Path, level: int = logging.INFO, console: bool = True) -> None:
    """Install rotating file handlers plus an optional console handler.

    Safe to call more than once; only the first call takes effect.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(_FORMAT, datefmt=_DATEFMT)

    root = logging.getLogger()
    root.setLevel(level)

    daemon_handler = logging.handlers.RotatingFileHandler(
        log_dir / "daemon.log", maxBytes=8 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    daemon_handler.setLevel(level)
    daemon_handler.setFormatter(formatter)
    root.addHandler(daemon_handler)

    error_handler = logging.handlers.RotatingFileHandler(
        log_dir / "errors.log", maxBytes=4 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    error_handler.setLevel(logging.WARNING)
    error_handler.setFormatter(formatter)
    root.addHandler(error_handler)

    if console:
        stream = logging.StreamHandler(sys.stdout)
        stream.setLevel(level)
        stream.setFormatter(formatter)
        root.addHandler(stream)

    # These are noisy at INFO and say nothing we do not already log ourselves.
    for noisy in ("httpx", "httpcore", "werkzeug", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
