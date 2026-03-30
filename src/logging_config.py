"""Logging configuration for property-finder."""

import logging
import os


def setup_logging(level: str | None = None) -> logging.Logger:
    log_level = level or os.environ.get("LOG_LEVEL", "INFO")
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    logger = logging.getLogger("property_finder")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        logger.addHandler(handler)
    logger.setLevel(numeric_level)
    return logger
