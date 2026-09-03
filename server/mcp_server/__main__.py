"""Entrypoint: ``python -m mcp_server``."""

import logging

import uvicorn

from .app import build_app, port
from .metrics import start_metrics_server

logging.basicConfig(level=logging.INFO)


def main() -> None:
    start_metrics_server()
    uvicorn.run(build_app(), host="0.0.0.0", port=port())


if __name__ == "__main__":
    main()
