"""The renderer: a tree (see `tree.py`) becomes one self-contained HTML file and wiki pages.

Every string reaches the HTML through `textContent` in the page's script, and the embedded data
block escapes `<`, `>` and `&`, so a string from a branch can never become markup. A tree in
which a real-dataset result carries `per_item` is refused (`RealDataLeak`) before anything is written.
"""

from __future__ import annotations

import json
from pathlib import Path

from .metrics import FORMULAS_EXTRA, METRICS, split_key
from .results import RealDataLeak, is_real
from .steps import STEPS

TEMPLATE = Path(__file__).parent / "templates" / "tree.html"
MARKS = {"keep": "keep", "drop": "drop", "inconclusive": "inconclusive", "planned": "planned", "blocked": "blocked", "running": "running"}  # fmt: skip
PROJECT_LINK = "[[projects/la-personalization/lab/_index]]"


def formula_for(key: str, aspect: str) -> str:
    name = split_key(key)[0]
    if name in METRICS:
        return METRICS[name].formula
    if name in FORMULAS_EXTRA:
        return FORMULAS_EXTRA[name]
    return f"defined in cases/{aspect}/metrics.py on the aspect branch"


def check_no_leak(tree: dict) -> None:
    for aspect in tree["aspects"]:
        for case in aspect["cases"]:
            for result in [*case.get("runs", []), *case.get("real_runs", [])]:
                if is_real(result) and "per_item" in result:
                    raise RealDataLeak(f"{case['id']}: a real-dataset result carries per_item")


def _num(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".") or "0"
    return str(value)


def _bar_text(bar: dict) -> str:
    target = bar.get("value") if bar.get("ref") is None else f"{bar['ref']}"
    where = f" on {bar['dataset']}" if bar.get("dataset") else ""
    flag = " (expected to fail)" if bar.get("expected_to_fail") else ""
    return f"{bar['metric']} {bar.get('agg', 'mean')} {bar.get('op', '>=')} {target}{where}{flag}"


def _headline(case: dict) -> str:
    bars = (case.get("verdict") or {}).get("bars") or []
    for bar in bars:
        if bar.get("observed") is not None:
            return f"{bar['metric']} {_num(bar['observed'])}"
    for result in case.get("runs") or [case.get("mock")]:
        metrics = (result or {}).get("metrics") or {}
        if metrics:
            key = sorted(metrics)[0]
            return f"{key} {_num(metrics[key])}"
    return "-"


def case_view(aspect: dict, case: dict) -> dict:
    manifest = case.get("manifest") or {}
    verdict = case.get("verdict") or {}
    state = verdict.get("verdict")
    mark = (
        case["status"]
        if case["status"] == "blocked"
        else (state or ("running" if case["status"] == "running" else "planned"))
    )
    columns: list[tuple[str, dict]] = []
    if case.get("mock"):
        columns.append(("mock", case["mock"]))
    columns += [(f"run {r.get('run', i + 1)}", r) for i, r in enumerate(case.get("runs", []))]
    columns += [
        (f"real run {r.get('run', i + 1)}", r) for i, r in enumerate(case.get("real_runs", []))
    ]
    keys = sorted({k for _, r in columns for k in r.get("metrics", {})})
    metric_rows = []
    for key in keys:
        row = [key]
        for _, r in columns:
            value = r.get("metrics", {}).get(key)
            ratio = r.get("ratios", {}).get(key)
            row.append(
                _num(value) + (f" ({ratio[0]}/{ratio[1]})" if ratio and value is not None else "")
            )
        row.append(formula_for(key, aspect["id"]))
        metric_rows.append(row)
    step_tables = [
        {"column": name, "rows": [[s["name"], STEPS.get(s["name"], "(not in the catalog)"), _num(s.get("n_in")), _num(s.get("n_out"))] for s in r.get("steps", [])]}
        for name, r in columns
        if r.get("steps")
    ]  # fmt: skip
    cost_rows = [
        [name, *(_num((r.get("cost") or {}).get(k)) for k in ("calls", "in_tokens", "out_tokens", "typed_requests", "usd"))]
        for name, r in columns
        if r.get("cost")
    ]  # fmt: skip
    return {
        "id": case["id"], "branch": case["branch"], "pr_url": case.get("pr_url") or "",
        "question": manifest.get("question", ""), "why": manifest.get("why", ""),
        "source": manifest.get("source", ""), "hypothesis": manifest.get("hypothesis", ""),
        "blocked_reason": manifest.get("blocked_reason", ""), "mark": mark, "label": mark,
        "headline": _headline(case), "case_sha": case.get("case_sha", ""),
        "bars_text": [_bar_text(b) for b in manifest.get("keep_if", [])],
        "mock_rows": [[k, _num(v)] for k, v in sorted((case.get("mock") or {}).get("metrics", {}).items())],
        "columns": [n for n, _ in columns], "metric_rows": metric_rows, "step_tables": step_tables,
        "cost_rows": cost_rows,
        "bar_rows": [[f"{b['metric']} {b.get('agg', '')} {b.get('op', '')}", _num(b.get("target")), _num(b.get("observed")), b.get("state", "")] for b in verdict.get("bars", [])],
        "note": verdict.get("note", ""),
    }  # fmt: skip


def view_model(tree: dict) -> dict:
    repo = tree.get("repo_url", "")
    aspects = []
    for aspect in tree["aspects"]:
        cases = []
        for case in aspect["cases"]:
            view = case_view(aspect, case)
            view["branch_url"] = f"{repo}/tree/{case['branch']}" if repo else ""
            cases.append(view)
        aspects.append({
            "id": aspect["id"], "question": aspect.get("question", ""), "why": aspect.get("why", ""),
            "source": aspect.get("source", ""), "branch": aspect.get("branch") or "",
            "branch_url": f"{repo}/tree/{aspect['branch']}" if repo and aspect.get("branch") else "",
            "cases": cases,
        })  # fmt: skip
    return {
        "generated_at": tree.get("generated_at", ""),
        "core_sha": tree.get("core_sha", ""),
        "aspects": aspects,
    }


def _embed(data: dict) -> str:
    text = json.dumps(data, ensure_ascii=False)
    for raw, safe in (
        ("<", "\\u003c"),
        (">", "\\u003e"),
        ("&", "\\u0026"),
        ("\u2028", "\\u2028"),
        ("\u2029", "\\u2029"),
    ):
        text = text.replace(raw, safe)
    return text


def render_html(view: dict) -> str:
    return TEMPLATE.read_text(encoding="utf-8").replace("__LAB_DATA__", _embed(view))


def _cell(text) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _md_table(head: list[str], rows: list[list]) -> list[str]:
    lines = ["| " + " | ".join(_cell(h) for h in head) + " |", "|" + "---|" * len(head)]
    return lines + ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]


def case_markdown(aspect: dict, c: dict) -> str:
    lines = [f"## {c['id']}", "", f"**Verdict:** {c['label']}  ", f"**Question:** {c['question']}  ", f"**Why:** {c['why']}  ", f"**Source:** {c['source']}  ", f"**Branch:** `{c['branch']}`" + (f" ([link]({c['branch_url']}))" if c["branch_url"] else "") + (f"  **PR:** {c['pr_url']}" if c["pr_url"] else ""), ""]  # fmt: skip
    if c["blocked_reason"]:
        lines += [f"> [!warning] Blocked: {c['blocked_reason']}", ""]
    lines += ["### Hypothesis", "", c["hypothesis"], "", "### Pass bar (keep if)", ""]
    lines += [f"- {t}" for t in c["bars_text"]] or ["none"]
    lines += ["", "### Mock numbers", ""]
    lines += (
        _md_table(["metric", "value"], c["mock_rows"]) if c["mock_rows"] else ["no mock result yet"]
    )
    lines += ["", "### Runs and metrics", ""]
    lines += (
        _md_table(["metric", *c["columns"], "formula"], c["metric_rows"])
        if c["columns"]
        else ["no results yet"]
    )
    for st in c["step_tables"]:
        lines += ["", f"### Steps: {st['column']}", ""]
        lines += _md_table(["step", "what it does", "in", "out"], st["rows"])
    lines += ["", "### Cost", ""]
    lines += (
        _md_table(
            ["run", "calls", "in tokens", "out tokens", "typed requests", "usd"], c["cost_rows"]
        )
        if c["cost_rows"]
        else ["no cost yet"]
    )
    lines += ["", "### Verdict", ""]
    lines += (
        _md_table(["bar", "target", "observed", "state"], c["bar_rows"])
        if c["bar_rows"]
        else [f"{c['label']} (no bars evaluated)"]
    )
    if c["note"]:
        lines += ["", f"Note: {c['note']}"]
    lines += ["", f"case_sha: {c['case_sha'] or 'unknown'}", ""]
    return "\n".join(lines)


def _frontmatter(title: str, generated: str, tags: list[str]) -> list[str]:
    day = (generated or "")[:10] or "unknown"
    return ["---", "type: project", f'title: "{title}"', f"created: {day}", f"updated: {day}", "tags:", *[f"  - {t}" for t in tags], "status: developing", "related:", f'  - "{PROJECT_LINK}"', "---", ""]  # fmt: skip


CALLOUT = ["> [!warning] Work in progress, not a source of truth", "> Generated from the `lab/*` branches by the lab's tree generator. Numbers come from result files; nothing here is edited by hand.", "", "Generated, do not edit. Regenerate with `scripts/lab_record.sh <case id>`.", ""]  # fmt: skip


def aspect_markdown(view: dict, aspect: dict) -> str:
    lines = _frontmatter(
        f"LA lab aspect: {aspect['id']}",
        view["generated_at"],
        ["sefaria", "ai-chatbot", "personalization", "lab"],
    )
    lines += [f"# Aspect: {aspect['id']}", "", *CALLOUT, f"**Question:** {aspect['question']}  ", f"**Why:** {aspect['why']}  ", f"**Source:** {aspect['source']}", "", "Back to [[projects/la-personalization/lab/tree]].", ""]  # fmt: skip
    return "\n".join(lines) + "\n" + "\n".join(case_markdown(aspect, c) for c in aspect["cases"])


def tree_markdown(view: dict) -> str:
    lines = _frontmatter(
        "LA lab tree",
        view["generated_at"],
        ["sefaria", "ai-chatbot", "personalization", "lab", "evals"],
    )
    lines += ["# LA lab tree", "", *CALLOUT, f"Generated {view['generated_at']}. Lab core: `{view['core_sha'] or 'unknown'}`. Introduction: {PROJECT_LINK}.", "", "- main"]  # fmt: skip
    for a in view["aspects"]:
        lines.append(
            f"  - [[projects/la-personalization/lab/{a['id']}|{a['id']}]]: {a['question']}"
        )
        for c in a["cases"]:
            lines.append(f"    - {c['id']}: {c['label']}; {c['headline']}")
    return "\n".join(lines) + "\n"


def render(tree: dict, html_path, wiki_dir) -> None:
    check_no_leak(tree)
    view = view_model(tree)
    html_path, wiki_dir = Path(html_path), Path(wiki_dir)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    wiki_dir.mkdir(parents=True, exist_ok=True)
    html_path.write_text(render_html(view), encoding="utf-8")
    (wiki_dir / "tree.md").write_text(tree_markdown(view), encoding="utf-8")
    for aspect in view["aspects"]:
        (wiki_dir / f"{aspect['id']}.md").write_text(
            aspect_markdown(view, aspect), encoding="utf-8"
        )
