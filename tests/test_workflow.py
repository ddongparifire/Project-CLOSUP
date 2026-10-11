import json
import html as html_module
import subprocess
import sys
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from accessibility_remediation.workflow import process_review_decisions, run_workflow


SAMPLES = Path(__file__).parents[1] / "samples" / "legacy"


def _load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write_decision(output_dir, field, decision, note="", attempt=1):
    path = output_dir / "decisions.json"
    entries = _load_json(path)
    for entry in entries:
        if entry["field"] == field:
            entry.update(decision=decision, note=note, attempt=attempt)
            break
    else:
        raise AssertionError(f"No decision entry for {field}")
    path.write_text(json.dumps(entries, indent=2), encoding="utf-8")


def _single_review_field(tmp_path):
    samples = tmp_path / "samples"
    samples.mkdir()
    source = (
        "<table><tr><td>Label</td><td>Value</td></tr>"
        "<tr><td>District</td><td>Lakeview</td></tr></table>"
    )
    (samples / "one_field.html").write_text(source)
    output = tmp_path / "workflow_output"
    run_workflow(samples, output)
    return samples, output, source


def test_workflow_stages_whole_passing_fields_and_writes_review_report(tmp_path):
    output_dir = tmp_path / "run"

    summary = run_workflow(SAMPLES, output_dir)

    assert summary.samples_processed == 11
    assert summary.tables_processed == 12
    assert summary.tables_staged == 2
    assert summary.tables_for_review == 10
    assert summary.sample_errors == 0
    staged_files = sorted((output_dir / "staging").glob("*.html"))
    assert [path.name for path in staged_files] == ["missing_scope.html", "missing_sections.html"]
    assert (output_dir / "staging" / "missing_scope.html").read_text().startswith("<table")

    review = _load_json(output_dir / "review.json")
    assert len(review) == 9
    missing_caption = next(record for record in review if record["field"] == "missing_caption")
    assert any(
        "missing-caption" in reason
        for table in missing_caption["table_reviews"]
        for reason in table["review_reasons"]
    )
    assert missing_caption["table_reviews"][0]["caption_step"]["status"] == "placeholder"

    report = (output_dir / "report.html").read_text()
    assert "Table remediation report" in report
    assert "Field missing_caption" in report
    assert "<h3>Before — original source (exact field text)</h3>" in report
    assert "<h4>Before — parsed view</h4>" in report
    assert "<h4>After — parsed view</h4>" in report
    assert "AI caption drafting is not implemented" in report


def test_raw_unclosed_source_is_preserved_in_report_and_review_json(tmp_path):
    original = (SAMPLES / "unclosed_tags.html").read_text()
    output_dir = tmp_path / "run"

    run_workflow(SAMPLES, output_dir)

    record = next(
        item for item in _load_json(output_dir / "review.json")
        if item["field"] == "unclosed_tags"
    )
    assert record["original_html"] == original
    report = (output_dir / "report.html").read_text()
    assert f"<code>{html_module.escape(original)}</code>" in report
    assert "District<td>Lakeview" in original
    assert "District&lt;td&gt;Lakeview" in report
    assert "<h3>Before — original source (exact field text)</h3>" in report

def test_mixed_field_with_surrounding_text_is_not_partially_staged(tmp_path):
    original = (SAMPLES / "mixed_field.html").read_text()
    output_dir = tmp_path / "run"

    run_workflow(SAMPLES, output_dir)

    assert not (output_dir / "staging" / "mixed_field.html").exists()
    record = next(
        item for item in _load_json(output_dir / "review.json")
        if item["field"] == "mixed_field"
    )
    assert record["original_html"] == original
    assert len(record["table_reviews"]) == 2
    assert record["table_reviews"][0]["review_reasons"] == []
    assert record["table_reviews"][1]["review_reasons"]
    assert "Field notes before the tables." in record["original_html"]
    assert "Footnote: the next table is an unlabeled list of values." in record["original_html"]
    assert "Field notes after the tables." in record["fixed_html"]


def test_workflow_routes_samples_without_tables_to_review(tmp_path):
    samples_dir = tmp_path / "samples"
    samples_dir.mkdir()
    (samples_dir / "empty.html").write_text("<p>No table here</p>")

    summary = run_workflow(samples_dir, tmp_path / "run")

    assert summary.samples_processed == 1
    assert summary.tables_processed == 0
    assert summary.tables_staged == 0
    assert summary.tables_for_review == 0
    review = _load_json(tmp_path / "run" / "review.json")
    assert len(review) == 1
    assert review[0]["table_reviews"][0]["review_reasons"] == ["no-table-found"]


def test_rendered_preview_is_sanitized_but_source_code_is_preserved(tmp_path):
    samples = tmp_path / "samples"
    samples.mkdir()
    source = (
        '<table onclick="bad()"><caption style="color:red">Example</caption>'
        '<thead><tr><th scope="col" onmouseover="bad()">Value</th></tr></thead>'
        '<tbody><tr><td><script>bad()</script><strong id="safe">42</strong>'
        '<img src=x onerror="bad()"></td></tr></tbody></table>'
    )
    (samples / "unsafe.html").write_text(source)
    output_dir = tmp_path / "run"

    run_workflow(samples, output_dir)

    report = BeautifulSoup((output_dir / "report.html").read_text(), "html.parser")
    previews = report.select(".preview")
    rendered_before = previews[0]
    assert rendered_before.find("table") is not None
    assert rendered_before.find("script") is None
    assert rendered_before.find("img") is None
    assert rendered_before.find("strong", id="safe") is not None
    for tag in rendered_before.find_all(True):
        assert set(tag.attrs) <= {"scope", "colspan", "rowspan", "headers", "id"}
        assert not any(name.lower().startswith("on") for name in tag.attrs)
    assert "&lt;script&gt;bad()&lt;/script&gt;" in (output_dir / "report.html").read_text()


def test_approve_stages_fixed_whole_field_and_marks_it_approved(tmp_path):
    _, output_dir, source = _single_review_field(tmp_path)
    record = _load_json(output_dir / "review.json")[0]
    fixed_html = record["fixed_html"]
    _write_decision(output_dir, "one_field", "approve", "Looks good")

    counts = process_review_decisions(output_dir, reviewer="A Reviewer")

    assert counts == {"staged": 1, "retried": 0, "manual": 0}
    assert (output_dir / "staging" / "one_field.html").read_text() == fixed_html
    updated = _load_json(output_dir / "review.json")[0]
    assert updated["status"] == "approved"
    assert updated["decision"] == "approve"
    assert updated["original_html"] == source
    event = json.loads((output_dir / "review_log.jsonl").read_text().splitlines()[0])
    assert event["field"] == "one_field"
    assert event["reviewer"] == "A Reviewer"
    assert event["note"] == "Looks good"
    assert event["timestamp"]


def test_approve_stages_the_complete_field_with_surrounding_text(tmp_path):
    samples = tmp_path / "samples"
    samples.mkdir()
    source = (
        "<p>Introductory note.</p>"
        "<table><tr><td>Label</td><td>Value</td></tr></table>"
        "<p>Footnote after the table.</p>"
    )
    (samples / "notes.html").write_text(source)
    output_dir = tmp_path / "workflow_output"
    run_workflow(samples, output_dir)
    _write_decision(output_dir, "notes", "approve", "Reviewed")

    process_review_decisions(output_dir)

    staged = (output_dir / "staging" / "notes.html").read_text()
    assert "Introductory note." in staged
    assert "Footnote after the table." in staged
    assert "<thead>" in staged


def test_first_reject_retries_once_and_attaches_feedback(tmp_path):
    _, output_dir, _ = _single_review_field(tmp_path)
    _write_decision(output_dir, "one_field", "reject", "Header meaning is unclear")

    counts = process_review_decisions(output_dir)

    assert counts == {"staged": 0, "retried": 1, "manual": 0}
    record = _load_json(output_dir / "review.json")[0]
    assert record["status"] == "review"
    assert record["attempt"] == 2
    assert record["feedback_notes"][0]["note"] == "Header meaning is unclear"
    assert "Header meaning is unclear" in (output_dir / "report.html").read_text()
    decision = _load_json(output_dir / "decisions.json")[0]
    assert decision["attempt"] == 2
    assert decision["decision"] is None
    assert not (output_dir / "staging" / "one_field.html").exists()


def test_second_reject_moves_field_to_manual_queue_with_notes_and_reasons(tmp_path):
    _, output_dir, _ = _single_review_field(tmp_path)
    _write_decision(output_dir, "one_field", "reject", "First review rejected")
    process_review_decisions(output_dir)
    _write_decision(output_dir, "one_field", "reject", "Retry still ambiguous", attempt=2)

    counts = process_review_decisions(output_dir)

    assert counts == {"staged": 0, "retried": 0, "manual": 1}
    manual = _load_json(output_dir / "manual_queue.json")[0]
    assert manual["field"] == "one_field"
    assert [note["note"] for note in manual["notes"]] == [
        "First review rejected",
        "Retry still ambiguous",
    ]
    assert manual["reasons"]
    report = (output_dir / "report.html").read_text()
    assert "First review rejected" in report
    assert "Retry still ambiguous" in report
    events = [
        json.loads(line)
        for line in (output_dir / "review_log.jsonl").read_text().splitlines()
    ]
    assert [event["attempt"] for event in events] == [1, 2]


def test_manual_decision_routes_field_to_manual_queue(tmp_path):
    _, output_dir, _ = _single_review_field(tmp_path)
    _write_decision(output_dir, "one_field", "manual", "Requires domain expertise")

    counts = process_review_decisions(output_dir)

    assert counts["manual"] == 1
    manual = _load_json(output_dir / "manual_queue.json")[0]
    assert "Requires domain expertise" in [note["note"] for note in manual["notes"]]


def test_manual_queue_stays_manual_when_loop_is_rerun(tmp_path):
    samples, output_dir, _ = _single_review_field(tmp_path)
    _write_decision(output_dir, "one_field", "manual", "Domain review required")
    process_review_decisions(output_dir)

    run_workflow(samples, output_dir)

    record = _load_json(output_dir / "review.json")[0]
    assert record["status"] == "manual"
    assert not (output_dir / "staging" / "one_field.html").exists()
    assert len(_load_json(output_dir / "manual_queue.json")) == 1


def test_reject_without_note_is_refused_before_logging(tmp_path):
    _, output_dir, _ = _single_review_field(tmp_path)
    _write_decision(output_dir, "one_field", "reject", " ")

    with pytest.raises(ValueError, match="requires a non-empty note"):
        process_review_decisions(output_dir)

    assert not (output_dir / "review_log.jsonl").exists()


def test_existing_pending_decisions_survive_workflow_rerun(tmp_path):
    samples, output_dir, _ = _single_review_field(tmp_path)
    decision_path = output_dir / "decisions.json"
    decisions = _load_json(decision_path)
    decisions[0].update(decision="reject", note="Keep this feedback", attempt=1)
    decision_path.write_text(json.dumps(decisions, indent=2), encoding="utf-8")

    run_workflow(samples, output_dir)

    saved = _load_json(decision_path)[0]
    assert saved["decision"] == "reject"
    assert saved["note"] == "Keep this feedback"
    assert saved["attempt"] == 1


def test_review_cli_command_applies_approve_decision(tmp_path):
    _, output_dir, _ = _single_review_field(tmp_path)
    _write_decision(output_dir, "one_field", "approve", "Approved")

    result = subprocess.run(
        [
            sys.executable, "-m", "accessibility_remediation", "review",
            "--output-dir", str(output_dir), "--reviewer", "CLI Reviewer",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "1 staged" in result.stdout
    event = json.loads((output_dir / "review_log.jsonl").read_text().splitlines()[0])
    assert event["reviewer"] == "CLI Reviewer"


def test_all_workflow_outputs_stay_inside_configured_output_dir(tmp_path):
    _, output_dir, _ = _single_review_field(tmp_path)
    _write_decision(output_dir, "one_field", "approve", "Approved")
    process_review_decisions(output_dir)

    for path in output_dir.rglob("*"):
        assert path.resolve().is_relative_to(output_dir.resolve())


def test_workflow_refuses_to_write_through_staging_symlink(tmp_path):
    samples = tmp_path / "samples"
    samples.mkdir()
    (samples / "one_field.html").write_text(
        "<table><caption>Example</caption><thead><tr><th scope='col'>Value</th></tr></thead>"
        "<tbody><tr><td>42</td></tr></tbody></table>"
    )
    output_dir = tmp_path / "workflow_output"
    outside = tmp_path / "outside.html"
    outside.write_text("untouched")
    staging = output_dir / "staging"
    staging.mkdir(parents=True)
    (staging / "one_field.html").symlink_to(outside)

    with pytest.raises(ValueError, match="symlink"):
        run_workflow(samples, output_dir)

    assert outside.read_text() == "untouched"
