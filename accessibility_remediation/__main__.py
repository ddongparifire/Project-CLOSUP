"""Run the local sample remediation workflow."""

from __future__ import annotations

import argparse
from pathlib import Path

from .workflow import run_workflow


def main() -> None:
    default_samples = Path(__file__).parents[1] / "samples" / "legacy"
    parser = argparse.ArgumentParser(
        description="Process legacy HTML table samples into staging and review outputs."
    )
    parser.add_argument(
        "--samples-dir",
        type=Path,
        default=default_samples,
        help="Directory containing input .html samples (default: samples/legacy).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("workflow_output"),
        help="Output root for staging/, review.json, and report.html.",
    )
    args = parser.parse_args()

    summary = run_workflow(args.samples_dir, args.output_dir)
    print(
        f"Processed {summary.samples_processed} samples and "
        f"{summary.tables_processed} tables: {summary.tables_staged} staged, "
        f"{summary.tables_for_review} for review, "
        f"{summary.sample_errors} sample errors. "
        f"Outputs: {summary.output_dir}"
    )


if __name__ == "__main__":
    main()
