import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from desktop_agent.error_history import ErrorHistory, ErrorHistoryHandler, default_error_history_path

def configure_logging(log_file: Path | None = None) -> logging.Logger:
    """Configura un log persistente, una sola vez por proceso."""

    logger = logging.getLogger("desktop_agent")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if logger.handlers:
        return logger

    target = log_file or default_error_history_path().with_name("agent.log")
    target.parent.mkdir(parents=True, exist_ok=True)

    handler = RotatingFileHandler(target, encoding="utf-8", maxBytes=2_000_000, backupCount=2)
    handler.setFormatter(
        logging.Formatter("[%(asctime)s] %(message)s", datefmt="%H:%M:%S")
    )
    logger.addHandler(handler)
    history_path = target.with_name("error_history.sqlite3")
    logger.addHandler(ErrorHistoryHandler(ErrorHistory(history_path)))
    return logger
