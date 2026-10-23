"""Fase 5: corre el scheduler de polling periódico en primer plano.

Uso:
    python run_scheduler.py
Ctrl+C para detener.
"""

from __future__ import annotations

import logging
import time

from app.config import load_poll_interval_minutes
from app.scheduler import build_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("run_scheduler")


def main() -> int:
    interval_minutes = load_poll_interval_minutes()
    scheduler = build_scheduler(interval_minutes)
    scheduler.start()
    logger.info("Scheduler iniciado: polling cada %d minuto(s). Ctrl+C para detener.", interval_minutes)

    try:
        while True:
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Deteniendo scheduler...")
        scheduler.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
