"""Run the personalization A/B comparison end to end. See README.md.

python evals/personalization_ab/run_ab.py --users 3 --seed 1 --data-dir ~/la-lab --prompts bundled
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))  # evals/, so `personalization_ab` imports
sys.path.insert(0, str(HERE.parents[2] / "server"))  # server/, for chat.* and Django

N_PROFILE, N_PROMPTS, HISTORY_TURNS = 10, 10, 4
PILOT_MAX_USERS = 3


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--users", type=int, required=True)
    p.add_argument("--seed", type=int, required=True)
    p.add_argument("--data-dir", required=True)
    p.add_argument("--prompts", choices=("bundled", "braintrust"), default="bundled")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args(argv)


def inside_git_repo(path) -> bool:
    import subprocess

    resolved = Path(path).expanduser().resolve()
    if any((a / ".git").exists() for a in (resolved, *resolved.parents)):
        return True
    probe = resolved if resolved.is_dir() else resolved.parent
    proc = subprocess.run(
        ["git", "-C", str(probe), "rev-parse", "--is-inside-work-tree"],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def load_export(data_dir: Path):
    from personalization_ab.select import Turn

    rows = json.loads((data_dir / "export.json").read_text(encoding="utf-8"))
    by_user = defaultdict(list)
    for n, r in enumerate(rows):
        by_user[r["user_id"]].append(
            Turn(r["user_id"], r["session_id"], n, datetime.fromisoformat(r["timestamp"]),
                 r["prompt"], r["answer"])
        )  # fmt: skip
    return by_user


def _scrub_turns(turns, llm, pseudo, raw_user):
    from chat.V2.personalization.tests.support import real_scrub

    user = pseudo.user_id(raw_user)
    out = []
    for t in turns:
        parts = []
        for text in (t.prompt, t.original_answer):
            text, _ = real_scrub.scrub_text(text)
            text, _, ok = real_scrub.scrub_names(text, llm)
            if not ok or real_scrub.residual_hits(text):
                raise RuntimeError("scrub left residual personal data; user skipped")
            parts.append(text)
        out.append(
            type(t)(
                user,
                pseudo.session_id(raw_user, t.session_id),
                t.turn_number,
                t.created,
                *parts,
            )
        )
    return user, out


def run(
    args, *, answerer=None, judge_call=None, llm=None, jev=None, client=None
) -> int:
    data_dir = Path(args.data_dir).expanduser()
    if inside_git_repo(data_dir):
        print(
            f"refusing: data dir {data_dir} is inside a git repository", file=sys.stderr
        )
        return 2
    if not data_dir.is_dir():
        print(f"data dir does not exist: {data_dir}", file=sys.stderr)
        return 2
    if args.users < 1:
        print("--users must be at least 1", file=sys.stderr)
        return 2
    if args.dry_run:
        print(
            f"dry run ok: {args.users} users, seed {args.seed}, prompts {args.prompts}, data {data_dir}"
        )
        return 0
    if not (data_dir / "export.json").is_file():
        print(f"missing {data_dir / 'export.json'}", file=sys.stderr)
        return 2

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "chatbot_server.settings")
    import django

    django.setup()
    from chat.V2.personalization.llm import ClaudeCodeLLMClient
    from chat.V2.personalization.tests.support import real_scrub

    from personalization_ab import answer, judge, profiles, report, select

    llm = llm or ClaudeCodeLLMClient()
    answerer = answerer or answer.answer_turn
    by_user = {u: select.dedupe(ts) for u, ts in load_export(data_dir).items()}
    chosen = select.pick_users(by_user, args.users, args.seed)
    mode = "pilot" if len(chosen) <= PILOT_MAX_USERS else "full"
    out_dir = data_dir / mode
    out_dir.mkdir(exist_ok=True)
    pseudo = real_scrub.Pseudonymizer()
    packets, judgments = [], []
    for raw in chosen:
        try:
            user, turns = _scrub_turns(
                by_user[raw][: N_PROFILE + N_PROMPTS], llm, pseudo, raw
            )
        except RuntimeError as exc:
            print(f"skipping one user: {exc}", file=sys.stderr)
            continue
        a = profiles.build_profile_a(user, turns, llm, jev)
        b = profiles.build_profile_b(user, turns, llm, jev)
        items = []
        for k, t in enumerate(turns[N_PROFILE : N_PROFILE + N_PROMPTS]):
            history = turns[
                max(0, N_PROFILE + k - HISTORY_TURNS) : N_PROFILE + k
            ]  # same for A and B
            ans_a = answerer(user, history, t.prompt, a.text)
            ans_b = answerer(user, history, t.prompt, b.text)
            items.append({
                "index": N_PROFILE + k + 1, "prompt": t.prompt, "original": t.original_answer,
                "A": ans_a.text, "B": ans_b.text,
                "trace_ids": {"A": ans_a.trace_id, "B": ans_b.trace_id},
            })  # fmt: skip
        packet = {
            "user": user, "mode": mode, "seed": args.seed, "profile_a": a.text, "profile_b": b.text,
            "revisions": {"A": a.revisions, "B": b.revisions},
            "turns": [{"prompt": t.prompt, "answer": t.original_answer} for t in turns[:N_PROFILE]],
            "prompts": items,
        }  # fmt: skip
        js = judge.run_judges(packet, seed=args.seed, call=judge_call)
        packet["judgments"] = [asdict(j) for j in js]
        (out_dir / f"user-{user}.json").write_text(
            json.dumps(packet, ensure_ascii=False, indent=2)
        )
        packets.append(packet)
        judgments.extend(js)
    if not packets:
        print("no users were run", file=sys.stderr)
        return 1
    print(
        report.log_experiment(
            packets, judgments, args.prompts, client=client, seed=args.seed
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(run(parse_args()))
