"""Default FastAPI CLI; the previous aiohttp runtime is available via --legacy."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

import uvicorn

from app.fastapi_lb.routing import STRATEGIES
from app.fastapi_lb.server import create_app


def main() -> None:
    if "--legacy" in sys.argv:
        sys.argv.remove("--legacy")
        from app.legacy_main import main as legacy_main
        legacy_main()
        return
    parser = argparse.ArgumentParser(description="Pulse FastAPI load balancer")
    parser.add_argument("-c", "--config", default=None, help="Trusted JSON/YAML route registry")
    parser.add_argument("--lab", action="store_true", help="Explicitly enable isolated experiments")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--strategy", choices=STRATEGIES)
    args = parser.parse_args()
    if args.lab and args.config:
        parser.error("--lab cannot be combined with external routes")
    if not args.lab and not args.config:
        args.config = str(Path(__file__).resolve().parents[1] / "config/live.local.json")
    settings = {}
    if args.config:
        settings = yaml.safe_load(Path(args.config).read_text(encoding="utf-8")).get("server", {})
    host = args.host or settings.get("host", "127.0.0.1")
    port = args.port if args.port is not None else int(settings.get("port", 8080))
    if not 1 <= port <= 65535:
        parser.error("port must be between 1 and 65535")
    uvicorn.run(create_app(args.config, port, args.strategy, lab=args.lab),
                host=host, port=port, workers=1, proxy_headers=False)


if __name__ == "__main__":
    main()
