from __future__ import annotations

import faulthandler
import logging
import sys
from typing import Any


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def configure_logging(verbosity: int = 0) -> None:
    if verbosity <= 0:
        level = logging.WARNING
    elif verbosity == 1:
        level = logging.INFO
    else:
        level = logging.DEBUG

    logging.basicConfig(
        level=level,
        format="%(levelname)s %(name)s: %(message)s",
    )
    if verbosity > 0:
        try:
            if not faulthandler.is_enabled():
                faulthandler.enable(file=sys.stderr, all_threads=True)
        except Exception:
            pass


def log_event(
    logger: logging.Logger,
    level: int,
    message: str,
    **fields: Any,
) -> None:
    if not logger.isEnabledFor(level):
        return

    if fields:
        field_text = " ".join(
            f"{key}={value}"
            for key, value in fields.items()
            if value is not None
        )
        if field_text:
            logger.log(level, "%s | %s", message, field_text)
            return

    logger.log(level, "%s", message)
