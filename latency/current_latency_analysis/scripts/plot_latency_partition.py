#!/usr/bin/env python3
"""Plot latency partition summaries from trace_latency_partition_rows.csv."""

from __future__ import annotations

from pathlib import Path
import textwrap
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = REPO_ROOT / "latency" / "current_latency_analysis" / "data"
PARTITION_FILENAME = "trace_latency_partition_rows.csv"
SPAN_FILENAME = "span_rows.csv"
SEFARIA_TOOL_NAMES = {
    "get_text",
    "text_search",
    "get_current_calendar",
    "english_semantic_search",
    "get_links_between_texts",
    "search_in_book",
    "search_in_dictionaries",
    "get_english_translations",
    "get_topic_details",
    "clarify_name_argument",
    "clarify_search_path_filter",
    "catalog_get_node",
    "catalog_get_children",
    "catalog_search",
    "catalog_query",
    "get_available_manuscripts",
    "get_manuscript_image",
}
LEGACY_PRODUCT_TOOL_NAMES = {
    "get_text_catalogue_info",
    "get_author_indexes",
    "get_text_or_category_shape",
}
SDK_INTERNAL_TOOL_NAMES = {
    "Agent",
    "ToolSearch",
    "Read",
    "Write",
    "Edit",
    "MultiEdit",
    "Glob",
    "Grep",
    "Bash",
    "WebFetch",
    "WebSearch",
    "Monitor",
}
RAW_GROUPED_COMPONENTS = [
    "guardrail_wall_ms",
    "router_wall_ms",
    "llm_planning_wall_ms",
    "llm_tool_analysis_wall_ms",
    "llm_final_generation_wall_ms",
    "llm_other_wall_ms",
    "llm_unknown_context_wall_ms",
    "tool_wall_ms",
    "overhead_total_wall_ms",
]
LLM_ONLY_COMPONENTS = [
    "llm_planning_wall_ms",
    "llm_tool_analysis_wall_ms",
    "llm_final_generation_wall_ms",
    "llm_other_wall_ms",
    "llm_unknown_context_wall_ms",
]
DISPLAY_LABELS = {
    "guardrail_wall_ms": "Guardrail",
    "router_wall_ms": "Router",
    "llm_planning_wall_ms": "LLM planning",
    "llm_tool_analysis_wall_ms": "LLM tool analysis",
    "llm_final_generation_wall_ms": "LLM final generation",
    "llm_other_wall_ms": "LLM other",
    "llm_unknown_context_wall_ms": "LLM unknown",
    "tool_wall_ms": "Tool execution",
    "overhead_total_wall_ms": "Overhead / gaps",
}
COMPONENT_EXPLANATIONS = {
    "guardrail_wall_ms": "Pre-turn safety / guardrail check before the main agent turn proceeds.",
    "router_wall_ms": "Request classification and route selection before main execution.",
    "llm_planning_wall_ms": "LLM work before tool use, typically deciding what to do or what tool to call first.",
    "llm_tool_analysis_wall_ms": "LLM work that appears to consume, interpret, or react to tool results.",
    "llm_final_generation_wall_ms": "LLM work likely spent producing the final user-facing answer.",
    "llm_other_wall_ms": "LLM work with enough context to classify as non-unknown, but not clearly planning, tool-analysis, or final-generation.",
    "llm_unknown_context_wall_ms": "LLM work whose role could not be confidently inferred from timing or metadata.",
    "tool_wall_ms": "Non-LLM tool runtime on the critical-path partition. If tool and LLM overlap, the overlap is assigned to LLM.",
    "overhead_total_wall_ms": "Agent overhead, other instrumented non-LLM work, and uninstrumented wall-clock gaps.",
}
SCRIPT_CONFIG = {
    "input_csv": None,
    "output_dir": None,
    "exclude_top_latency_quantile": 0.99,
    "sampled_timeline_count": 60,
    "sampled_timeline_above_mean_count": 60,
    "timeline_figsize": (14, 4),
    "llm_figsize": (12, 3.5),
    "variance_figsize": (9, 6),
    "sampled_timeline_width": 16,
    "sampled_timeline_row_height": 0.38,
    "sampled_timeline_min_height": 11,
    "sampled_timeline_max_height": 22,
    "small_segment_threshold_ms": 2500,
    "small_segment_y_offsets": [26, 44, 62, 80],
    "small_segment_x_offsets": [-18, 18, -30, 30],
    "explanation_fontsize": 9,
}


def require_plotting_deps():
    try:
        import matplotlib.pyplot as plt
        import numpy as np
        import pandas as pd
        from matplotlib.ticker import FuncFormatter
    except ImportError as exc:
        raise RuntimeError(
            "This script requires pandas, numpy, and matplotlib. "
            "Install them with: pip install pandas numpy matplotlib"
        ) from exc
    return pd, np, plt, FuncFormatter


def find_latest_partition_csv(data_dir: Path) -> Path:
    candidates = sorted(
        data_dir.glob(f"*/{PARTITION_FILENAME}"),
        key=lambda path: path.stat().st_mtime,
    )
    if not candidates:
        raise RuntimeError(f"Could not find {PARTITION_FILENAME} under {data_dir}")
    return candidates[-1]


def find_latest_span_csv(data_dir: Path) -> Path:
    candidates = sorted(
        data_dir.glob(f"*/{SPAN_FILENAME}"),
        key=lambda path: path.stat().st_mtime,
    )
    if not candidates:
        raise RuntimeError(f"Could not find {SPAN_FILENAME} under {data_dir}")
    return candidates[-1]


def get_input_csv() -> Path:
    configured = SCRIPT_CONFIG["input_csv"]
    return Path(configured) if configured else find_latest_partition_csv(DATA_DIR)


def get_output_dir(input_csv: Path) -> Path:
    configured = SCRIPT_CONFIG["output_dir"]
    return Path(configured) if configured else input_csv.parent


def get_span_csv(input_csv: Path) -> Path:
    sibling = input_csv.parent / SPAN_FILENAME
    if sibling.exists():
        return sibling
    return find_latest_span_csv(DATA_DIR)


def format_ms(value: float) -> str:
    seconds = value / 1000.0
    absolute = abs(seconds)
    if absolute >= 100:
        return f"{seconds:.0f}s"
    if absolute >= 10:
        return f"{seconds:.1f}s"
    return f"{seconds:.2f}s"


def format_axis_seconds(value: float, _pos: float | None = None) -> str:
    seconds = value / 1000.0
    if abs(seconds) >= 100:
        return f"{seconds:.0f}s"
    if abs(seconds) >= 10:
        return f"{seconds:.1f}s"
    return f"{seconds:.2f}s"


def format_share(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.0f}%"


def format_chars(value: float) -> str:
    absolute = abs(value)
    if absolute >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if absolute >= 1_000:
        return f"{value / 1_000:.1f}k"
    return f"{value:.0f}"


def classify_tool_name(tool_name: str) -> str:
    if tool_name in SEFARIA_TOOL_NAMES:
        return "Sefaria tool"
    if tool_name in LEGACY_PRODUCT_TOOL_NAMES:
        return "Legacy product tool"
    if tool_name in SDK_INTERNAL_TOOL_NAMES:
        return "SDK/internal"
    return "Unclassified"


def validate_columns(df: Any, columns: list[str]) -> None:
    missing = [column for column in columns if column not in df.columns]
    if missing:
        raise RuntimeError(f"Missing expected columns: {missing}")


def add_plot_group_columns(df: Any) -> Any:
    df = df.copy()
    return df


def apply_latency_outlier_filter(df: Any) -> tuple[Any, float | None]:
    quantile = SCRIPT_CONFIG["exclude_top_latency_quantile"]
    if quantile is None:
        return df.copy(), None
    cutoff = float(df["total_turn_ms"].quantile(quantile))
    filtered = df[df["total_turn_ms"] <= cutoff].copy()
    return filtered, cutoff


def compute_variance_contributions(df: Any, np: Any, components: list[str]):
    total = df["total_turn_ms"]
    total_variance = np.var(total, ddof=1)
    if total_variance == 0:
        return {component: 0.0 for component in components}
    contributions: dict[str, float] = {}
    for component in components:
        covariance = np.cov(df[component], total, ddof=1)[0, 1]
        contributions[component] = float(covariance / total_variance)
    return contributions


def annotate_segment(
    *,
    ax: Any,
    left: float,
    width: float,
    y_center: float,
    mean_value: float,
    std_value: float,
    share_value: float | None,
    small_segment_threshold_ms: float,
    small_segment_y_offsets: list[float],
    small_segment_x_offsets: list[float],
    small_segment_index: int,
) -> int:
    label = f"{format_ms(mean_value)}\nσ {format_ms(std_value)}\n{format_share(share_value)}"
    if width < small_segment_threshold_ms:
        y_offset = small_segment_y_offsets[
            small_segment_index % len(small_segment_y_offsets)
        ]
        x_offset = small_segment_x_offsets[
            small_segment_index % len(small_segment_x_offsets)
        ]
        x_anchor = left + width / 2
        ax.annotate(
            label,
            xy=(x_anchor, y_center),
            xytext=(x_offset, y_offset),
            textcoords="offset points",
            ha="left" if x_offset >= 0 else "right",
            va="bottom",
            fontsize=8,
            color="black",
            arrowprops={"arrowstyle": "-", "lw": 0.8, "color": "#666666"},
            bbox={
                "boxstyle": "round,pad=0.2",
                "facecolor": "white",
                "edgecolor": "none",
                "alpha": 0.92,
            },
        )
        return small_segment_index + 1

    ax.text(
        left + width / 2,
        y_center,
        label,
        ha="center",
        va="center",
        fontsize=8,
        color="black",
    )
    return small_segment_index


def plot_stacked_mean_bar(
    *,
    ax: Any,
    means: Any,
    stds: Any,
    shares: dict[str, float | None],
    components: list[str],
    colors: list[Any],
    title: str,
    subtitle: str,
    footer: str,
    axis_formatter: Any,
    total_mean_ms: float,
    total_std_ms: float,
) -> None:
    left = 0.0
    small_segment_index = 0
    for index, component in enumerate(components):
        width = float(means[component])
        if width <= 0:
            continue
        std = float(stds[component]) if stds[component] == stds[component] else 0.0
        ax.barh(
            y=0,
            width=width,
            left=left,
            height=0.5,
            color=colors[index % len(colors)],
            alpha=0.9,
            label=DISPLAY_LABELS.get(component, component),
        )
        small_segment_index = annotate_segment(
            ax=ax,
            left=left,
            width=width,
            y_center=0,
            mean_value=width,
            std_value=std,
            share_value=shares.get(component),
            small_segment_threshold_ms=SCRIPT_CONFIG["small_segment_threshold_ms"],
            small_segment_y_offsets=SCRIPT_CONFIG["small_segment_y_offsets"],
            small_segment_x_offsets=SCRIPT_CONFIG["small_segment_x_offsets"],
            small_segment_index=small_segment_index,
        )
        left += width

    ax.set_yticks([])
    ax.set_xlabel("Latency (s)")
    ax.xaxis.set_major_formatter(axis_formatter)
    ax.set_title(title, fontsize=16, pad=26)
    ax.text(
        0.5,
        1.05,
        subtitle,
        transform=ax.transAxes,
        ha="center",
        va="bottom",
        fontsize=10,
        color="#444444",
    )
    ax.text(
        0.5,
        1.00,
        f"Total mean: {format_ms(total_mean_ms)}   σ: {format_ms(total_std_ms)}",
        transform=ax.transAxes,
        ha="center",
        va="bottom",
        fontsize=10,
        color="#222222",
        fontweight="bold",
    )
    ax.text(
        0.5,
        -0.14,
        footer,
        transform=ax.transAxes,
        ha="center",
        va="top",
        fontsize=9,
        color="#555555",
    )
    ax.set_ylim(-0.30, 0.92)

    line_y = -0.27
    line_step = 0.14
    label_x = 0.01
    description_x = 0.20
    for index, component in enumerate(components):
        label = DISPLAY_LABELS.get(component, component)
        explanation = COMPONENT_EXPLANATIONS.get(component, "")
        color = colors[index % len(colors)]
        ax.text(
            -0.02,
            line_y,
            "■",
            transform=ax.transAxes,
            ha="left",
            va="top",
            fontsize=13,
            color=color,
            fontweight="bold",
        )
        ax.text(
            label_x,
            line_y,
            f"{label}: ",
            transform=ax.transAxes,
            ha="left",
            va="top",
            fontsize=SCRIPT_CONFIG["explanation_fontsize"],
            color="#222222",
            fontweight="bold",
        )
        ax.text(
            description_x,
            line_y,
            textwrap.fill(explanation, width=112),
            transform=ax.transAxes,
            ha="left",
            va="top",
            fontsize=SCRIPT_CONFIG["explanation_fontsize"],
            color="#555555",
        )
        line_y -= line_step


def plot_sampled_timelines(
    df: Any,
    plt: Any,
    output_base_path: Path,
    axis_formatter: Any,
    *,
    sample_count: int,
    title: str,
    subtitle: str,
) -> None:
    sampled = df.sample(min(len(df), sample_count), random_state=7).copy()
    sampled = sampled.sort_values("total_turn_ms", ascending=True).reset_index(
        drop=True
    )
    colors = list(plt.cm.tab20.colors)
    figure_height = min(
        SCRIPT_CONFIG["sampled_timeline_max_height"],
        max(
            SCRIPT_CONFIG["sampled_timeline_min_height"],
            3.8 + SCRIPT_CONFIG["sampled_timeline_row_height"] * len(sampled),
        ),
    )
    fig, ax = plt.subplots(
        figsize=(SCRIPT_CONFIG["sampled_timeline_width"], figure_height)
    )

    for row_index, (_, row) in enumerate(sampled.iterrows()):
        left = 0.0
        for component_index, component in enumerate(RAW_GROUPED_COMPONENTS):
            width = float(row[component])
            if width <= 0:
                continue
            ax.barh(
                y=row_index,
                width=width,
                left=left,
                height=0.7,
                color=colors[component_index % len(colors)],
                alpha=0.85,
            )
            left += width

    ax.set_xlabel("Latency (s)")
    ax.xaxis.set_major_formatter(axis_formatter)
    ax.set_ylabel("Sampled traces")
    fig.suptitle(title, fontsize=16, y=0.952)
    ax.text(
        0.5,
        1.012,
        subtitle,
        transform=ax.transAxes,
        ha="center",
        va="bottom",
        fontsize=10,
        color="#444444",
    )
    ax.text(
        0.5,
        0.985,
        f"Trace count shown: {len(sampled)}",
        transform=ax.transAxes,
        ha="center",
        va="bottom",
        fontsize=9.5,
        color="#222222",
        fontweight="bold",
    )
    footer_lines = [
        ("Guardrail", DISPLAY_LABELS["guardrail_wall_ms"], colors[0]),
        ("Router", DISPLAY_LABELS["router_wall_ms"], colors[1]),
        ("LLM planning", DISPLAY_LABELS["llm_planning_wall_ms"], colors[2]),
        ("LLM tool analysis", DISPLAY_LABELS["llm_tool_analysis_wall_ms"], colors[3]),
        (
            "LLM final generation",
            DISPLAY_LABELS["llm_final_generation_wall_ms"],
            colors[4],
        ),
        ("LLM other", DISPLAY_LABELS["llm_other_wall_ms"], colors[5]),
        ("LLM unknown", DISPLAY_LABELS["llm_unknown_context_wall_ms"], colors[6]),
        ("Tool execution", DISPLAY_LABELS["tool_wall_ms"], colors[7]),
        ("Overhead / gaps", DISPLAY_LABELS["overhead_total_wall_ms"], colors[8]),
    ]
    x_positions = [0.02, 0.27, 0.52]
    y_positions = [-0.14, -0.20, -0.26]
    for idx, (_key, label, color) in enumerate(footer_lines):
        col = idx % 3
        row = idx // 3
        x = x_positions[col]
        y = y_positions[row]
        ax.text(
            x,
            y,
            "■",
            transform=ax.transAxes,
            ha="left",
            va="top",
            fontsize=12,
            color=color,
            fontweight="bold",
        )
        ax.text(
            x + 0.02,
            y,
            label,
            transform=ax.transAxes,
            ha="left",
            va="top",
            fontsize=9,
            color="#333333",
        )
    ax.text(
        0.02,
        -0.35,
        "Color dictionary: blue = guardrail/router, orange = LLM planning/tool analysis, green = LLM final generation/other, red = LLM unknown, pink = tool execution, purple = overhead or gaps.",
        transform=ax.transAxes,
        ha="left",
        va="top",
        fontsize=8.5,
        color="#555555",
    )
    fig.subplots_adjust(top=0.88, bottom=0.28, left=0.08, right=0.98)
    fig.savefig(output_base_path.with_suffix(".png"), dpi=150, bbox_inches="tight")
    fig.savefig(output_base_path.with_suffix(".pdf"), bbox_inches="tight")
    plt.close(fig)


def plot_tool_output_chars(
    *,
    span_df: Any,
    trace_df: Any,
    plt: Any,
    output_dir: Path,
) -> list[str]:
    tool_df = span_df[
        (span_df["span_category"] == "tool")
        & (span_df["within_root_window"])
        & span_df["span_name"].notna()
    ].copy()
    if tool_df.empty:
        return ["No valid tool spans found for tool output length plot."]

    tool_df["tool_output_chars"] = (
        tool_df["output_json"].fillna("").astype(str).map(len)
    )
    tool_df["tool_class"] = tool_df["span_name"].map(classify_tool_name)
    total_trace_count = int(trace_df["root_span_id"].nunique())
    summary = (
        tool_df.groupby(["span_name", "tool_class"], dropna=False)
        .agg(
            avg_output_chars=("tool_output_chars", "mean"),
            std_output_chars=("tool_output_chars", "std"),
            median_output_chars=("tool_output_chars", "median"),
            tool_call_count=("tool_output_chars", "size"),
            trace_count=("root_span_id", "nunique"),
        )
        .sort_values(["avg_output_chars", "tool_call_count"], ascending=[True, False])
    )
    summary["trace_share"] = summary["trace_count"] / total_trace_count
    summary["display_name"] = [
        (
            f"{span_name} [SDK/internal]"
            if tool_class == "SDK/internal"
            else (
                f"{span_name} [legacy]"
                if tool_class == "Legacy product tool"
                else (
                    f"{span_name} [unclassified]"
                    if tool_class == "Unclassified"
                    else str(span_name)
                )
            )
        )
        for span_name, tool_class in summary.index
    ]

    fig_height = max(4.5, 0.5 * len(summary) + 1.8)
    fig, ax = plt.subplots(figsize=(12, fig_height))
    bars = ax.barh(
        summary["display_name"],
        summary["avg_output_chars"],
        color=[
            "#E15759"
            if tool_class == "SDK/internal"
            else "#4C78A8"
            if tool_class == "Sefaria tool"
            else "#72B7B2"
            if tool_class == "Legacy product tool"
            else "#B07AA1"
            for _, tool_class in summary.index
        ],
        alpha=0.9,
    )
    ax.set_title("Average Tool Response Length", fontsize=16, pad=18)
    ax.text(
        0.5,
        0.995,
        "Each bar shows the average character length of the raw tool span output payload.",
        transform=ax.transAxes,
        ha="center",
        va="bottom",
        fontsize=10,
        color="#444444",
    )
    ax.text(
        0.5,
        0.965,
        "Labels mark SDK/internal and legacy tools explicitly. Counts are in characters, not tokens.",
        transform=ax.transAxes,
        ha="center",
        va="bottom",
        fontsize=9,
        color="#555555",
    )
    ax.set_xlabel("Average response length (chars)")
    ax.set_ylabel("Tool")

    x_max = float(summary["avg_output_chars"].max()) if not summary.empty else 0.0
    x_pad = max(50.0, x_max * 0.05)
    ax.set_xlim(0, x_max + x_pad * 6)

    for bar, ((tool_name, tool_class), row) in zip(
        bars, summary.iterrows(), strict=False
    ):
        std_value = (
            float(row["std_output_chars"])
            if row["std_output_chars"] == row["std_output_chars"]
            else 0.0
        )
        label = (
            f"{format_chars(float(row['avg_output_chars']))} chars avg\n"
            f"σ {format_chars(std_value)}  median {format_chars(float(row['median_output_chars']))}\n"
            f"called in {float(row['trace_share']) * 100:.0f}% of traces  "
            f"n={int(row['tool_call_count'])}"
        )
        ax.text(
            bar.get_width() + x_pad,
            bar.get_y() + bar.get_height() / 2,
            label,
            va="center",
            ha="left",
            fontsize=8.5,
            color="#333333",
        )

    ax.text(
        0.01,
        -0.13,
        "Color key: blue = current Sefaria tool, teal = legacy product tool, red = SDK/internal tool, purple = unclassified tool.",
        transform=ax.transAxes,
        ha="left",
        va="top",
        fontsize=8.5,
        color="#555555",
    )

    fig.tight_layout()
    png_path = output_dir / "tool_average_response_chars.png"
    pdf_path = output_dir / "tool_average_response_chars.pdf"
    fig.savefig(png_path, dpi=150, bbox_inches="tight")
    fig.savefig(pdf_path, bbox_inches="tight")
    plt.close(fig)

    return [
        "Tool response length summary (chars):",
        summary.sort_values("avg_output_chars", ascending=False).to_string(),
    ]


def write_summary_text(path: Path, summary_lines: list[str]) -> None:
    path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")


def main() -> int:
    pd, np, plt, FuncFormatter = require_plotting_deps()

    input_csv = get_input_csv()
    span_csv = get_span_csv(input_csv)
    output_dir = get_output_dir(input_csv)
    output_dir.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(input_csv)
    span_df = pd.read_csv(span_csv)
    validate_columns(
        df,
        RAW_GROUPED_COMPONENTS
        + LLM_ONLY_COMPONENTS
        + [
            "total_turn_ms",
            "partition_residual_ms",
            "llm_context_residual_ms",
            "has_partition_error",
            "has_llm_context_partition_error",
        ],
    )

    df = df[
        df["total_turn_ms"].notna()
        & (~df["has_partition_error"])
        & (~df["has_llm_context_partition_error"])
    ].copy()
    if df.empty:
        raise RuntimeError("No valid rows remained after filtering partition errors.")
    original_count = len(df)
    df, outlier_cutoff_ms = apply_latency_outlier_filter(df)
    if df.empty:
        raise RuntimeError("No valid rows remained after latency outlier filtering.")

    df = add_plot_group_columns(df)
    colors = list(plt.cm.tab20.colors)

    grouped_means = df[RAW_GROUPED_COMPONENTS].mean()
    grouped_stds = df[RAW_GROUPED_COMPONENTS].std()
    mean_total = float(df["total_turn_ms"].mean())
    std_total = float(df["total_turn_ms"].std())
    grouped_shares = {
        component: (float(grouped_means[component]) / mean_total)
        if mean_total > 0
        else None
        for component in RAW_GROUPED_COMPONENTS
    }

    fig, ax = plt.subplots(figsize=SCRIPT_CONFIG["timeline_figsize"])
    plot_stacked_mean_bar(
        ax=ax,
        means=grouped_means,
        stds=grouped_stds,
        shares=grouped_shares,
        components=RAW_GROUPED_COMPONENTS,
        colors=colors,
        title="Grouped Mean Latency Decomposition",
        subtitle=(
            "Segment width is empirical mean wall-clock contribution. σ is across-trace "
            "standard deviation. Components are mutually exclusive and sum to mean "
            "end-to-end latency."
            + (
                f" Top 1% highest-latency traces were excluded"
                f" (cutoff {format_ms(outlier_cutoff_ms)}; kept {len(df)}/{original_count} traces)."
                if outlier_cutoff_ms is not None
                else ""
            )
        ),
        footer="Labels show: mean latency in seconds, σ across traces in seconds, and share of mean total latency.",
        axis_formatter=FuncFormatter(format_axis_seconds),
        total_mean_ms=mean_total,
        total_std_ms=std_total,
    )
    fig.tight_layout()
    fig.savefig(
        output_dir / "grouped_mean_latency_decomposition.png",
        dpi=150,
        bbox_inches="tight",
    )
    plt.close(fig)

    llm_means = df[LLM_ONLY_COMPONENTS].mean()
    llm_stds = df[LLM_ONLY_COMPONENTS].std()
    llm_total_mean = float(llm_means.sum())
    llm_total_std = float(df["llm_context_total_wall_ms"].std())
    llm_shares = {
        component: (float(llm_means[component]) / llm_total_mean)
        if llm_total_mean > 0
        else None
        for component in LLM_ONLY_COMPONENTS
    }

    fig, ax = plt.subplots(figsize=SCRIPT_CONFIG["llm_figsize"])
    plot_stacked_mean_bar(
        ax=ax,
        means=llm_means,
        stds=llm_stds,
        shares=llm_shares,
        components=LLM_ONLY_COMPONENTS,
        colors=colors,
        title="LLM Context Mean Decomposition",
        subtitle=(
            "LLM time is classified by temporal relation to tool calls and/or metadata "
            "when available."
            + (
                f" Top 1% highest-latency traces were excluded"
                f" (cutoff {format_ms(outlier_cutoff_ms)})."
                if outlier_cutoff_ms is not None
                else ""
            )
        ),
        footer="Labels show: mean latency in seconds, σ across traces in seconds, and share of mean LLM latency.",
        axis_formatter=FuncFormatter(format_axis_seconds),
        total_mean_ms=llm_total_mean,
        total_std_ms=llm_total_std,
    )
    fig.tight_layout()
    fig.savefig(
        output_dir / "llm_context_mean_decomposition.png", dpi=150, bbox_inches="tight"
    )
    plt.close(fig)

    contrib_series = pd.Series(
        compute_variance_contributions(df, np, RAW_GROUPED_COMPONENTS)
    )
    fig, ax = plt.subplots(figsize=SCRIPT_CONFIG["variance_figsize"])
    contrib_series.sort_values().rename(index=DISPLAY_LABELS).plot(
        kind="barh",
        ax=ax,
        color="#4C78A8",
    )
    ax.set_title("Variance Contribution", fontsize=16, pad=16)
    ax.set_xlabel("Covariance / Var(total latency)")
    fig.tight_layout()
    fig.savefig(output_dir / "variance_contribution.png", dpi=150, bbox_inches="tight")
    plt.close(fig)

    median_cutoff = float(df["total_turn_ms"].quantile(0.50))
    median_plus_df = df[df["total_turn_ms"] > median_cutoff].copy()
    median_plus_means = median_plus_df[RAW_GROUPED_COMPONENTS].mean()
    median_plus_stds = median_plus_df[RAW_GROUPED_COMPONENTS].std()
    median_plus_total_mean = float(median_plus_df["total_turn_ms"].mean())
    median_plus_total_std = float(median_plus_df["total_turn_ms"].std())
    median_plus_shares = {
        component: (float(median_plus_means[component]) / median_plus_total_mean)
        if median_plus_total_mean > 0
        else None
        for component in RAW_GROUPED_COMPONENTS
    }

    fig, ax = plt.subplots(figsize=SCRIPT_CONFIG["timeline_figsize"])
    plot_stacked_mean_bar(
        ax=ax,
        means=median_plus_means,
        stds=median_plus_stds,
        shares=median_plus_shares,
        components=RAW_GROUPED_COMPONENTS,
        colors=colors,
        title="Above-Median Mean Decomposition",
        subtitle=(
            "Subset: traces with total latency above the median."
            + (
                f" Top 1% highest-latency traces were excluded first"
                f" (cutoff {format_ms(outlier_cutoff_ms)})."
                if outlier_cutoff_ms is not None
                else ""
            )
        ),
        footer="Labels show: mean latency in seconds, σ across above-median traces, and share of mean above-median latency.",
        axis_formatter=FuncFormatter(format_axis_seconds),
        total_mean_ms=median_plus_total_mean,
        total_std_ms=median_plus_total_std,
    )
    fig.tight_layout()
    fig.savefig(
        output_dir / "median_plus_mean_decomposition.png", dpi=150, bbox_inches="tight"
    )
    plt.close(fig)

    p90_cutoff = float(df["total_turn_ms"].quantile(0.90))
    slow_df = df[df["total_turn_ms"] >= p90_cutoff].copy()
    slow_means = slow_df[RAW_GROUPED_COMPONENTS].mean()
    slow_stds = slow_df[RAW_GROUPED_COMPONENTS].std()
    slow_total_mean = float(slow_df["total_turn_ms"].mean())
    slow_total_std = float(slow_df["total_turn_ms"].std())
    slow_shares = {
        component: (float(slow_means[component]) / slow_total_mean)
        if slow_total_mean > 0
        else None
        for component in RAW_GROUPED_COMPONENTS
    }

    fig, ax = plt.subplots(figsize=SCRIPT_CONFIG["timeline_figsize"])
    plot_stacked_mean_bar(
        ax=ax,
        means=slow_means,
        stds=slow_stds,
        shares=slow_shares,
        components=RAW_GROUPED_COMPONENTS,
        colors=colors,
        title="Slow-Trace Mean Decomposition",
        subtitle=(
            "Subset: traces with total latency at or above the p90 threshold."
            + (
                f" Top 1% highest-latency traces were excluded first"
                f" (cutoff {format_ms(outlier_cutoff_ms)})."
                if outlier_cutoff_ms is not None
                else ""
            )
        ),
        footer="Labels show: mean latency in seconds, σ across slow traces in seconds, and share of mean slow-trace latency.",
        axis_formatter=FuncFormatter(format_axis_seconds),
        total_mean_ms=slow_total_mean,
        total_std_ms=slow_total_std,
    )
    fig.tight_layout()
    fig.savefig(
        output_dir / "slow_trace_mean_decomposition.png", dpi=150, bbox_inches="tight"
    )
    plt.close(fig)

    plot_sampled_timelines(
        df,
        plt,
        output_dir / "optional_sampled_timelines",
        FuncFormatter(format_axis_seconds),
        sample_count=SCRIPT_CONFIG["sampled_timeline_count"],
        title="Sampled Trace Timelines",
        subtitle=(
            "Each row is one trace. Components are mutually exclusive wall-clock "
            "groups, sampled from all valid traces."
            + (
                f" Top 1% highest-latency traces were excluded first"
                f" (cutoff {format_ms(outlier_cutoff_ms)})."
                if outlier_cutoff_ms is not None
                else ""
            )
        ),
    )

    above_mean_df = df[df["total_turn_ms"] > mean_total].copy()
    if not above_mean_df.empty:
        plot_sampled_timelines(
            above_mean_df,
            plt,
            output_dir / "optional_sampled_timelines_above_mean",
            FuncFormatter(format_axis_seconds),
            sample_count=SCRIPT_CONFIG["sampled_timeline_above_mean_count"],
            title="Sampled Trace Timelines Above Mean",
            subtitle=(
                "Each row is one trace. Components are mutually exclusive wall-clock "
                "groups, sampled only from traces above the mean total latency."
                + (
                    f" Top 1% highest-latency traces were excluded first"
                    f" (cutoff {format_ms(outlier_cutoff_ms)})."
                    if outlier_cutoff_ms is not None
                    else ""
                )
            ),
        )

    tool_summary_lines = plot_tool_output_chars(
        span_df=span_df,
        trace_df=df,
        plt=plt,
        output_dir=output_dir,
    )

    summary_lines = [
        f"Trace count: {len(df)}",
        (
            f"Outlier filter: excluded traces above p99 total_turn_ms "
            f"(cutoff {outlier_cutoff_ms})"
            if outlier_cutoff_ms is not None
            else "Outlier filter: none"
        ),
        f"Mean total latency: {mean_total}",
        f"P50 / P90 / P95: "
        f"{np.quantile(df['total_turn_ms'], 0.50)}, "
        f"{np.quantile(df['total_turn_ms'], 0.90)}, "
        f"{np.quantile(df['total_turn_ms'], 0.95)}",
        f"Mean partition residual: {df['partition_residual_ms'].mean()}",
        f"Max abs partition residual: {df['partition_residual_ms'].abs().max()}",
        f"Mean LLM context residual: {df['llm_context_residual_ms'].mean()}",
        f"Max abs LLM context residual: {df['llm_context_residual_ms'].abs().max()}",
        f"Partition errors: {int(df['has_partition_error'].sum())}",
        f"LLM context partition errors: {int(df['has_llm_context_partition_error'].sum())}",
        "",
        "Top mean contributors:",
        grouped_means.sort_values(ascending=False)
        .rename(index=DISPLAY_LABELS)
        .to_string(),
        "",
        "Top variance contributors:",
        contrib_series.sort_values(ascending=False)
        .rename(index=DISPLAY_LABELS)
        .to_string(),
        "",
        "Mean LLM context breakdown:",
        llm_means.sort_values(ascending=False).rename(index=DISPLAY_LABELS).to_string(),
        "",
        "Above-median breakdown:",
        median_plus_means.sort_values(ascending=False)
        .rename(index=DISPLAY_LABELS)
        .to_string(),
        "",
        "Slow-trace breakdown:",
        slow_means.sort_values(ascending=False)
        .rename(index=DISPLAY_LABELS)
        .to_string(),
        "",
        *tool_summary_lines,
    ]
    write_summary_text(output_dir / "latency_summary.txt", summary_lines)

    print(f"Trace count: {len(df)}")
    if outlier_cutoff_ms is not None:
        print(
            "Outlier filter: excluded traces above p99 total_turn_ms "
            f"(cutoff {outlier_cutoff_ms})"
        )
    print(f"Mean total latency: {mean_total}")
    print(
        "P50 / P90 / P95: "
        f"{np.quantile(df['total_turn_ms'], 0.50)}, "
        f"{np.quantile(df['total_turn_ms'], 0.90)}, "
        f"{np.quantile(df['total_turn_ms'], 0.95)}"
    )
    print(f"Mean partition residual: {df['partition_residual_ms'].mean()}")
    print(f"Max abs partition residual: {df['partition_residual_ms'].abs().max()}")
    print(f"Mean LLM context residual: {df['llm_context_residual_ms'].mean()}")
    print(f"Max abs LLM context residual: {df['llm_context_residual_ms'].abs().max()}")
    print(f"Partition errors: {int(df['has_partition_error'].sum())}")
    print(
        f"LLM context partition errors: {int(df['has_llm_context_partition_error'].sum())}"
    )
    print("\nTop mean contributors:")
    print(
        grouped_means.sort_values(ascending=False).rename(index=DISPLAY_LABELS).head()
    )
    print("\nTop variance contributors:")
    print(
        contrib_series.sort_values(ascending=False).rename(index=DISPLAY_LABELS).head()
    )
    print("\nMean LLM context breakdown:")
    print(llm_means.sort_values(ascending=False).rename(index=DISPLAY_LABELS))
    print("\nAbove-median breakdown:")
    print(median_plus_means.sort_values(ascending=False).rename(index=DISPLAY_LABELS))
    print("\nSlow-trace breakdown:")
    print(slow_means.sort_values(ascending=False).rename(index=DISPLAY_LABELS))
    print("\n" + "\n".join(tool_summary_lines))
    print(f"\nWrote plots and summary to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
