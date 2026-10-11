"""Local sample-processing and human-review loop for the table prototype."""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup, Comment, Tag

from .tables import FieldFixResult, TableFixResult, apply_fix, scan_table, validate_cell_values

_PREVIEW_TAGS = {
    "table", "caption", "thead", "tbody", "tfoot", "tr", "th", "td",
    "p", "br", "strong", "em",
}
_PREVIEW_ATTRIBUTES = {"scope", "colspan", "rowspan", "headers", "id"}
_DANGEROUS_TAGS = {"script", "style", "iframe", "object", "embed", "svg", "math", "template"}
_DECISIONS = {"approve", "reject", "manual"}


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


def _safe_stem(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_") or "field"


def _read_text_exact(path: Path) -> str:
    with path.open(encoding="utf-8", newline="") as source:
        return source.read()


def _write_json(path: Path, value: Any) -> None:
    _write_text(path, json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def _write_text(path: Path, content: str) -> None:
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError(f"Refusing to write through a symlink: {path}")
    path.write_text(content, encoding="utf-8")


def _ensure_output_directory(path: Path) -> None:
    if path.is_symlink():
        raise ValueError(f"Refusing to use a symlink as an output directory: {path}")
    path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir():
        raise ValueError(f"Output path is not a directory: {path}")


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _sanitize_preview(fragment: str) -> str:
    soup = BeautifulSoup(fragment, "html5lib")
    for comment in soup.find_all(string=lambda value: isinstance(value, Comment)):
        comment.extract()
    for tag in list(soup.find_all(True)):
        if tag.parent is None or tag.name is None:
            continue
        if tag.name in _DANGEROUS_TAGS:
            tag.decompose()
        elif tag.name not in _PREVIEW_TAGS:
            tag.unwrap()
        else:
            for attribute in list(tag.attrs):
                if attribute not in _PREVIEW_ATTRIBUTES:
                    del tag.attrs[attribute]
    return soup.body.decode_contents() if soup.body else str(soup)


def _table_outcomes(
    original_html: str,
    result: FieldFixResult,
) -> list[dict[str, Any]]:
    before_tables = _table_fragments(original_html)
    after_tables = _table_fragments(result.html)
    outcomes: list[dict[str, Any]] = []
    for index, outcome in enumerate(result.tables, start=1):
        before_view = before_tables[index - 1] if index <= len(before_tables) else ""
        after_view = after_tables[index - 1] if index <= len(after_tables) else before_view
        reasons = list(outcome.review_reasons)
        caption_step = None
        if "missing-caption" in outcome.issues_before:
            caption_step = _caption_ai_placeholder(before_view)
            reasons.append("caption-ai-placeholder: " + str(caption_step["message"]))
        outcomes.append({
            "table": index,
            "confidence": outcome.confidence,
            "fixed": outcome.fixed,
            "issues_before": list(outcome.issues_before),
            "issues_after": list(outcome.issues_after),
            "review_reasons": reasons,
            "caption_step": caption_step,
            "parsed_before_view": before_view,
            "parsed_after_view": after_view,
        })
    return outcomes


def _evaluation_error(
    field: str,
    original_html: str,
    message: str,
) -> dict[str, Any]:
    parsed = _table_fragments(original_html)
    return {
        "field": field,
        "source_file": f"{field}.html",
        "status": "review",
        "confidence": 0.0,
        "attempt": 1,
        "decision": None,
        "note": "",
        "original_html": original_html,
        "fixed_html": original_html,
        "table_reviews": [
            {
                "table": index,
                "confidence": 0.0,
                "fixed": False,
                "issues_before": scan_table(fragment),
                "issues_after": scan_table(fragment),
                "review_reasons": [message],
                "caption_step": None,
                "parsed_before_view": fragment,
                "parsed_after_view": fragment,
            }
            for index, fragment in enumerate(parsed, start=1)
        ] or [{
            "table": None,
            "confidence": 0.0,
            "fixed": False,
            "issues_before": scan_table(original_html),
            "issues_after": scan_table(original_html),
            "review_reasons": [message],
            "caption_step": None,
            "parsed_before_view": "",
            "parsed_after_view": "",
        }],
        "feedback_notes": [],
        "errors": [message],
    }


def _evaluate_field(field: str, original_html: str) -> dict[str, Any]:
    try:
        result: FieldFixResult = apply_fix(original_html)
        validate_cell_values(original_html, result.html)
    except ValueError as error:
        return _evaluation_error(field, original_html, f"fix-or-validation-failed: {error}")

    table_reviews = _table_outcomes(original_html, result)
    reasons = [
        reason
        for table in table_reviews
        for reason in table["review_reasons"]
    ]
    if not result.tables:
        reasons.extend(result.review_reasons)
        if not reasons:
            reasons.append("no-table-found")
        table_reviews.append({
            "table": None,
            "confidence": result.confidence,
            "fixed": False,
            "issues_before": scan_table(original_html),
            "issues_after": scan_table(original_html),
            "review_reasons": reasons,
            "caption_step": None,
            "parsed_before_view": "",
            "parsed_after_view": "",
        })

    return {
        "field": field,
        "source_file": f"{field}.html",
        "status": "review" if reasons else "staged",
        "confidence": result.confidence,
        "attempt": 1,
        "decision": None,
        "note": "",
        "original_html": original_html,
        "fixed_html": result.html,
        "table_reviews": table_reviews,
        "feedback_notes": [],
        "errors": [],
    }


def _decision_defaults(field: str) -> dict[str, Any]:
    return {
        "field": field,
        "decision": None,
        "note": "",
        "attempt": 1,
    }


def _decision_identity(decision: dict[str, Any]) -> dict[str, Any] | None:
    previous = decision.get("last_applied")
    return previous if isinstance(previous, dict) else None


def _already_applied(decision: dict[str, Any]) -> bool:
    previous = _decision_identity(decision)
    return bool(
        previous
        and previous.get("decision") == decision.get("decision")
        and previous.get("note") == decision.get("note")
        and previous.get("attempt") == decision.get("attempt")
    )


def _mark_applied(decision: dict[str, Any]) -> None:
    decision["last_applied"] = {
        "decision": decision.get("decision"),
        "note": decision.get("note", ""),
        "attempt": decision.get("attempt", 1),
    }


def _staging_manifest(staging_dir: Path) -> tuple[list[str], set[str]]:
    manifest = staging_dir / ".agent-loop-manifest.json"
    files = _read_json(manifest, [])
    if not isinstance(files, list) or any(
        not isinstance(filename, str)
        or not re.fullmatch(r"[A-Za-z0-9_-]+\.html", filename)
        for filename in files
    ):
        raise ValueError(f"Invalid staging manifest: {manifest}")
    return files, set(files)


def _stage_field(staging_dir: Path, field: str, fixed_html: str) -> str:
    filename = f"{_safe_stem(field)}.html"
    files, file_set = _staging_manifest(staging_dir)
    staged_path = staging_dir / filename
    if staged_path.is_symlink():
        raise ValueError(f"Refusing to write through a symlink: {staged_path}")
    _write_text(staged_path, fixed_html)
    if filename not in file_set:
        files.append(filename)
    _write_json(staging_dir / ".agent-loop-manifest.json", files)
    return filename


def _rules_are_approval_ready(record: dict[str, Any]) -> bool:
    table_reviews = [
        table for table in record.get("table_reviews", [])
        if table.get("table") is not None
    ]
    return bool(table_reviews) and all(
        table.get("fixed") is True
        and not table.get("issues_after")
        and not table.get("review_reasons")
        for table in table_reviews
    ) and not scan_table(record["fixed_html"])


def _approved_html(
    output_dir: Path,
    record: dict[str, Any],
) -> tuple[str, str]:
    if _rules_are_approval_ready(record):
        validate_cell_values(record["original_html"], record["fixed_html"])
        issues = scan_table(record["fixed_html"])
        if issues:
            raise ValueError(
                f"Cannot approve {record['field']}: rule output has unresolved issues: "
                f"{', '.join(issues)}."
            )
        return record["fixed_html"], "rules"

    field = record["field"]
    edits_path = output_dir / "edits" / f"{_safe_stem(field)}.html"
    if not edits_path.is_file() or edits_path.is_symlink():
        raise ValueError(
            f"Cannot approve {field}: the rules did not resolve every table; "
            f"provide reviewer-corrected HTML at {edits_path}."
        )
    edited_html = _read_text_exact(edits_path)
    validate_cell_values(record["original_html"], edited_html)
    issues = scan_table(edited_html)
    if issues:
        raise ValueError(
            f"Cannot approve {field}: reviewer edit has unresolved issues: "
            f"{', '.join(issues)}."
        )
    return edited_html, "reviewer_edit"


def _set_approved_output(record: dict[str, Any], content: str, source: str) -> None:
    record["fixed_html"] = content
    record["approval_source"] = source
    parsed_tables = _table_fragments(content)
    for index, table in enumerate(record.get("table_reviews", [])):
        if index < len(parsed_tables):
            table["parsed_after_view"] = parsed_tables[index]


def _unstage_field(staging_dir: Path, field: str) -> None:
    filename = f"{_safe_stem(field)}.html"
    files, file_set = _staging_manifest(staging_dir)
    if filename not in file_set:
        return
    path = staging_dir / filename
    if path.is_file() or path.is_symlink():
        path.unlink()
    _write_json(
        staging_dir / ".agent-loop-manifest.json",
        [item for item in files if item != filename],
    )


def _clear_staging(staging_dir: Path) -> None:
    files, _ = _staging_manifest(staging_dir)
    for filename in files:
        path = staging_dir / filename
        if path.is_file() or path.is_symlink():
            path.unlink()
    _write_json(staging_dir / ".agent-loop-manifest.json", [])


def _render_report(records: list[dict[str, Any]], table_count: int, staged: int) -> str:
    sections: list[str] = []
    for record in records:
        field = html.escape(str(record["field"]))
        status = html.escape(str(record.get("status", "review")))
        decision = html.escape(str(record.get("decision") or "pending"))
        attempt = html.escape(str(record.get("attempt", 1)))
        feedback_notes = record.get("feedback_notes", [])
        if feedback_notes:
            notes_markup = "<ul>" + "".join(
                f"<li>Attempt {html.escape(str(item.get('attempt', '')))}: "
                f"{html.escape(str(item.get('note', '')))}</li>"
                if isinstance(item, dict)
                else f"<li>{html.escape(str(item))}</li>"
                for item in feedback_notes
            ) + "</ul>"
        else:
            note = html.escape(str(record.get("note", ""))) or "None"
            notes_markup = f"<p>{note}</p>"
        original = str(record.get("original_html", ""))
        fixed = str(record.get("fixed_html", original))
        original_code = html.escape(original)
        fixed_code = html.escape(fixed)
        original_preview = _sanitize_preview(original)
        fixed_preview = _sanitize_preview(fixed)
        table_details: list[str] = []
        for table in record.get("table_reviews", []):
            table_index = html.escape(str(table.get("table")))
            reasons = table.get("review_reasons", [])
            reason_markup = (
                "<ul>" + "".join(f"<li>{html.escape(str(reason))}</li>" for reason in reasons) + "</ul>"
                if reasons else "<p>None</p>"
            )
            caption = table.get("caption_step")
            caption_markup = ""
            if caption:
                caption_markup = (
                    "<p>Caption step: "
                    f"{html.escape(str(caption['status']))} — "
                    f"{html.escape(str(caption['message']))}</p>"
                )
            before_view = str(table.get("parsed_before_view", ""))
            after_view = str(table.get("parsed_after_view", ""))
            table_details.append(
                f"<article><h3>Table {table_index} — parsed view (not original source)</h3>"
                f"<p>Review reasons:</p>{reason_markup}{caption_markup}"
                f"<div class=\"comparison\">"
                f"<div><h4>Before — parsed view</h4><div class=\"preview\">"
                f"{_sanitize_preview(before_view)}</div><pre><code>{html.escape(before_view)}</code></pre></div>"
                f"<div><h4>After — parsed view</h4><div class=\"preview\">"
                f"{_sanitize_preview(after_view)}</div><pre><code>{html.escape(after_view)}</code></pre></div>"
                f"</div></article>"
            )
        table_markup = "".join(table_details) or "<p>No tables found in this field.</p>"
        approval_source = record.get("approval_source")
        source_markup = (
            f"; approved HTML source: {html.escape(str(approval_source))}"
            if approval_source else ""
        )
        sections.append(
            f"<section><h2>Field {field}</h2>"
            f"<p>Status: <strong>{status}</strong>; decision: <strong>{decision}</strong>; "
            f"attempt: {attempt}{source_markup}</p><h3>Notes</h3>{notes_markup}"
            f"<div class=\"comparison\">"
            f"<div><h3>Before — original source (exact field text)</h3>"
            f"<div class=\"preview\">{original_preview}</div><pre><code>{original_code}</code></pre></div>"
            f"<div><h3>After — fixed field</h3>"
            f"<div class=\"preview\">{fixed_preview}</div><pre><code>{fixed_code}</code></pre></div>"
            f"</div>{table_markup}</section>"
        )
    body = "\n".join(sections) or "<p>No HTML samples were found.</p>"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Table remediation report</title>
<style>
body {{ font: 16px/1.5 system-ui, sans-serif; margin: 2rem auto; max-width: 1200px; padding: 0 1rem; }}
section, article {{ border-top: 1px solid #bbb; margin-top: 2rem; padding-top: 1rem; }}
.comparison {{ display: grid; gap: 1rem; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); }}
.preview {{ border: 1px solid #ddd; overflow-x: auto; padding: 1rem; }}
pre {{ background: #f4f4f4; overflow-wrap: anywhere; padding: 1rem; white-space: pre-wrap; }}
</style>
</head>
<body>
<h1>Table remediation report</h1>
<p>Fields: {len(records)}; tables: {table_count}; staged fields: {staged}.</p>
{body}
</body>
</html>
"""


def _write_report(
    output_dir: Path,
    records: list[dict[str, Any]],
    table_count: int | None = None,
) -> None:
    if table_count is None:
        table_count = sum(
            table.get("table") is not None
            for record in records
            for table in record.get("table_reviews", [])
        )
    staged = sum(record.get("status") in {"staged", "approved", "staged_after_retry"} for record in records)
    _write_text(output_dir / "report.html", _render_report(records, table_count, staged))


def _load_decisions(output_dir: Path) -> dict[str, dict[str, Any]]:
    raw = _read_json(output_dir / "decisions.json", [])
    if not isinstance(raw, list):
        raise ValueError("decisions.json must contain a list.")
    decisions: dict[str, dict[str, Any]] = {}
    for entry in raw:
        if not isinstance(entry, dict) or not isinstance(entry.get("field"), str):
            raise ValueError("Each decisions.json entry must have a string field.")
        decisions[entry["field"]] = {**_decision_defaults(entry["field"]), **entry}
    return decisions


def _prepare_decisions(
    review_records: list[dict[str, Any]],
    existing: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    for record in review_records:
        decision = existing.get(record["field"], _decision_defaults(record["field"])).copy()
        record["decision"] = decision.get("decision")
        record["note"] = decision.get("note", "")
        record["attempt"] = decision.get("attempt", 1)
        if _already_applied(decision) and decision.get("decision") == "approve":
            record["status"] = "approved"
        record["feedback_notes"] = list(record.get("feedback_notes", []))
        prepared.append(decision)
    return prepared


def run_workflow(
    samples_dir: str | Path,
    output_dir: str | Path = "workflow_output",
) -> WorkflowSummary:
    """Process each sample file as one code field and route whole fields."""
    source_dir = Path(samples_dir)
    destination = Path(output_dir)
    if not source_dir.is_dir():
        raise FileNotFoundError(f"Sample directory does not exist: {source_dir}")
    resolved_source = source_dir.resolve()
    resolved_destination = destination.resolve()
    if (
        resolved_source == resolved_destination
        or resolved_source.is_relative_to(resolved_destination)
        or resolved_destination.is_relative_to(resolved_source)
    ):
        raise ValueError("Sample and output directories must not overlap.")
    sample_paths = sorted(source_dir.glob("*.html"))
    if not sample_paths:
        raise ValueError(f"No .html sample files found in {source_dir}")

    _ensure_output_directory(destination)
    staging_dir = destination / "staging"
    _ensure_output_directory(staging_dir)
    existing_decisions = _load_decisions(destination)
    prior_reviews = _read_json(destination / "review.json", [])
    prior_by_field = {
        entry["field"]: entry
        for entry in prior_reviews
        if isinstance(entry, dict) and isinstance(entry.get("field"), str)
    } if isinstance(prior_reviews, list) else {}

    _clear_staging(staging_dir)
    review_records: list[dict[str, Any]] = []
    report_records: list[dict[str, Any]] = []
    tables_processed = 0
    tables_staged = 0
    tables_for_review = 0
    sample_errors = 0

    for sample_path in sample_paths:
        field = sample_path.stem
        original_html = _read_text_exact(sample_path)
        record = _evaluate_field(field, original_html)
        previous = prior_by_field.get(field, {})
        record["feedback_notes"] = list(previous.get("feedback_notes", []))
        actual_tables = [
            table for table in record.get("table_reviews", [])
            if table.get("table") is not None
        ]
        tables_processed += len(actual_tables)
        if record.get("errors"):
            sample_errors += 1

        needs_review = record["status"] == "review"
        if needs_review:
            tables_for_review += len(actual_tables)
            decision = existing_decisions.get(field)
            if decision:
                record["decision"] = decision.get("decision")
                record["note"] = decision.get("note", "")
                record["attempt"] = decision.get("attempt", 1)
                if _already_applied(decision) and decision.get("decision") == "approve":
                    approved_html, source = _approved_html(destination, record)
                    _stage_field(staging_dir, field, approved_html)
                    _set_approved_output(record, approved_html, source)
                    record["status"] = "approved"
                    tables_staged += 1
                elif (
                    _already_applied(decision)
                    and (
                        decision.get("decision") == "manual"
                        or (
                            decision.get("decision") == "reject"
                            and decision.get("attempt", 1) >= 2
                        )
                    )
                ):
                    _unstage_field(staging_dir, field)
                    record["status"] = "manual"
            review_records.append(record)
        else:
            _stage_field(staging_dir, field, record["fixed_html"])
            tables_staged += 1
        report_records.append(record)

    decisions_output = _prepare_decisions(review_records, existing_decisions)
    _write_json(destination / "review.json", review_records)
    _write_json(destination / "decisions.json", decisions_output)
    _write_json(destination / "fields.json", report_records)
    _write_report(destination, report_records, tables_processed)
    return WorkflowSummary(
        samples_processed=len(sample_paths),
        tables_processed=tables_processed,
        tables_staged=tables_staged,
        tables_for_review=tables_for_review,
        sample_errors=sample_errors,
        output_dir=destination,
    )


def _log_decision(
    output_dir: Path,
    field: str,
    decision: str,
    note: str,
    attempt: int,
    reviewer: str | None,
    approved_html_source: str | None = None,
) -> None:
    event: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "field": field,
        "decision": decision,
        "note": note,
        "attempt": attempt,
    }
    if reviewer:
        event["reviewer"] = reviewer
    if approved_html_source is not None:
        event["approved_html_source"] = approved_html_source
    log_path = output_dir / "review_log.jsonl"
    if log_path.is_symlink() or log_path.parent.is_symlink():
        raise ValueError(f"Refusing to write through a symlink: {log_path}")
    with log_path.open("a", encoding="utf-8") as log:
        log.write(json.dumps(event, ensure_ascii=False) + "\n")


def _decision_notes_for_field(output_dir: Path, field: str) -> list[dict[str, Any]]:
    log_path = output_dir / "review_log.jsonl"
    if not log_path.exists():
        return []
    notes: list[dict[str, Any]] = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        if event.get("field") == field and event.get("note"):
            notes.append({
                "attempt": event.get("attempt"),
                "note": event["note"],
                "reviewer": event.get("reviewer"),
            })
    return notes


def process_review_decisions(
    output_dir: str | Path = "workflow_output",
    reviewer: str | None = None,
) -> dict[str, int]:
    """Apply pending reviewer decisions to local review records and staging."""
    destination = Path(output_dir)
    _ensure_output_directory(destination)
    review_path = destination / "review.json"
    records = _read_json(review_path, None)
    if not isinstance(records, list):
        raise FileNotFoundError(f"No review.json found in {destination}")
    decisions = _load_decisions(destination)
    staging_dir = destination / "staging"
    _ensure_output_directory(staging_dir)
    staged = retried = queued = 0
    updated_records = {record["field"]: record for record in records}
    field_records_raw = _read_json(destination / "fields.json", records)
    if not isinstance(field_records_raw, list):
        raise ValueError("fields.json must contain a list.")
    all_field_records = {
        record["field"]: record
        for record in field_records_raw
        if isinstance(record, dict) and isinstance(record.get("field"), str)
    }
    for field, record in updated_records.items():
        all_field_records[field] = record

    for field, record in updated_records.items():
        decision_entry = decisions.get(field)
        if not decision_entry:
            continue
        choice = decision_entry.get("decision")
        note = decision_entry.get("note", "")
        attempt = decision_entry.get("attempt", 1)
        if choice is None or _already_applied(decision_entry):
            continue
        if choice not in _DECISIONS:
            raise ValueError(f"Invalid decision for {field}: choose approve, reject, or manual.")
        if not isinstance(note, str):
            raise ValueError(f"Decision note for {field} must be text.")
        if choice == "reject" and not note.strip():
            raise ValueError(f"Reject decision for {field} requires a non-empty note.")
        if not isinstance(attempt, int) or attempt < 1:
            raise ValueError(f"Attempt for {field} must be a positive integer.")

        if choice == "approve":
            original_html = record["original_html"]
            fixed_html, html_source = _approved_html(destination, record)
            validate_cell_values(original_html, fixed_html)
            issues = scan_table(fixed_html)
            if issues:
                raise ValueError(
                    f"Cannot approve {field}: approved HTML has unresolved issues: "
                    f"{', '.join(issues)}."
                )
            _stage_field(staging_dir, field, fixed_html)
            _set_approved_output(record, fixed_html, html_source)
            record["status"] = "approved"
            record["decision"] = "approve"
            record["note"] = note
            record["attempt"] = attempt
            _log_decision(
                destination,
                field,
                choice,
                note,
                attempt,
                reviewer,
                approved_html_source=html_source,
            )
            _mark_applied(decision_entry)
            all_field_records[field] = record
            staged += 1
        elif choice == "manual" or attempt >= 2:
            _unstage_field(staging_dir, field)
            _log_decision(destination, field, choice, note, attempt, reviewer)
            _mark_applied(decision_entry)
            record["status"] = "manual"
            record["decision"] = "manual" if choice == "manual" else "reject"
            record["note"] = note
            record["attempt"] = attempt
            queued += 1
            manual_queue = _read_json(destination / "manual_queue.json", [])
            manual_by_field = {
                entry["field"]: entry for entry in manual_queue
                if isinstance(entry, dict) and isinstance(entry.get("field"), str)
            } if isinstance(manual_queue, list) else {}
            event_notes = _decision_notes_for_field(destination, field)
            record["feedback_notes"] = event_notes
            manual_record = {
                **record,
                "notes": event_notes,
                "reasons": [
                    reason
                    for table in record.get("table_reviews", [])
                    for reason in table.get("review_reasons", [])
                ] + list(record.get("errors", [])),
            }
            manual_by_field[field] = manual_record
            _write_json(destination / "manual_queue.json", list(manual_by_field.values()))
            all_field_records[field] = record
        else:
            _unstage_field(staging_dir, field)
            _log_decision(destination, field, choice, note, attempt, reviewer)
            _mark_applied(decision_entry)
            original_html = record["original_html"]
            retried_record = _evaluate_field(field, original_html)
            feedback_notes = list(record.get("feedback_notes", []))
            feedback_notes.append({
                "attempt": attempt,
                "note": note,
                "reviewer": reviewer,
            })
            retried_record["feedback_notes"] = feedback_notes
            retried_record["attempt"] = 2
            retried_record["note"] = note
            retried_record["decision"] = None
            if retried_record["status"] == "staged":
                _stage_field(staging_dir, field, retried_record["fixed_html"])
                updated_records.pop(field, None)
                decisions.pop(field, None)
                retried_record["status"] = "staged_after_retry"
                all_field_records[field] = retried_record
                staged += 1
            else:
                updated_records[field] = retried_record
                all_field_records[field] = retried_record
                decision_entry["decision"] = None
                decision_entry["attempt"] = 2
                retried += 1

    updated = list(updated_records.values())
    _write_json(destination / "review.json", updated)
    _write_json(destination / "decisions.json", list(decisions.values()))
    all_fields = list(all_field_records.values())
    _write_json(destination / "fields.json", all_fields)
    _write_report(destination, all_fields)
    return {"staged": staged, "retried": retried, "manual": queued}
