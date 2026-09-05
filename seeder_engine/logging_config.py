"""Shared logging configuration for the CLI, engine, and web application."""

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path


DEFAULT_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_logging(log_dir: str | Path = "logs") -> logging.Logger:
    root_logger = logging.getLogger("seeder")
    if root_logger.handlers:
        return root_logger

    level_name = os.getenv("SEEDER_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    root_logger.setLevel(level)
    root_logger.propagate = False

    formatter = logging.Formatter(os.getenv("SEEDER_LOG_FORMAT", DEFAULT_LOG_FORMAT))
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    log_path = Path(os.getenv("SEEDER_LOG_FILE", str(Path(log_dir) / "seeder.log")))
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        log_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)
    return root_logger


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"seeder.{name}")