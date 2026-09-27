import logging
import sys

from .config import LOG_DIR


def get_logger(name: str = "tlc_pipeline") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
    for handler in (logging.StreamHandler(sys.stdout),
                    logging.FileHandler(LOG_DIR / "pipeline.log", encoding="utf-8")):
        handler.setFormatter(fmt)
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger
