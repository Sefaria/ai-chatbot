#!/usr/bin/env python3
"""Create visual summaries for the latency partition dataframe.

This script reads `trace_latency_partition_rows.csv`, filters valid traces, and
produces:

1. `latency_timeline_mean_std.png`
2. `latency_timeline_grouped.png`
3. `latency_variance_contribution.png`
4. `latency_histograms.png`
5. `latency_summary.txt`

Edit `SCRIPT_CONFIG` directly, then run:
    python latency/current_latency_analysis/scripts/plot_latency_partition_summary.py
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
ANALYSIS_ROOT = REPO_ROOT / "latency" / "current_latency_analysis"
DATA_DIR = ANALYSIS_ROOT / "data"
PARTITION_FILENAME = "trace_latency_partition_rows.csv"
RAW_COMPONENTS = [
    "guardrail_wall_ms",
    "router_wall_ms",
    "llm_ttft_wall_ms",
    "llm_post_first_token_wall_ms",
    "llm_unknown_wall_ms",
    "tool_wall_ms",
    "score_wall_ms",
    "facet_wall_ms",
    "classifier_wall_ms",
    "function_wall_ms",
    "automation_wall_ms",
    "agent_overhead_wall_ms",
    "other_instrumented_wall_ms",
    "uninstrumented_gap_ms",
]
GROUPED_COMPONENTS = [
    "guardrail_wall_ms",
    "router_wall_ms",
    "llm_total",
    "tool_wall_ms",
    "overhead",
]

SCRIPT_CONFIG = {
    "input_csv": None,
    "output_dir": None,
    "hist_bins": 50,
    "timeline_figsize": (12, 3),
    "grouped_figsize": (10, 3),
    "variance_figsize": (8, 6),
    "hist_figsize": (10, 4),
    "std_label_fontsize": 8,
    "small_segment_threshold_ms": 2500,
    "small_segment_y_offsets": [0.34, 0.24, 0.14, 0.04],
}


def require_plotting_deps():
    try:
        import matplotlib.pyplot as plt
        import numpy as np
        import pandas as pd
    except ImportError as exc:
        requirements_path = (
            REPO_ROOT / "latency" / "current_latency_analysis" / "requirements.txt"
        )
        raise RuntimeError(
            "This script requires pandas, numpy, and matplotlib in the active Python "
            "environment. Install them from "
            f"{requirements_path}."
        ) from exc
    return pd, np, plt


def find_latest_partition_csv(data_dir: Path) -> Path:
    candidates = sorted(
        data_dir.glob(f"*/{PARTITION_FILENAME}"),
        key=lambda path: path.stat().st_mtime,
    )
    if not candidates:
        raise RuntimeError(
            f"Could not find any {PARTITION_FILENAME} files under {data_dir}"
        )
    return candidates[-1]


def get_input_csv() -> Path:
    configured = SCRIPT_CONFIG["input_csv"]
    if configured:
        return Path(configured)
    return find_latest_partition_csv(DATA_DIR)


def get_output_dir(input_csv: Path) -> Path:
    configured = SCRIPT_CONFIG["output_dir"]
    if configured:
        return Path(configured)
    return input_csv.parent


def validate_components_present(df: Any, components: list[str]) -> None:
    missing = [component for component in components if component not in df.columns]
    if missing:
        raise RuntimeError(
            f"Partition dataframe is missing expected columns: {missing}"
        )


def build_grouped_columns(df: Any) -> Any:
    df = df.copy()
    df["llm_total"] = (
        df["llm_ttft_wall_ms"]
        + df["llm_post_first_token_wall_ms"]
        + df["llm_unknown_wall_ms"]
    )
    df["overhead"] = (
        df["agent_overhead_wall_ms"]
        + df["other_instrumented_wall_ms"]
        + df["uninstrumented_gap_ms"]
    )
    return df


def compute_variance_contributions(df: Any, np: Any, components: list[str]):
    total = df["total_turn_ms"]
    var_total = np.var(total, ddof=1)
    if var_total == 0:
        return {component: 0.0 for component in components}
    contributions: dict[str, float] = {}
    for component in components:
        cov = np.cov(df[component], total, ddof=1)[0, 1]
        contributions[component] = float(cov / var_total)
    return contributions


def plot_stacked_timeline(
    *,
    ax: Any,
    means: Any,
    stds: Any,
    components: list[str],
    colors: list[Any],
    title: str,
    std_label_fontsize: int,
    small_segment_threshold_ms: float,
    small_segment_y_offsets: list[float],
) -> None:
    left = 0.0
    small_segment_index = 0
    for index, component in enumerate(components):
        width = float(means[component])
        std = float(stds[component]) if stds[component] == stds[component] else 0.0
        ax.barh(
            y=0,
            width=width,
            left=left,
            height=0.5,
            label=component,
            color=colors[index % len(colors)],
            alpha=0.85,
        )
        if width > 0:
            label_text = f"{width:.0f} ms\n±{std:.0f}"
            if width < small_segment_threshold_ms:
                y_offset = small_segment_y_offsets[
                    small_segment_index % len(small_segment_y_offsets)
                ]
                ax.text(
                    left + width,
                    y_offset,
                    label_text,
                    ha="center",
                    va="bottom",
                    fontsize=std_label_fontsize,
                    color="black",
                )
                small_segment_index += 1
            else:
                ax.text(
                    left + width / 2,
                    0,
                    label_text,
                    ha="center",
                    va="center",
                    fontsize=std_label_fontsize,
                    color="black",
                )
        left += width

    ax.set_xlabel("Latency (ms)")
    ax.set_yticks([])
    ax.set_title(title)
    ax.set_ylim(-0.28, 0.62)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.35), ncol=4, frameon=False)


def write_summary_file(
    *,
    path: Path,
    mean_total: float,
    means: Any,
    grouped_means: Any,
    contrib_series: Any,
) -> None:
    top_means = means.sort_values(ascending=False).head()
    top_grouped = grouped_means.sort_values(ascending=False).head()
    top_variance = contrib_series.sort_values(ascending=False).head()

    lines = [
        f"Mean total latency: {mean_total}",
        "",
        "Top mean contributors:",
        top_means.to_string(),
        "",
        "Top grouped mean contributors:",
        top_grouped.to_string(),
        "",
        "Top variance contributors:",
        top_variance.to_string(),
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    pd, np, plt = require_plotting_deps()

    input_csv = get_input_csv()
    output_dir = get_output_dir(input_csv)
    output_dir.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(input_csv)
    validate_components_present(
        df, RAW_COMPONENTS + ["total_turn_ms", "has_partition_error"]
    )

    df = df[df["total_turn_ms"].notna() & (~df["has_partition_error"])].copy()
    if df.empty:
        raise RuntimeError("No valid partition rows remained after filtering.")

    df = build_grouped_columns(df)

    means = df[RAW_COMPONENTS].mean()
    stds = df[RAW_COMPONENTS].std()
    grouped_means = df[GROUPED_COMPONENTS].mean()
    grouped_stds = df[GROUPED_COMPONENTS].std()
    mean_total = float(df["total_turn_ms"].mean())

    if abs(float(means.sum()) - mean_total) >= 5:
        raise RuntimeError(
            "Mean partition components do not sum to mean total_turn_ms within tolerance."
        )

    contrib_series = pd.Series(compute_variance_contributions(df, np, RAW_COMPONENTS))

    colors = list(plt.cm.tab20.colors)

    fig, ax = plt.subplots(figsize=SCRIPT_CONFIG["timeline_figsize"])
    plot_stacked_timeline(
        ax=ax,
        means=means,
        stds=stds,
        components=RAW_COMPONENTS,
        colors=colors,
        title="Mean Latency Decomposition",
        std_label_fontsize=SCRIPT_CONFIG["std_label_fontsize"],
        small_segment_threshold_ms=SCRIPT_CONFIG["small_segment_threshold_ms"],
        small_segment_y_offsets=SCRIPT_CONFIG["small_segment_y_offsets"],
    )
    fig.tight_layout()
    fig.savefig(
        output_dir / "latency_timeline_mean_std.png", dpi=150, bbox_inches="tight"
    )
    plt.close(fig)

    fig, ax = plt.subplots(figsize=SCRIPT_CONFIG["grouped_figsize"])
    plot_stacked_timeline(
        ax=ax,
        means=grouped_means,
        stds=grouped_stds,
        components=GROUPED_COMPONENTS,
        colors=colors,
        title="Grouped Mean Latency Decomposition",
        std_label_fontsize=SCRIPT_CONFIG["std_label_fontsize"],
        small_segment_threshold_ms=SCRIPT_CONFIG["small_segment_threshold_ms"],
        small_segment_y_offsets=SCRIPT_CONFIG["small_segment_y_offsets"],
    )
    fig.tight_layout()
    fig.savefig(
        output_dir / "latency_timeline_grouped.png", dpi=150, bbox_inches="tight"
    )
    plt.close(fig)

    fig, ax = plt.subplots(figsize=SCRIPT_CONFIG["variance_figsize"])
    contrib_series.sort_values().plot(
        kind="barh",
        ax=ax,
        title="Contribution to Latency Variability (Covariance-based)",
        color="#4C78A8",
    )
    ax.set_xlabel("Fraction of Variance Explained")
    fig.tight_layout()
    fig.savefig(
        output_dir / "latency_variance_contribution.png",
        dpi=150,
        bbox_inches="tight",
    )
    plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=SCRIPT_CONFIG["hist_figsize"])
    df["total_turn_ms"].plot(kind="hist", bins=SCRIPT_CONFIG["hist_bins"], ax=axes[0])
    axes[0].set_title("Total Turn Latency")
    axes[0].set_xlabel("Latency (ms)")
    df["llm_total"].plot(kind="hist", bins=SCRIPT_CONFIG["hist_bins"], ax=axes[1])
    axes[1].set_title("LLM Total Latency")
    axes[1].set_xlabel("Latency (ms)")
    fig.tight_layout()
    fig.savefig(output_dir / "latency_histograms.png", dpi=150, bbox_inches="tight")
    plt.close(fig)

    write_summary_file(
        path=output_dir / "latency_summary.txt",
        mean_total=mean_total,
        means=means,
        grouped_means=grouped_means,
        contrib_series=contrib_series,
    )

    print(f"Mean total latency: {mean_total}")
    print("\nTop mean contributors:")
    print(means.sort_values(ascending=False).head())
    print("\nTop variance contributors:")
    print(contrib_series.sort_values(ascending=False).head())
    print(f"\nWrote plots and summary to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
