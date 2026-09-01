"""CLI entrypoint for the Adaptive L7 Load Balancer."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

from app.config import load_config
from app.logging.structured import setup_logging
from app.server import LoadBalancerApp

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Adaptive L7 HTTP Load Balancer",
    )
    p.add_argument(
        "-c",
        "--config",
        default="config/config.yaml",
        help="Path to YAML configuration file",
    )
    p.add_argument(
        "--host",
        default=None,
        help="Override server host",
    )
    p.add_argument(
        "--port",
        type=int,
        default=None,
        help="Override server port",
    )
    p.add_argument(
        "--strategy",
        default=None,
        choices=[
            "round_robin",
            "weighted_round_robin",
            "least_connections",
            "latency_aware",
        ],
        help="Override load-balancing strategy",
    )
    return p.parse_args()


async def run(config_path: str, host=None, port=None, strategy=None) -> None:
    config = load_config(config_path)
    if host:
        config.server.host = host
    if port:
        config.server.port = port
    if strategy:
        config.load_balancing.strategy = strategy

    setup_logging(config.logging.level, config.logging.format)

    app = LoadBalancerApp(config)
    await app.start()

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("signal received, shutting down")
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Windows
            signal.signal(sig, lambda s, f: _signal_handler())

    await stop_event.wait()
    await app.stop()


def main() -> None:
    args = parse_args()
    try:
        asyncio.run(
            run(
                args.config,
                host=args.host,
                port=args.port,
                strategy=args.strategy,
            )
        )
    except KeyboardInterrupt:
        pass
    except Exception:
        logging.exception("fatal error")
        sys.exit(1)


if __name__ == "__main__":
    main()
