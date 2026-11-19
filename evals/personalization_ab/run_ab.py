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

N_PROFILE, N_PROMPTS = 10, 10
PROMPT_SLUG_ENVS = (
    "PERSONALIZATION_PROMPT_SLUG_ANSWERING",
    "PERSONALIZATION_PROMPT_SLUG_PROFILE_USED",
    "PERSONALIZATION_PROMPT_SLUG_PROFILE_WRITER",
    "PERSONALIZATION_PROMPT_SLUG_PROFILE_EXTRACTION",
    "PERSONALIZATION_PROMPT_SLUG_PROFILE_DREAM",
    "PERSONALIZATION_PROMPT_SLUG_PROFILE_EDIT",
)
PILOT_MAX_USERS = 3


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--users", type=int, required=True)
    p.add_argument("--seed", type=int, required=True)
    p.add_argument("--data-dir", required=True)
    p.add_argument("--prompts", choices=("bundled", "braintrust"), default="bundled")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument(
        "--judge-only",
        action="store_true",
        help="load the saved packets under DIR/<pilot|full>/ and only judge and report",
    )
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


def configure_run_env(environ=None) -> None:
    """Before Django starts: engine traces stay out of the production Braintrust logs (report.py
    uses the braintrust SDK directly, so experiment logging is unaffected), and the Claude CLI's
    debug-to-stderr stream (on by default; any non-empty CLAUDE_SDK_DEBUG enables it) is off."""
    environ = os.environ if environ is None else environ
    environ["BRAINTRUST_LOGGING_ENABLED"] = "false"
    environ["CLAUDE_SDK_DEBUG"] = ""


def write_private(path: Path, data: dict) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(data, ensure_ascii=False, indent=2))
    os.chmod(path, 0o600)


def load_packets(data_dir: Path) -> tuple[str, list[dict]]:
    for mode in ("full", "pilot"):
        files = sorted((data_dir / mode).glob("user-*.json"))
        if files:
            return mode, [json.loads(f.read_text(encoding="utf-8")) for f in files]
    return "", []


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


def answer_pair(user, turns, idx, text_a, text_b, answerer, summary_builder):
    """Answer prompt `turns[idx]` under both Profiles with the context production would give it:
    the new message plus the summary of this session's earlier turns (same session, before idx),
    built once so A and B get the identical string."""
    t = turns[idx]
    earlier = [x for x in turns[:idx] if x.session_id == t.session_id]
    summary = summary_builder(earlier, f"{user}-{t.session_id}-{idx}")
    n = len(earlier) + 1
    return (
        answerer(user, t.prompt, text_a, summary, turn_number=n),
        answerer(user, t.prompt, text_b, summary, turn_number=n),
    )


def apply_prompt_source(mode: str, environ=None) -> None:
    """`bundled` clears the six slug settings (bundled files); `braintrust` needs all six set in
    the environment and copies them into settings. Raises RuntimeError naming any missing one."""
    from django.conf import settings

    environ = os.environ if environ is None else environ
    if mode == "bundled":
        for name in PROMPT_SLUG_ENVS:
            setattr(settings, name, "")
        return
    missing = [n for n in PROMPT_SLUG_ENVS if not environ.get(n, "").strip()]
    if missing:
        raise RuntimeError(
            "--prompts braintrust needs all six slugs set; missing: "
            + ", ".join(missing)
        )
    for name in PROMPT_SLUG_ENVS:
        setattr(settings, name, environ[name].strip())


def run(
    args,
    *,
    answerer=None,
    summary_builder=None,
    judge_call=None,
    llm=None,
    jev=None,
    client=None,
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
    if not args.judge_only:
        if not os.environ.get("SEMANTIC_SEARCH_API_TOKEN", "").strip():
            print(
                "refusing: SEMANTIC_SEARCH_API_TOKEN is not set; without it every "
                "semantic_search call 401s and the answers come from a crippled engine",
                file=sys.stderr,
            )
            return 2
        if not (data_dir / "export.json").is_file():
            print(f"missing {data_dir / 'export.json'}", file=sys.stderr)
            return 2

    configure_run_env()
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "chatbot_server.settings")
    import django

    django.setup()
    from personalization_ab import answer, judge, report

    packets, judgments = [], []
    if args.judge_only:
        mode, saved = load_packets(data_dir)
        if not saved:
            print(f"no saved packets under {data_dir}/<pilot|full>", file=sys.stderr)
            return 2
        out_dir = data_dir / mode
        for packet in saved:
            _judge_and_save(packet, out_dir, args.seed, judge_call, judge)
            packets.append(packet)
    else:
        from chat.V2.personalization.llm import ClaudeCodeLLMClient
        from chat.V2.personalization.tests.support import real_scrub

        from personalization_ab import profiles, select

        try:
            apply_prompt_source(args.prompts)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        llm = llm or ClaudeCodeLLMClient()
        answerer = answerer or answer.answer_turn
        summary_builder = summary_builder or answer.session_summary
        by_user = {u: select.dedupe(ts) for u, ts in load_export(data_dir).items()}
        chosen = select.pick_users(by_user, args.users, args.seed)
        mode = "pilot" if len(chosen) <= PILOT_MAX_USERS else "full"
        out_dir = data_dir / mode
        out_dir.mkdir(exist_ok=True)
        os.chmod(out_dir, 0o700)
        pseudo = real_scrub.Pseudonymizer()
        versions = report.prompt_versions()
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
                ans_a, ans_b = answer_pair(
                    user,
                    turns,
                    N_PROFILE + k,
                    a.text,
                    b.text,
                    answerer,
                    summary_builder,
                )
                items.append({
                    "index": N_PROFILE + k + 1, "prompt": t.prompt, "original": t.original_answer,
                    "A": ans_a.text, "B": ans_b.text,
                    "trace_ids": {"A": ans_a.trace_id, "B": ans_b.trace_id},
                })  # fmt: skip
            packet = {
                "user": user, "mode": mode, "seed": args.seed,
                "profile_a": a.text, "profile_b": b.text,
                "revisions": {"A": a.revisions, "B": b.revisions},
                "dropped": {"A": a.dropped, "B": b.dropped},
                "prompt_versions": versions,
                "turns": [{"prompt": t.prompt, "answer": t.original_answer} for t in turns[:N_PROFILE]],
                "prompts": items,
            }  # fmt: skip
            write_private(out_dir / f"user-{user}.json", packet)  # before judging
            _judge_and_save(packet, out_dir, args.seed, judge_call, judge)
            packets.append(packet)
    if not packets:
        print("no users were run", file=sys.stderr)
        return 1
    judgments = [judge.Judgment(**j) for p in packets for j in p["judgments"]]
    usable = [j for j in judgments if "unusable" not in j.data]
    print(f"judgments: {len(usable)} usable, {len(judgments) - len(usable)} unusable")
    print(
        report.log_experiment(
            packets, usable, args.prompts, client=client, seed=args.seed
        )
    )
    return 0


def _judge_and_save(packet, out_dir, seed, judge_call, judge) -> None:
    js = judge.run_judges(packet, seed=packet.get("seed", seed), call=judge_call)
    packet["judgments"] = [asdict(j) for j in js]
    write_private(out_dir / f"user-{packet['user']}.json", packet)


if __name__ == "__main__":
    sys.exit(run(parse_args()))
