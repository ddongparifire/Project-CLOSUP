from pathlib import Path

import pytest

from accessibility_remediation import (
    apply_fix,
    extract_cell_values,
    scan_table,
    validate_cell_values,
)

SAMPLES = Path(__file__).parents[1] / "samples" / "legacy"


def test_fixes_simple_table_and_preserves_every_cell_value():
    before = '<table><tr><td>Region</td><td>Count</td></tr><tr><td>North</td><td>42</td></tr></table>'
    after = apply_fix(before)

    assert "<thead>" in after
    assert "<tbody>" in after
    assert '<th scope="col">Region</th>' in after
    assert extract_cell_values(before) == extract_cell_values(after)
    validate_cell_values(before, after)


def test_adds_row_scope_to_existing_row_header():
    before = (
        "<table><thead><tr><th>Region</th><th>Count</th></tr></thead>"
        "<tbody><tr><th>North</th><td>42</td></tr></tbody></table>"
    )
    after = apply_fix(before)

    assert '<th scope="row">North</th>' in after
    validate_cell_values(before, after)


def test_repairs_unclosed_cell_tags_without_changing_values():
    before = "<table><tr><td>Region<td>North</tr><tr><td>Count<td>42</tr></table>"
    after = apply_fix(before)

    assert "unclosed-tag" in scan_table(before)
    assert "unclosed-tag" not in scan_table(after)
    validate_cell_values(before, after)


def test_fixer_preserves_full_document_shell():
    before = (
        "<!doctype html><html><head><title>Data</title></head><body>"
        "<table><tr><td>Region</td><td>North</td></tr></table></body></html>"
    )
    after = apply_fix(before)

    assert "<title>Data</title>" in after
    assert "<!DOCTYPE html>" in after
    validate_cell_values(before, after)


def test_scanner_finds_missing_caption_headers_and_sections():
    problems = scan_table("<table><tr><td>Only data</td></tr></table>")

    assert "missing-caption" in problems
    assert "no-headers" in problems
    assert "missing-thead" in problems
    assert "missing-tbody" in problems


def test_scanner_detects_unclosed_tags():
    problems = scan_table("<table><tr><td>Region<td>North</tr></table>")

    assert "unclosed-tag" in problems


@pytest.mark.parametrize("name", ["merged_cells.html", "spacer_row.html", "multi_level_headers.html"])
def test_fixer_refuses_structures_needing_human_judgment(name):
    with pytest.raises(ValueError, match="manual remediation|ambiguous"):
        apply_fix((SAMPLES / name).read_text())


def test_data_check_rejects_changed_cell_value():
    with pytest.raises(ValueError, match="cell values changed"):
        validate_cell_values(
            "<table><tr><td>Count</td><td>29</td></tr></table>",
            "<table><tr><td>Count</td><td>290</td></tr></table>",
        )


def test_all_samples_are_readable_and_scannable():
    samples = sorted(SAMPLES.glob("*.html"))
    assert len(samples) == 10
    assert all(isinstance(scan_table(path.read_text()), list) for path in samples)


@pytest.mark.parametrize(
    ("name", "expected_issue"),
    [
        ("no_headers.html", "no-headers"),
        ("unclosed_tags.html", "unclosed-tag"),
        ("merged_cells.html", "merged-cells"),
        ("multi_level_headers.html", "multi-level-headers"),
        ("missing_caption.html", "missing-caption"),
        ("spacer_row.html", "spacer-row"),
        ("irregular_rows.html", "irregular-rows"),
    ],
)
def test_scanner_reports_expected_sample_issues(name, expected_issue):
    assert expected_issue in scan_table((SAMPLES / name).read_text())
