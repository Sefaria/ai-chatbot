#!/usr/bin/env python3
"""Command-line client for the personalization status and profile endpoints (preview testing).

The user token is read from the environment variable CHATBOT_USER_TOKEN only, never from an
argument. `--dry-run` prints the request with the token replaced by <token> and sends nothing.

    export CHATBOT_USER_TOKEN=...        # type it yourself; do not paste it into a file
    python3 scripts/personalization_cli.py --url https://<preview-host> status
    python3 scripts/personalization_cli.py --url https://<preview-host> set preferred_language he
"""

from __future__ import annotations

import argparse
import json
import os
import sys

TOKEN_ENV = "CHATBOT_USER_TOKEN"
PLACEHOLDER = "<token>"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--url", required=True, help="base URL of the chatbot server")
    parser.add_argument(
        "--dry-run", action="store_true", help="print the request, send nothing"
    )
    parser.add_argument("--subject", help="scope the profile to <user>:eval:<subject>")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="how personalization is set up for you")
    sub.add_parser("get", help="your slots, settings, proposals and counts")
    set_cmd = sub.add_parser("set", help="store a stated value")
    set_cmd.add_argument("slot")
    set_cmd.add_argument(
        "value", nargs="+", help="one value, or several for a list slot"
    )
    release = sub.add_parser(
        "release", help="turn a stated value back into an inferred one"
    )
    release.add_argument("slot")
    sub.add_parser("clear", help="delete your profile")
    settings = sub.add_parser("settings", help="toggle personalizing and storing")
    settings.add_argument(
        "--personalize", action=argparse.BooleanOptionalAction, default=None
    )
    settings.add_argument(
        "--store", action=argparse.BooleanOptionalAction, default=None
    )
    resolve = sub.add_parser("resolve", help="accept or reject a proposal")
    resolve.add_argument("proposal_id")
    resolve.add_argument("decision", choices=["accept", "reject"])
    batch = sub.add_parser("run-batch", help="run the consolidation for your own user")
    batch.add_argument(
        "--mode", choices=["realtime", "nightly", "hybrid", "hybrid_jev", "explicit"]
    )
    return parser


def build_request(args, token: str) -> tuple[str, dict]:
    base = args.url.rstrip("/") + "/api/v2/personalization/"
    if args.command == "status":
        return base + "status", {"userId": token}
    body: dict = {"userId": token, "action": args.command.replace("-", "_")}
    if args.subject:
        body["personalizationSubject"] = args.subject
    if args.command == "set":
        body.update(
            slot=args.slot, value=args.value[0] if len(args.value) == 1 else args.value
        )
    elif args.command == "release":
        body["slot"] = args.slot
    elif args.command == "settings":
        if args.personalize is not None:
            body["personalizeEnabled"] = args.personalize
        if args.store is not None:
            body["storeEnabled"] = args.store
    elif args.command == "resolve":
        body.update(action="resolve_proposal", proposalId=args.proposal_id)
        body["accept"] = args.decision == "accept"
    elif args.command == "run-batch" and args.mode:
        body["mode"] = args.mode
    return base + "profile", body


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    token = os.environ.get(TOKEN_ENV, "")
    if not token and not args.dry_run:
        print(f"Set {TOKEN_ENV} in the environment first.", file=sys.stderr)
        return 2
    url, body = build_request(args, token or PLACEHOLDER)
    if args.dry_run:
        shown = {**body, "userId": PLACEHOLDER}
        print(json.dumps({"method": "POST", "url": url, "body": shown}, indent=2))
        return 0
    import requests

    response = requests.post(url, json=body, timeout=120)
    try:
        print(json.dumps(response.json(), indent=2, ensure_ascii=False))
    except ValueError:
        print(response.text)
    return 0 if response.ok else 1


if __name__ == "__main__":
    sys.exit(main())
