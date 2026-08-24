import logging
from pathlib import Path


def configure_logging(log_file: Path | None = None) -> logging.Logger:
    """Configura un log persistente, una sola vez por proceso."""

    logger = logging.getLogger("desktop_agent")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if logger.handlers:
        return logger

    target = log_file or Path("logs") / "agent.log"
    target.parent.mkdir(parents=True, exist_ok=True)

    handler = logging.FileHandler(target, encoding="utf-8")
    handler.setFormatter(
        logging.Formatter("[%(asctime)s] %(message)s", datefmt="%H:%M:%S")
    )
    logger.addHandler(handler)
    return logger

