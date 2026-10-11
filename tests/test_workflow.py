import json
from pathlib import Path

from accessibility_remediation.workflow import run_workflow


SAMPLES = Path(__file__).parents[1] / "samples" / "legacy"


def test_workflow_stages_only_passing_tables_and_writes_review_and_report(tmp_path):
    output_dir = tmp_path / "run"

    summary = run_workflow(SAMPLES, output_dir)

    assert summary.samples_processed == 10
    assert summary.tables_processed == 10
    assert summary.tables_staged == 2
    assert summary.tables_for_review == 8
    assert summary.sample_errors == 0
    staged_files = sorted((output_dir / "staging").glob("*.html"))
    assert len(staged_files) == 2
    assert any("missing_scope" in path.name for path in staged_files)
    assert any("missing_sections" in path.name for path in staged_files)

    review = json.loads((output_dir / "review.json").read_text())
    assert len(review) == 8
    missing_caption = next(record for record in review if record["sample"] == "missing_caption.html")
    assert any("missing-caption" in reason for reason in missing_caption["reasons"])
    assert missing_caption["caption_step"]["status"] == "placeholder"
    assert missing_caption["caption_step"]["draft"] is None

    report = (output_dir / "report.html").read_text()
    assert "Table remediation report" in report
    assert "missing_caption.html — table 1" in report
    assert "<h3>Before</h3>" in report
    assert "<h3>After</h3>" in report
    assert "AI caption drafting is not implemented" in report


def test_workflow_routes_samples_without_tables_to_review(tmp_path):
    samples_dir = tmp_path / "samples"
    samples_dir.mkdir()
    (samples_dir / "empty.html").write_text("<p>No table here</p>")

    summary = run_workflow(samples_dir, tmp_path / "run")

    assert summary.samples_processed == 1
    assert summary.tables_processed == 0
    assert summary.tables_staged == 0
    review = json.loads((tmp_path / "run" / "review.json").read_text())
    assert len(review) == 1
    assert review[0]["reasons"] == ["no-table-found"]


def test_workflow_escapes_table_markup_in_report(tmp_path):
    samples_dir = tmp_path / "samples"
    samples_dir.mkdir()
    (samples_dir / "markup.html").write_text(
        "<table><caption>Example</caption><thead><tr><th scope='col'>Value</th></tr></thead>"
        "<tbody><tr><td>&lt;script&gt;demo&lt;/script&gt;</td></tr></tbody></table>"
    )

    run_workflow(samples_dir, tmp_path / "run")

    report = (tmp_path / "run" / "report.html").read_text()
    assert "&amp;lt;script&amp;gt;demo&amp;lt;/script&amp;gt;" in report
    assert "<script>demo</script>" not in report


def test_workflow_removes_stale_staged_outputs_on_rerun(tmp_path):
    samples_dir = tmp_path / "samples"
    samples_dir.mkdir()
    sample_path = samples_dir / "table.html"
    sample_path.write_text(
        "<table><caption>Example</caption><thead><tr><th scope='col'>Value</th></tr></thead>"
        "<tbody><tr><td>42</td></tr></tbody></table>"
    )
    output_dir = tmp_path / "run"

    first = run_workflow(samples_dir, output_dir)
    assert first.tables_staged == 1
    assert len(list((output_dir / "staging").glob("*.html"))) == 1

    sample_path.write_text("<table><tr><td rowspan='2'>Merged</td><td>A</td></tr></table>")
    second = run_workflow(samples_dir, output_dir)

    assert second.tables_staged == 0
    assert second.tables_for_review == 1
    assert list((output_dir / "staging").glob("*.html")) == []
