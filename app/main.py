"""Container entry point."""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading

from . import __version__
from .config import Config, ConfigError
from .service import Service
from .web import DashboardServer, serve_in_thread


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("dvd")
    try:
        config = Config.from_env()
    except ConfigError as exc:
        log.error("Configuratiefout: %s", exc)
        return 2
    if os.geteuid() != 0:
        log.error("De container moet als root starten om de schijf aan te koppelen")
        return 2
    service = Service(config)
    try:
        server = DashboardServer(config.web_port, service)
    except OSError as exc:
        log.error("Kan de webinterface niet starten op poort %d: %s", config.web_port, exc)
        return 2
    http = serve_in_thread(server)
    stopped = threading.Event()

    def handle_signal(signum: int, _frame: object) -> None:
        log.info("Signaal %s ontvangen; schijf netjes loskoppelen", signum)
        stopped.set()
        service.stop.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    service.start()
    log.info(
        "DVD Bridge %s klaar op poort %d; speler %s",
        __version__,
        config.web_port,
        config.device,
    )
    log.info("NAS-map: %s; kopieermodus: %s", config.mountpoint, config.copy_files)
    try:
        while not stopped.wait(0.5):
            if not service.worker_alive():
                log.error("De schijfbewaker is onverwacht gestopt")
                return 1
    finally:
        service.shutdown()
        server.shutdown()
        server.server_close()
        http.join(timeout=5)
    return 0


if __name__ == "__main__":
    sys.exit(main())
