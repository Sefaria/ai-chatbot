#!/usr/bin/env python3
"""Run the full latency analysis workflow for one Braintrust experiment.

This is the main entrypoint for latency analysis. It:

1. selects a Braintrust experiment by id, by name, or defaults to the latest
   `beta_baseline` experiment
2. exports trace rows, span rows, and partition rows into `latency/analysis/`
3. runs the partition plots
4. runs the textual-search retry analysis

The lower-level scripts remain available, but this script is the intended
one-command path.
"""

from __future__ import annotations

from pathlib import Path

import build_production_latency_dataframe as export_mod
import plot_latency_partition as partition_plot_mod
import plot_textual_search_retries as retry_plot_mod


SCRIPT_CONFIG = {
    "project": export_mod.DEFAULT_PROJECT,
    "experiment_name": None,
    "experiment_id": None,
    "output_dir": export_mod.DATA_DIR,
    "page_size": export_mod.DEFAULT_PAGE_SIZE,
    "root_id_batch_size": export_mod.DEFAULT_ROOT_ID_BATCH_SIZE,
    "max_btql_retries": 4,
    "retry_backoff_seconds": 2.0,
    "tool_analysis_window_ms": export_mod.TOOL_ANALYSIS_WINDOW_MS,
}


def configure_export_script() -> None:
    export_mod.SCRIPT_CONFIG.update(
        {
            "project": SCRIPT_CONFIG["project"],
            "experiment_name": SCRIPT_CONFIG["experiment_name"],
            "experiment_id": SCRIPT_CONFIG["experiment_id"],
            "output_dir": Path(SCRIPT_CONFIG["output_dir"]),
            "page_size": SCRIPT_CONFIG["page_size"],
            "root_id_batch_size": SCRIPT_CONFIG["root_id_batch_size"],
            "max_btql_retries": SCRIPT_CONFIG["max_btql_retries"],
            "retry_backoff_seconds": SCRIPT_CONFIG["retry_backoff_seconds"],
            "tool_analysis_window_ms": SCRIPT_CONFIG["tool_analysis_window_ms"],
        }
    )


def configure_plot_scripts(output_dir: Path) -> None:
    partition_csv = output_dir / "trace_latency_partition_rows.csv"
    partition_plot_mod.SCRIPT_CONFIG["input_csv"] = partition_csv
    partition_plot_mod.SCRIPT_CONFIG["output_dir"] = output_dir

    retry_plot_mod.SCRIPT_CONFIG["input_csv"] = partition_csv
    retry_plot_mod.SCRIPT_CONFIG["output_dir"] = output_dir


def main() -> int:
    configure_export_script()
    output_dir = export_mod.run_export()
    configure_plot_scripts(output_dir)
    partition_plot_mod.main()
    retry_plot_mod.main()
    print(f"\nFull latency analysis complete: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
