"""The lab command line: python -m chat.V2.personalization.lab <command> (see scripts/lab.sh)."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

from .. import llm as llm_module
from . import (
    datasets,
    guard,
    label,
    manifest,
    render,
    results,
    runner,
    tree,
    validate_data,
    verdict,
)
from .metrics import CASES_ROOT, METRICS, load_aspect_metrics, split_key
from .replay import fake_responder

LIVE_RUNS = 3


def _case_dir(root: Path, case_id: str) -> Path:
    return root / case_id


def _load(root: Path, case_id: str) -> manifest.Case:
    return manifest.load_case(_case_dir(root, case_id))


def cmd_validate(args, root) -> int:
    if args.all:
        dirs = manifest.case_dirs(root)
        bad = 0
        for directory in dirs:
            try:
                manifest.load_case(directory)
            except manifest.ManifestError as error:
                print(f"INVALID {directory.parent.name}/{directory.name}: {error}")
                bad += 1
        print(f"{len(dirs)} cases, {bad} invalid")
        if len(dirs) < args.min_cases:
            print(f"fewer than the required {args.min_cases} cases")
            return 1
        return 1 if bad else 0
    try:
        case = _load(root, args.id)
    except manifest.ManifestError as error:
        print(f"INVALID {args.id}: {error}")
        return 1
    print(f"ok {case.id} ({case.status})")
    return 0


def _live_llm(model: str):
    client = llm_module.ClaudeCodeLLMClient()
    client.calibrate(model)
    return client


def _typed_for(case, dataset, llm):
    config = case.config
    variants = config.get("variants") or []
    needs_jev = (
        config.get("typed") == "jev"
        or "hybrid_jev" in variants
        or config.get("strategy") == "hybrid_jev"
    )
    if needs_jev:
        if dataset.kind == "real" and not dataset.anonymized:
            return None
        if os.environ.get("PERSONALIZATION_ALLOW_JEV") != "true":
            return None
        try:
            from ..jev import HttpJevClient
            from ..typed import JevTypedClient

            return JevTypedClient(HttpJevClient(allow_keychain=True))
        except Exception:  # a missing key leaves the Jev design unavailable, recorded as invalid
            return None
    if config.get("typed") == "haiku":
        from ..typed import HaikuTypedClient

        return HaikuTypedClient(llm, config.get("model", runner.DEFAULT_MODEL))
    return None


def _run_mock(case, root, deps) -> int:
    module = runner.load_pipeline(case.dir)
    llm = (
        module.mock_llm()
        if hasattr(module, "mock_llm")
        else llm_module.FakeLLMClient(fake_responder)
    )
    typed = module.mock_typed() if hasattr(module, "mock_typed") else None
    if hasattr(module, "mock_items"):
        items = module.mock_items()
    else:
        items = datasets.load_dataset(case.datasets[0]).items
    sha = hashlib.sha256(json.dumps(items, sort_keys=True, default=str).encode()).hexdigest()
    dataset = datasets.Dataset("mock", "synthetic", sha, items)
    result = runner.run_case(case, kind="mock", run_no=0, dataset=dataset, llm=llm, typed=typed)
    path = results.write(case.dir, result)
    print(f"mock result written: {path}")
    return 0


def _run_live(case, args, deps) -> int:
    wanted = (
        [args.dataset]
        if args.dataset
        else [d for d in case.datasets if datasets.registry()[d]["kind"] != "real"]
    )
    if not wanted:
        print("no synthetic dataset to run; name a real one with --dataset")
        return 1
    model = case.config.get("model", runner.DEFAULT_MODEL)
    llm = deps.get("llm") or _live_llm(model)
    for run_no in range(1, args.runs + 1):
        for name in wanted:
            dataset = datasets.load_dataset(name)
            real = dataset.kind == "real"
            file = results.file_name("live", run_no, real)
            existing = results.read(case.dir, file)
            primary = name == case.datasets[0] or real
            if (
                args.resume
                and existing
                and ((existing["dataset"]["name"] == name) or name in existing.get("also", {}))
            ):
                print(f"run {run_no} on {name}: already done")
                continue
            typed = deps.get("typed") or _typed_for(case, dataset, llm)
            result = runner.run_case(
                case, kind="live", run_no=run_no, dataset=dataset, llm=llm, typed=typed,
                resume=args.resume,
            )  # fmt: skip
            if primary:
                results.write(case.dir, result)
            else:
                if existing is None:
                    print(
                        f"run {run_no}: run the case's first dataset {case.datasets[0]} before {name}"
                    )
                    return 1
                existing.setdefault("also", {})[name] = {
                    k: result[k] for k in ("dataset", "metrics", "ratios", "invalid", "privacy")
                }
                results.write(case.dir, existing, file)
            print(
                f"run {run_no} on {name}: {len(result['metrics'])} metrics, usd {result['cost']['usd']:.4f}"
            )
    return 0


def _print_unlabeled_count(name: str) -> None:
    """When labeled.jsonl has fewer rows than the unlabeled file, print how many are unlabeled."""
    try:
        directory = validate_data._resolve_dataset_dir(name, None)
        labeled_path = directory / "labeled.jsonl"
        if not labeled_path.is_file():
            return
        labeled = sum(
            1 for ln in labeled_path.read_text(encoding="utf-8").splitlines() if ln.strip()
        )
        total = len(label._read_unlabeled_items(directory))
    except (OSError, ValueError, label.LabelError, datasets.DatasetError):
        return
    if labeled < total:
        print(f"{total - labeled} unlabeled of {total} items in {name}")


def cmd_validate_data(args) -> int:
    try:
        problems, report = validate_data.validate_with_report(args.name)
    except validate_data.DatasetBlocked as error:
        print(f"blocked {args.name}: {error}")
        return 3
    except validate_data.ValidationUnavailable as error:
        print(error)
        return 2
    for problem in problems[:40]:
        print(f"INVALID {args.name}: {problem}")
    if len(problems) > 40:
        print(f"... and {len(problems) - 40} more")
    _print_unlabeled_count(args.name)
    if problems:
        return 1
    print(f"ok {args.name} {json.dumps(report, sort_keys=True)}")
    return 0


def cmd_label(args, deps) -> int:
    result = label.label_dataset(
        args.name,
        model=args.model,
        limit=args.limit,
        llm=deps.get("llm"),
        workers=args.workers,
    )
    print(
        f"labeled {args.name}: {result['labeled']}/{result['total']} "
        f"(malformed={result['malformed']}) sha256={result['sha256']}"
    )
    return 0


def cmd_run(args, root, deps) -> int:
    case = _load(root, args.id)
    if case.status == "blocked":
        print(f"{case.id} is blocked: {case.blocked_reason}")
        return 3
    if args.mock:
        return _run_mock(case, root, deps)
    if not args.live:
        print("say --mock or --live")
        return 2
    return _run_live(case, args, deps)


def _live_results(case) -> list[dict]:
    found = []
    for run_no in range(1, LIVE_RUNS + 1):
        for real in (False, True):
            found += [
                r for r in [results.read(case.dir, results.file_name("live", run_no, real))] if r
            ]
    return found


def cmd_verdict(args, root) -> int:
    case = _load(root, args.id)
    found = _live_results(case)
    computed = verdict.compute(case, found)
    path = Path(case.dir) / "results" / "verdict.json"
    if path.is_file():
        computed["note"] = json.loads(path.read_text(encoding="utf-8")).get("note", "")
    computed.update(id=case.id, case_sha=runner._safe(guard.case_sha, case.dir), runs=len(found))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(computed, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8"
    )
    print(f"{case.id}: {computed['verdict']}")
    for bar in computed["bars"]:
        print(
            f"  {bar['metric']} {bar['agg']} {bar['op']} {bar['target']}: observed {bar['observed']} -> {bar['state']}"
        )
    return 0


def cmd_explain(args, root) -> int:
    case = _load(root, args.id)
    known = {**METRICS, **load_aspect_metrics(Path(case.dir).parent / "metrics.py")}
    print(f"Case {case.id} ({case.status}, {case.engine}/{case.task})")
    print(f"Question: {case.question}\nWhy: {case.why}\nHypothesis: {case.hypothesis}")
    print(f"Datasets: {', '.join(case.datasets)}\nMetrics:")
    seen = set()
    for key in [*case.metrics, *(b.metric for b in case.keep_if)]:
        name, _ = split_key(key)
        if name not in seen:
            seen.add(name)
            print(f"  {name} ({known[name].unit}): {known[name].formula}")
    print("Pass bar (keep if):")
    for bar in case.keep_if:
        target = (
            bar.value
            if not bar.ref
            else f"{bar.ref.get('factor', 1)} x {bar.ref['case']}:{bar.ref['metric']} + {bar.ref.get('offset', 0)}"
        )
        flag = " (expected to fail; reported, not counted)" if bar.expected_to_fail else ""
        print(f"  {bar.metric} [{bar.agg}] {bar.op} {target} on {bar.dataset}{flag}")
    for name in (
        "mock.json",
        "run1.json",
        "run2.json",
        "run3.json",
        "real_run1.json",
        "real_run2.json",
        "real_run3.json",
    ):
        result = results.read(case.dir, name)
        if result:
            print(
                f"{name} (dataset {result['dataset']['name']}): "
                + json.dumps(result["metrics"], sort_keys=True)
            )
    if (Path(case.dir) / "pipeline.py").is_file():
        hook = getattr(runner.load_pipeline(case.dir), "explain_lines", None)
        for line in hook(case, lambda name: results.read(case.dir, name)) if hook else []:
            print(line)
    final = results.read(case.dir, "verdict.json")
    if final:
        print(f"Verdict: {final['verdict']}" + (f" ({final['note']})" if final.get("note") else ""))
    return 0


def cmd_check(args, root) -> int:
    try:
        case = _load(root, args.id)
    except manifest.ManifestError as error:
        print(f"INVALID {args.id}: {error}")
        return 1
    if case.status == "blocked":
        print(f"blocked: {case.blocked_reason}")
        return 3
    problems = []
    sha = guard.case_sha(case.dir)
    names = ["mock.json", *(f"run{n}.json" for n in range(1, LIVE_RUNS + 1)), "verdict.json"]
    registry = datasets.registry()
    for name in names:
        data = results.read(case.dir, name)
        if data is None:
            problems.append(f"{name} is missing")
            continue
        if data.get("case_sha") != sha:
            problems.append(f"{name} was made for another version of the case")
        if name != "verdict.json":
            parts = [data["dataset"], *(p["dataset"] for p in data.get("also", {}).values())]
            for part in parts:
                entry = registry.get(part["name"])
                if entry and entry["kind"] == "synthetic" and entry.get("sha256") != part["sha256"]:
                    problems.append(f"{name}: dataset {part['name']} changed since the run")
    for name in (f"real_run{n}.json" for n in range(1, LIVE_RUNS + 1)):
        data = results.read(case.dir, name)
        if data is None:
            continue
        if data.get("case_sha") != sha:
            problems.append(f"{name} was made for another version of the case")
        for part in [data["dataset"], *(p["dataset"] for p in data.get("also", {}).values())]:
            if registry.get(part["name"], {}).get("kind") != "real":
                continue
            try:
                current = (
                    (datasets.data_dir() / part["name"] / "sha256")
                    .read_text(encoding="utf-8")
                    .strip()
                )
            except (OSError, datasets.DatasetError):
                problems.append(
                    f"{name}: real dataset {part['name']} cannot be read to compare its checksum"
                )
                continue
            if current != part["sha256"]:
                problems.append(f"{name}: dataset {part['name']} changed since the run")
    for line in problems:
        print(line)
    print(f"{case.id}: {'ok' if not problems else 'FAILED'}")
    return 1 if problems else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lab", description=__doc__)
    parser.add_argument("--cases-root", default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("validate")
    p.add_argument("id", nargs="?")
    p.add_argument("--all", action="store_true")
    p.add_argument("--min-cases", type=int, default=0)
    sub.add_parser("validate-data").add_argument("name")
    p = sub.add_parser("label")
    p.add_argument("name")
    p.add_argument("--model", default=label.DEFAULT_MODEL)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--workers", type=int, default=4)
    p = sub.add_parser("run")
    p.add_argument("id")
    p.add_argument("--mock", action="store_true")
    p.add_argument("--live", action="store_true")
    p.add_argument("--runs", type=int, default=LIVE_RUNS)
    p.add_argument("--dataset", default=None)
    p.add_argument("--resume", action="store_true")
    for name in ("verdict", "explain", "check"):
        sub.add_parser(name).add_argument("id")
    p = sub.add_parser("tree")
    p.add_argument("--remote", default="origin")
    p.add_argument("--out", required=True)
    p.add_argument("--with-prs", action="store_true")
    p.add_argument("--repo", default=".")
    p = sub.add_parser("render")
    p.add_argument("--tree", required=True)
    p.add_argument("--html", required=True)
    p.add_argument("--wiki-dir", required=True)
    return parser


def cmd_tree(args) -> int:
    data = tree.write_tree(args.repo, args.remote, args.out, args.with_prs)
    cases = sum(len(a["cases"]) for a in data["aspects"])
    print(f"tree: {len(data['aspects'])} aspects, {cases} cases -> {args.out}")
    return 0


def cmd_render(args) -> int:
    data = json.loads(Path(args.tree).read_text(encoding="utf-8"))
    render.render(data, args.html, args.wiki_dir)
    print(f"render: {args.html} and {args.wiki_dir}")
    return 0


def main(argv: list[str] | None = None, **deps) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.cases_root or os.environ.get("LAB_CASES_ROOT") or CASES_ROOT)
    if args.command == "validate" and not args.all and not args.id:
        print("validate needs an id or --all")
        return 2
    try:
        if args.command == "tree":
            return cmd_tree(args)
        if args.command == "render":
            return cmd_render(args)
        if args.command == "validate":
            return cmd_validate(args, root)
        if args.command == "validate-data":
            return cmd_validate_data(args)
        if args.command == "label":
            return cmd_label(args, deps)
        if args.command == "run":
            return cmd_run(args, root, deps)
        return {"verdict": cmd_verdict, "explain": cmd_explain, "check": cmd_check}[args.command](
            args, root
        )
    except (manifest.ManifestError, guard.NotPreregistered, datasets.DatasetError, datasets.DatasetChanged,
            runner.JevOnIdentifiableData, runner.RunnerError, results.RealDataLeak, label.LabelError) as error:  # fmt: skip
        print(f"{type(error).__name__}: {error}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
