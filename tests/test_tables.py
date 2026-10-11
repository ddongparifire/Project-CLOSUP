from pathlib import Path

import pytest

from accessibility_remediation import (
    apply_fix,
    apply_fixes_to_fields,
    extract_cell_values,
    scan_table,
    scan_table_notes,
    validate_cell_values,
)

SAMPLES = Path(__file__).parents[1] / "samples" / "legacy"


def test_fixes_simple_table_and_preserves_every_cell_value():
    before = '<table><tr><td>Region</td><td>Count</td></tr><tr><td>North</td><td>42</td></tr></table>'
    result = apply_fix(before)

    assert "<thead>" in result.html
    assert "<tbody>" in result.html
    assert '<th scope="col">Region</th>' in result.html
    assert result.confidence == 0.5
    assert result.needs_review
    assert "inferred-first-row-header" in result.tables[0].review_reasons
    assert extract_cell_values(before) == extract_cell_values(result.html)
    validate_cell_values(before, result.html)


def test_adds_row_scope_to_existing_row_header():
    before = (
        "<table><thead><tr><th>Region</th><th>Count</th></tr></thead>"
        "<tbody><tr><th>North</th><td>42</td></tr></tbody></table>"
    )
    result = apply_fix(before)

    assert '<th scope="row">North</th>' in result.html
    validate_cell_values(before, result.html)


def test_repairs_unclosed_cell_tags_without_changing_values():
    before = "<table><tr><td>Region<td>North</tr><tr><td>Count<td>42</tr></table>"
    result = apply_fix(before)

    assert "unclosed-tag" in scan_table(before)
    assert "unclosed-tag" not in scan_table(result.html)
    assert result.needs_review
    validate_cell_values(before, result.html)


def test_fixer_preserves_full_document_shell():
    before = (
        "<!doctype html><html><head><title>Data</title></head><body>"
        "<table><tr><td>Region</td><td>North</td></tr></table></body></html>"
    )
    result = apply_fix(before)

    assert "<title>Data</title>" in result.html
    assert "<!DOCTYPE html>" in result.html
    validate_cell_values(before, result.html)


def test_scanner_finds_missing_caption_headers_and_sections():
    problems = scan_table("<table><tr><td>Only data</td></tr></table>")

    assert "missing-caption" in problems
    assert "no-headers" in problems
    assert "missing-thead" in problems
    assert "missing-tbody" in problems


def test_scanner_detects_unclosed_tags():
    problems = scan_table("<table><tr><td>Region<td>North</tr></table>")

    assert "unclosed-tag" in problems


def test_scanner_detects_tags_left_open_at_end_of_field():
    problems = scan_table("<table><tr><td>Region</td>")

    assert "unclosed-tag" in problems


@pytest.mark.parametrize("name", ["merged_cells.html", "spacer_row.html", "multi_level_headers.html"])
def test_fixer_reports_structures_needing_human_judgment(name):
    result = apply_fix((SAMPLES / name).read_text())

    assert result.needs_review
    assert not result.tables[0].fixed
    assert result.confidence == 0.0
    assert result.tables[0].review_reasons


def test_data_check_rejects_changed_cell_value():
    with pytest.raises(ValueError, match="cell values changed"):
        validate_cell_values(
            "<table><tr><td>Count</td><td>29</td></tr></table>",
            "<table><tr><td>Count</td><td>290</td></tr></table>",
        )


def test_all_samples_are_readable_and_scannable():
    samples = sorted(SAMPLES.glob("*.html"))
    assert len(samples) == 11
    assert all(isinstance(scan_table(path.read_text()), list) for path in samples)


def test_label_value_table_is_repaired_but_flagged_for_header_review():
    result = apply_fix((SAMPLES / "unclosed_tags.html").read_text())

    assert '<th scope="col">District</th>' in result.html
    assert result.tables[0].fixed
    assert result.tables[0].confidence < 1.0
    assert result.needs_review
    assert "inferred-first-row-header" in result.review_reasons[0]


def test_fixes_every_table_in_a_field_and_preserves_all_cell_values():
    before = (
        "<table><tr><td>District</td><td>Population</td></tr>"
        "<tr><td>Lakeview</td><td>1200</td></tr></table>"
        "<p>Between tables</p>"
        "<table><tr><td>Year</td><td>Count</td></tr>"
        "<tr><td>2025</td><td>42</td></tr></table>"
    )
    result = apply_fix(before)

    assert len(result.tables) == 2
    assert all(table.fixed for table in result.tables)
    assert result.needs_review
    assert result.html.count("<thead>") == 2
    validate_cell_values(before, result.html)


def test_unsupported_table_is_flagged_without_blocking_later_tables():
    html = (
        "<table><tr><td rowspan='2'>Merged</td><td>A</td></tr>"
        "<tr><td>B</td></tr></table>"
        "<table><tr><td>Label</td><td>Value</td></tr></table>"
    )
    result = apply_fix(html)

    assert len(result.tables) == 2
    assert not result.tables[0].fixed
    assert result.tables[1].fixed
    assert result.needs_review
    assert '<td rowspan="2">Merged</td>' in result.html


def test_processes_all_supplied_code_fields_and_aggregates_review():
    result = apply_fixes_to_fields({
        "field_a": "<table><tr><td>Label</td><td>Value</td></tr></table>",
        "field_b": (
            "<table><caption>Annual results</caption>"
            "<thead><tr><th scope='col'>Year</th></tr></thead>"
            "<tbody><tr><td>2025</td></tr></tbody></table>"
        ),
    })

    assert set(result.fields) == {"field_a", "field_b"}
    assert len(result.fields["field_a"].tables) == 1
    assert len(result.fields["field_b"].tables) == 1
    assert result.needs_review
    assert result.fields["field_a"].needs_review
    assert not result.fields["field_b"].needs_review


def test_scanner_includes_issues_from_later_tables():
    html = (
        "<table><caption>First</caption><thead><tr><th scope='col'>A</th></tr></thead>"
        "<tbody><tr><td>1</td></tr></tbody></table>"
        "<table><tr><td>B</td></tr></table>"
    )

    assert "no-headers" in scan_table(html)
    assert "missing-caption" in scan_table(html)


@pytest.mark.parametrize(
    ("name", "expected_issue"),
    [
        ("no_headers.html", "no-headers"),
        ("unclosed_tags.html", "unclosed-tag"),
        ("missing_caption.html", "missing-caption"),
        ("spacer_row.html", "spacer-row"),
    ],
)
def test_scanner_reports_expected_sample_issues(name, expected_issue):
    assert expected_issue in scan_table((SAMPLES / name).read_text())


@pytest.mark.parametrize(
    ("name", "expected_note"),
    [
        ("merged_cells.html", "merged-cells"),
        ("multi_level_headers.html", "multi-level-headers"),
        ("irregular_rows.html", "irregular-rows"),
    ],
)
def test_scanner_separates_complexity_notes_from_blocking_errors(name, expected_note):
    source = (SAMPLES / name).read_text()

    assert expected_note in scan_table_notes(source)
    assert expected_note not in scan_table(source)


def test_header_linked_by_id_and_headers_does_not_need_scope():
    html = (
        '<table><caption>Results</caption><thead><tr>'
        '<th id="year">Year</th></tr></thead><tbody><tr>'
        '<td headers="year">2025</td></tr></tbody></table>'
    )

    assert "header-missing-scope" not in scan_table(html)


def test_unlinked_header_without_scope_is_blocking_even_with_complexity_notes():
    html = (
        "<table><caption>Results</caption><thead><tr>"
        "<th id='year'>Year</th></tr></thead><tbody>"
        "<tr><td>2025</td></tr></tbody></table>"
    )

    assert "header-missing-scope" in scan_table(html)
