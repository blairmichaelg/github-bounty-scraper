"""
Structured logging configuration for the bounty scraper.
"""

import logging
import sys


def setup_logging(verbose: bool = False) -> logging.Logger:
    """Configure and return the package-level logger.

    Parameters
    ----------
    verbose : bool
        When *True* the log level is set to DEBUG, otherwise INFO.
    """
    logger = logging.getLogger("bounty_scraper")
    if logger.handlers:
        # Already configured (e.g. re-import guard).
        return logger

    level = logging.DEBUG if verbose else logging.INFO
    logger.setLevel(level)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    fmt = logging.Formatter("[%(levelname)s %(asctime)s] %(message)s", datefmt="%H:%M:%S")
    handler.setFormatter(fmt)
    logger.addHandler(handler)

    return logger


def get_logger() -> logging.Logger:
    """Return the package logger (assumes ``setup_logging`` was called)."""
    return logging.getLogger("bounty_scraper")
