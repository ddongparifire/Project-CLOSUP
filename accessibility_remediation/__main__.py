"""Run the local sample remediation workflow."""

from __future__ import annotations

import argparse
from pathlib import Path

from .workflow import process_review_decisions, run_workflow


def main() -> None:
    default_samples = Path(__file__).parents[1] / "samples" / "legacy"
    parser = argparse.ArgumentParser(description="Local legacy-table remediation prototype.")
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
        help="Output root for staging/, review and report files.",
    )
    subparsers = parser.add_subparsers(dest="command")
    review_parser = subparsers.add_parser("review", help="Apply decisions from decisions.json.")
    review_parser.add_argument(
        "--output-dir",
        type=Path,
        default=argparse.SUPPRESS,
        help="Output root (default: workflow_output).",
    )
    review_parser.add_argument(
        "--reviewer",
        help="Optional reviewer name to include in the decision log.",
    )
    args = parser.parse_args()

    if args.command == "review":
        counts = process_review_decisions(args.output_dir, args.reviewer)
        print(
            f"Review decisions processed: {counts['staged']} staged, "
            f"{counts['retried']} retried, {counts['manual']} sent to manual queue."
        )
        failed_fields = counts.get("failed_fields", [])
        if failed_fields:
            print(f"Decisions failed for: {', '.join(failed_fields)}")
            raise SystemExit(1)
        return

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
