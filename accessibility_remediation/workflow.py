"""Local sample-processing loop for the table remediation prototype."""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from .tables import FieldFixResult, TableFixResult, apply_fix, scan_table, validate_cell_values


@dataclass(frozen=True)
class WorkflowSummary:
    samples_processed: int
    tables_processed: int
    tables_staged: int
    tables_for_review: int
    sample_errors: int
    output_dir: Path


def _caption_ai_placeholder(table_html: str) -> dict[str, str | None]:
    """Represent the future caption-writing step without inventing a caption."""
    del table_html
    return {
        "status": "placeholder",
        "draft": None,
        "message": "AI caption drafting is not implemented; human review is required.",
    }


def _table_fragments(source: str) -> list[str]:
    soup = BeautifulSoup(source, "html5lib")
    return [str(table) for table in soup.find_all("table")]


def _safe_stem(path: Path) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", path.stem).strip("._") or "sample"


def _clear_previous_staging(staging_dir: Path) -> None:
    manifest_path = staging_dir / ".agent-loop-manifest.json"
    if not manifest_path.exists():
        return
    previous_files = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(previous_files, list):
        raise ValueError(f"Invalid staging manifest: {manifest_path}")
    for filename in previous_files:
        if (
            not isinstance(filename, str)
            or Path(filename).name != filename
            or not re.fullmatch(r"\d{3}_[A-Za-z0-9._-]+_table_\d+\.html", filename)
        ):
            raise ValueError(f"Invalid staging entry in {manifest_path}: {filename!r}")
        staged_path = staging_dir / filename
        if staged_path.is_file() or staged_path.is_symlink():
            staged_path.unlink()


def _render_report(
    records: list[dict[str, Any]],
    samples_processed: int,
    tables_processed: int,
    tables_staged: int,
    tables_for_review: int,
) -> str:
    sections: list[str] = []
    for record in records:
        sample = html.escape(str(record["sample"]))
        table_index = record.get("table_index")
        heading = f"{sample} — table {table_index}" if table_index else sample
        before = html.escape(str(record.get("before_html", "")))
        after = html.escape(str(record.get("after_html", "")))
        status = html.escape(str(record["status"]))
        reasons = record.get("reasons", [])
        reason_markup = (
            "<ul>" + "".join(f"<li>{html.escape(str(reason))}</li>" for reason in reasons) + "</ul>"
            if reasons else "<p>None</p>"
        )
        caption = record.get("caption_step")
        caption_markup = ""
        if caption:
            caption_markup = (
                "<h4>Caption AI step</h4><p>"
                f"{html.escape(str(caption['status']))}: "
                f"{html.escape(str(caption['message']))}</p>"
            )
        sections.append(
            f"<section><h2>{html.escape(heading)}</h2>"
            f"<p>Status: <strong>{status}</strong></p>"
            f"<h3>Review reasons</h3>{reason_markup}{caption_markup}"
            f"<div class=\"comparison\"><div><h3>Before</h3><pre><code>{before}</code></pre></div>"
            f"<div><h3>After</h3><pre><code>{after}</code></pre></div></div></section>"
        )
    body = "\n".join(sections) or "<p>No HTML samples were found.</p>"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Table remediation report</title>
<style>
body {{ font: 16px/1.5 system-ui, sans-serif; margin: 2rem auto; max-width: 1100px; padding: 0 1rem; }}
section {{ border-top: 1px solid #bbb; margin-top: 2rem; padding-top: 1rem; }}
.comparison {{ display: grid; gap: 1rem; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); }}
pre {{ background: #f4f4f4; overflow-wrap: anywhere; padding: 1rem; white-space: pre-wrap; }}
</style>
</head>
<body>
<h1>Table remediation report</h1>
<p>Samples processed: {samples_processed}; tables processed: {tables_processed};
tables staged: {tables_staged}; tables for review: {tables_for_review}.</p>
{body}
</body>
</html>
"""


def run_workflow(
    samples_dir: str | Path,
    output_dir: str | Path = "workflow_output",
) -> WorkflowSummary:
    """Fetch each local sample, scan, fix, validate, and route its tables."""
    source_dir = Path(samples_dir)
    destination = Path(output_dir)
    if not source_dir.is_dir():
        raise FileNotFoundError(f"Sample directory does not exist: {source_dir}")
    sample_paths = sorted(source_dir.glob("*.html"))
    if not sample_paths:
        raise ValueError(f"No .html sample files found in {source_dir}")

    staging_dir = destination / "staging"
    staging_dir.mkdir(parents=True, exist_ok=True)

    review_records: list[dict[str, Any]] = []
    report_records: list[dict[str, Any]] = []
    staged_outputs: list[tuple[str, str]] = []
    tables_processed = 0
    tables_staged = 0
    tables_for_review = 0
    sample_errors = 0

    for sample_number, sample_path in enumerate(sample_paths, start=1):
        original_html = sample_path.read_text(encoding="utf-8")
        scanned_issues = scan_table(original_html)
        before_tables = _table_fragments(original_html)
        try:
            result: FieldFixResult = apply_fix(original_html)
            validate_cell_values(original_html, result.html)
        except ValueError as error:
            sample_errors += 1
            tables_processed += max(len(before_tables), 1)
            for index, before_table in enumerate(before_tables, start=1):
                review = {
                    "sample": sample_path.name,
                    "table_index": index,
                    "status": "review",
                    "reasons": [f"fix-or-validation-failed: {error}"],
                    "issues_before": scanned_issues,
                    "issues_after": scanned_issues,
                    "caption_step": None,
                    "before_html": before_table,
                    "after_html": before_table,
                }
                review_records.append(review)
                report_records.append(review)
                tables_for_review += 1
            if not before_tables:
                record = {
                    "sample": sample_path.name,
                    "table_index": None,
                    "status": "review",
                    "reasons": [f"fix-or-validation-failed: {error}"],
                    "issues_before": scanned_issues,
                    "issues_after": scanned_issues,
                    "caption_step": None,
                    "before_html": original_html,
                    "after_html": original_html,
                }
                review_records.append(record)
                report_records.append(record)
                tables_for_review += 1
            continue

        if not result.tables:
            record = {
                "sample": sample_path.name,
                "table_index": None,
                "status": "review",
                "confidence": result.confidence,
                "fixed": False,
                "issues_before": scanned_issues,
                "issues_after": scanned_issues,
                "reasons": list(result.review_reasons),
                "caption_step": None,
                "before_html": original_html,
                "after_html": result.html,
            }
            report_records.append(record)
            review_records.append(record)
            tables_for_review += 1
            continue

        after_tables = _table_fragments(result.html)
        for index, outcome in enumerate(result.tables, start=1):
            tables_processed += 1
            before_table = before_tables[index - 1] if index <= len(before_tables) else ""
            after_table = after_tables[index - 1] if index <= len(after_tables) else before_table
            reasons = list(outcome.review_reasons)
            caption_step = None
            if "missing-caption" in outcome.issues_before:
                caption_step = _caption_ai_placeholder(before_table)
                reasons.append("caption-ai-placeholder: " + str(caption_step["message"]))

            needs_review = outcome.needs_review or caption_step is not None
            status = "review" if needs_review else "staged"
            record = {
                "sample": sample_path.name,
                "table_index": index,
                "status": status,
                "confidence": outcome.confidence,
                "fixed": outcome.fixed,
                "issues_before": list(outcome.issues_before),
                "issues_after": list(outcome.issues_after),
                "reasons": reasons,
                "caption_step": caption_step,
                "before_html": before_table,
                "after_html": after_table,
            }
            report_records.append(record)
            if needs_review:
                review_records.append(record)
                tables_for_review += 1
            else:
                filename = f"{sample_number:03d}_{_safe_stem(sample_path)}_table_{index}.html"
                staged_outputs.append((filename, after_table + "\n"))
                tables_staged += 1

    _clear_previous_staging(staging_dir)
    for filename, content in staged_outputs:
        (staging_dir / filename).write_text(content, encoding="utf-8")
    (staging_dir / ".agent-loop-manifest.json").write_text(
        json.dumps([filename for filename, _ in staged_outputs], indent=2) + "\n",
        encoding="utf-8",
    )
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "review.json").write_text(
        json.dumps(review_records, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (destination / "report.html").write_text(
        _render_report(
            report_records,
            samples_processed=len(sample_paths),
            tables_processed=tables_processed,
            tables_staged=tables_staged,
            tables_for_review=tables_for_review,
        ),
        encoding="utf-8",
    )
    return WorkflowSummary(
        samples_processed=len(sample_paths),
        tables_processed=tables_processed,
        tables_staged=tables_staged,
        tables_for_review=tables_for_review,
        sample_errors=sample_errors,
        output_dir=destination,
    )
