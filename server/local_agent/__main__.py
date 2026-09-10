"""Entrypoint: ``python -m local_agent``."""

from __future__ import annotations

import argparse
import logging

import uvicorn

from . import config, pairing, session
from .app import build_app
from .paths import data_dir, ensure_data_dir

LOOPBACK = "127.0.0.1"

logger = logging.getLogger("local_agent")


def _announce(port: int) -> None:
    """Print the code a browser tab needs to pair with this agent."""
    code = pairing.new_code()
    session.set_code(code)

    if pairing.load():
        logger.info("  paired:   yes (re-pair to replace)")

    logger.info("")
    logger.info("  To connect this machine, open sefaria.org and enter:")
    logger.info("      %s", code)
    logger.info("")
    logger.info(
        "  Valid once, for %d hours. Restart to get a new one.", session.TTL_SECONDS // 3600
    )
    logger.info("")


def main() -> None:
    parser = argparse.ArgumentParser(prog="local_agent")
    parser.add_argument("--port", type=int, default=config.port())
    parser.add_argument("--check", action="store_true", help="Set up and exit without serving.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ensure_data_dir()

    logger.info("Sefaria local agent")
    logger.info("  data:     %s", data_dir())
    logger.info("  sefaria:  %s", config.chatbot_url())
    logger.info("  mcp:      %s (%s)", config.mcp_url(), config.mcp_server_config()["type"])
    logger.info("  model:    %s", config.model())
    logger.info("  history:  %s", "on" if config.history_enabled() else "off")

    if args.check:
        logger.info("  check:    ok")
        return

    _announce(args.port)
    logger.info("  serving:  http://%s:%s", LOOPBACK, args.port)
    uvicorn.run(build_app(), host=LOOPBACK, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
