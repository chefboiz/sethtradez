"""
Structured logger with custom SIGNAL and TRADE levels.
Format: [2026-05-03 14:23:11] [LEVEL] message
Writes to stdout always; optionally to logs/sethtradez.log.
"""

import logging
import os
import sys
from datetime import datetime, timezone

SIGNAL_LEVEL = 25
TRADE_LEVEL = 26
logging.addLevelName(SIGNAL_LEVEL, "SIGNAL")
logging.addLevelName(TRADE_LEVEL, "TRADE")


class _SethFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        return f"[{ts}] [{record.levelname}] {record.getMessage()}"


def _build_logger() -> logging.Logger:
    log = logging.getLogger("sethtradez")
    log.setLevel(logging.DEBUG)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_SethFormatter())
    log.addHandler(handler)

    log_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
    os.makedirs(log_dir, exist_ok=True)
    file_handler = logging.FileHandler(os.path.join(log_dir, "sethtradez.log"))
    file_handler.setFormatter(_SethFormatter())
    log.addHandler(file_handler)

    return log


logger = _build_logger()


def signal(msg: str) -> None:
    logger.log(SIGNAL_LEVEL, msg)


def trade(msg: str) -> None:
    logger.log(TRADE_LEVEL, msg)


def info(msg: str) -> None:
    logger.info(msg)


def debug(msg: str) -> None:
    logger.debug(msg)


def warning(msg: str) -> None:
    logger.warning(msg)


def error(msg: str) -> None:
    logger.error(msg)
