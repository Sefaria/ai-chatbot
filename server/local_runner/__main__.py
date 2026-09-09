"""Entrypoint: ``python -m local_runner``.

Serves WSGI with a thread pool, matching how production runs the same views
(``gunicorn ... --worker-class gthread --threads 4``), so streaming behaves the
same way here as it does on the server. Waitress rather than gunicorn because the
daemon has to run on a user's machine, including Windows.
"""

from __future__ import annotations

import argparse
import logging
import os

from . import DEFAULT_PORT, pairing, session
from .identity import ServerNotConfigured, server_base_url
from .paths import data_dir, database_path, ensure_data_dir

LOOPBACK = "127.0.0.1"
THREADS = 4

logger = logging.getLogger("local_runner")


def _setup_django() -> None:
    import django

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "local_runner.settings")
    django.setup()


def _migrate() -> None:
    """Bring the local SQLite database up to date.

    Runs on every start: the schema travels with the runner, and a user has no
    other way to apply migrations after an upgrade.
    """
    from django.core.management import call_command

    call_command("migrate", interactive=False, verbosity=0)


def _port() -> int:
    return int(os.environ.get("SEFARIA_AGENT_PORT", DEFAULT_PORT))


def _announce_pairing(port: int) -> None:
    """Print the code a browser tab needs to pair with this daemon.

    A fresh code every run: it lives in memory only, so a code from a previous
    run cannot pair. An already-paired daemon still prints one, because re-pairing
    is how a user recovers a lost or rotated runner token.
    """
    code = pairing.new_code()
    session.set_code(code)

    existing = pairing.load()
    if existing:
        logger.info("  paired:   yes (re-pair to replace)")

    logger.info("")
    logger.info("  To connect this machine, open sefaria.org and enter:")
    logger.info("      %s", code)
    logger.info("")
    logger.info(
        "  Valid once, for %d minutes. Restart to get a new one.", session.TTL_SECONDS // 60
    )
    logger.info("")


def main() -> None:
    parser = argparse.ArgumentParser(prog="local_runner")
    parser.add_argument("--port", type=int, default=_port())
    parser.add_argument(
        "--check",
        action="store_true",
        help="Set up, migrate and exit without serving.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    try:
        server = server_base_url()
    except ServerNotConfigured as exc:
        # Fail here rather than at the first proxied call: a missing server is a
        # setup mistake, and it should read like one.
        logger.error("%s", exc)
        raise SystemExit(2) from exc

    ensure_data_dir()
    _setup_django()
    _migrate()

    logger.info("Sefaria agent runner")
    logger.info("  data:     %s", data_dir())
    logger.info("  database: %s", database_path())
    logger.info("  server:   %s", server)

    if args.check:
        logger.info("  check:    ok")
        return

    _announce_pairing(args.port)

    from django.core.wsgi import get_wsgi_application
    from waitress import serve

    logger.info("  serving:  http://%s:%s", LOOPBACK, args.port)
    serve(get_wsgi_application(), host=LOOPBACK, port=args.port, threads=THREADS)


if __name__ == "__main__":
    main()
