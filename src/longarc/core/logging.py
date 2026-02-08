"""Logging setup for LongArc."""

from __future__ import annotations

import logging


def configure_logging(level: str = "INFO") -> None:
    """Configure a simple structured-like logger format."""
    logging.basicConfig(
        level=level.upper(),
        format=(
            '{"ts":"%(asctime)s","level":"%(levelname)s",'
            '"logger":"%(name)s","message":"%(message)s"}'
        ),
    )
