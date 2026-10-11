"""Scanning and conservative deterministic fixes for HTML tables."""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Mapping

from bs4 import BeautifulSoup, Tag

_STRUCTURAL_TAGS = {"table", "caption", "thead", "tbody", "tfoot", "tr", "th", "td"}
_CELL_TAGS = {"th", "td"}


@dataclass(frozen=True)
class TableFixResult:
    """Outcome and review signal for one table in a code field."""

    index: int
    confidence: float
    needs_review: bool
    fixed: bool
    issues_before: tuple[str, ...]
    issues_after: tuple[str, ...]
    review_reasons: tuple[str, ...]


@dataclass(frozen=True)
class FieldFixResult:
    """Fixed HTML and aggregated outcome for one HTML code field."""

    html: str
    confidence: float
    needs_review: bool
    tables: tuple[TableFixResult, ...]
    review_reasons: tuple[str, ...]


@dataclass(frozen=True)
class CodeFieldsFixResult:
    """Per-field results and review signal for all supplied code fields."""

    fields: Mapping[str, FieldFixResult]
    confidence: float
    needs_review: bool
    review_reasons: tuple[str, ...]


class _UnclosedTagParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.malformed_tables: set[int] = set()
        self.table_tags: dict[int, set[str]] = {}
        self.table_stack: list[int] = []
        self.table_count = 0
        self.has_document_shell = False

    def close(self) -> None:
        super().close()
        if self.stack:
            self._mark_malformed()
            self.malformed_tables.update(self.table_stack)

    def _mark_malformed(self) -> None:
        if self.table_stack:
            self.malformed_tables.add(self.table_stack[-1])

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"html", "head"}:
            self.has_document_shell = True
        if tag == "table":
            self.table_count += 1
            self.table_stack.append(self.table_count)
            self.table_tags[self.table_count] = set()
        if self.table_stack and tag in _STRUCTURAL_TAGS:
            self.table_tags[self.table_stack[-1]].add(tag)
        if tag in _STRUCTURAL_TAGS:
            if tag in _CELL_TAGS and self.stack and self.stack[-1] in _CELL_TAGS:
                self._mark_malformed()
            self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag == "table" and self.table_stack:
            table_index = self.table_stack[-1]
            table_position = len(self.stack) - 1 - self.stack[::-1].index("table")
            if table_position < len(self.stack) - 1:
                self.malformed_tables.add(table_index)
            self.table_stack.pop()
        if tag not in _STRUCTURAL_TAGS:
            return
        if tag in self.stack:
            if self.stack[-1] != tag:
                self._mark_malformed()
            index = len(self.stack) - 1 - self.stack[::-1].index(tag)
            self.stack = self.stack[:index]
        else:
            self._mark_malformed()

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _STRUCTURAL_TAGS:
            self._mark_malformed()

    def handle_decl(self, decl: str) -> None:
        self.has_document_shell = True


def _parse_html(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html5lib")


def _cell_text(cell: Tag) -> str:
    return " ".join(cell.get_text(" ", strip=True).split())


def extract_cell_values(html: str) -> list[str]:
    """Return ordered, normalized text values from all tables in the HTML."""
    soup = _parse_html(html)
    return [
        _cell_text(cell)
        for table in soup.find_all("table")
        for cell in table.find_all(["th", "td"])
        if cell.find_parent("table") is table
    ]


def validate_cell_values(before_html: str, after_html: str) -> None:
    """Raise ValueError unless all ordered cell values are unchanged."""
    before = extract_cell_values(before_html)
    after = extract_cell_values(after_html)
    if before != after:
        raise ValueError(f"Table cell values changed: before={before!r}, after={after!r}")


def _scan_one_table(
    table: Tag,
    index: int,
    parser: _UnclosedTagParser,
) -> list[str]:
    issues: list[str] = []
    if index in parser.malformed_tables:
        issues.append("unclosed-tag")

    if table.find("caption", recursive=False) is None:
        issues.append("missing-caption")

    cells = table.find_all(["th", "td"])
    if not any(cell.name == "th" and cell.find_parent("table") is table for cell in cells):
        issues.append("no-headers")
    if any(
        cell.name == "th" and not cell.has_attr("scope")
        for cell in cells
        if cell.find_parent("table") is table
    ):
        issues.append("header-missing-scope")

    explicit_tags = parser.table_tags.get(index, set())
    if "thead" not in explicit_tags:
        issues.append("missing-thead")
    if "tbody" not in explicit_tags:
        issues.append("missing-tbody")

    own_cells = [
        cell for cell in cells if cell.find_parent("table") is table
    ]
    if any(cell.has_attr("rowspan") or cell.has_attr("colspan") for cell in own_cells):
        issues.append("merged-cells")

    rows = [
        row for row in table.find_all("tr")
        if row.find_parent("table") is table
    ]
    if any(
        not any(_cell_text(cell) for cell in row.find_all(["th", "td"], recursive=False))
        for row in rows
    ):
        issues.append("spacer-row")
    widths = {
        len(row.find_all(["th", "td"], recursive=False))
        for row in rows
    }
    has_merged_cells = any(
        cell.has_attr("rowspan") or cell.has_attr("colspan")
        for cell in own_cells
    )
    if len(widths) > 1 and not has_merged_cells:
        issues.append("irregular-rows")

    thead = table.find("thead", recursive=False)
    if isinstance(thead, Tag) and len(thead.find_all("tr")) > 1:
        issues.append("multi-level-headers")
    elif thead is None:
        leading_header_rows = 0
        for row in rows:
            row_cells = row.find_all(["th", "td"], recursive=False)
            if row_cells and all(cell.name == "th" for cell in row_cells):
                leading_header_rows += 1
            else:
                break
        if leading_header_rows > 1:
            issues.append("multi-level-headers")

    return issues


def scan_table(html: str) -> list[str]:
    """List detectable issues across every table in the supplied HTML."""
    parser = _UnclosedTagParser()
    parser.feed(html)
    parser.close()
    soup = _parse_html(html)
    tables = soup.find_all("table")
    if not tables:
        return ["Input must contain an HTML table."]

    issues: list[str] = []
    for index, table in enumerate(tables, start=1):
        issues.extend(_scan_one_table(table, index, parser))
    return list(dict.fromkeys(issues))


def _repair_table(soup: BeautifulSoup, table: Tag) -> bool:
    """Repair one supported table and return whether its headers were inferred."""
    cells = table.find_all(["th", "td"])
    has_merged_cells = any(
        cell.find_parent("table") is table
        and (cell.has_attr("rowspan") or cell.has_attr("colspan"))
        for cell in cells
    )
    if has_merged_cells:
        raise ValueError("Merged cells require manual remediation.")
    if table.find("table") is not None:
        raise ValueError("Nested tables require manual remediation.")

    rows = table.find_all("tr")
    if not rows:
        raise ValueError("Table has no rows.")
    if table.find("tfoot", recursive=False) is not None:
        raise ValueError("Footer rows require manual remediation.")

    row_cells = [row.find_all(["th", "td"], recursive=False) for row in rows]
    widths = {len(row) for row in row_cells}
    if 0 in widths or len(widths) != 1:
        raise ValueError("Irregular or spacer rows require manual remediation.")
    if any(not any(_cell_text(cell) for cell in cells_in_row) for cells_in_row in row_cells):
        raise ValueError("Irregular or spacer rows require manual remediation.")
    if any(not _cell_text(cell) for cell in row_cells[0]):
        raise ValueError("An empty first row cannot be safely inferred as a header.")

    existing_thead = table.find("thead", recursive=False)
    if isinstance(existing_thead, Tag) and len(existing_thead.find_all("tr")) > 1:
        raise ValueError("Multi-level headers require manual remediation.")

    header_row = rows[0]
    if any(cell.name == "th" for cell in row_cells[0]) and not all(
        cell.name == "th" for cell in row_cells[0]
    ):
        raise ValueError("Mixed header and data cells in the first row are ambiguous.")
    if isinstance(existing_thead, Tag) and header_row.parent is not existing_thead:
        raise ValueError("Existing header structure is ambiguous.")
    inferred_headers = all(cell.name != "th" for cell in row_cells[0])
    for cell in row_cells[0]:
        if cell.name != "th":
            cell.name = "th"
        cell["scope"] = "col"

    if isinstance(existing_thead, Tag):
        thead = existing_thead
        body_rows = [row for row in rows if row.find_parent("thead") is not thead]
    else:
        thead = soup.new_tag("thead")
        body_rows = rows[1:]
        header_row.extract()
        thead.append(header_row)

    old_tbodies = table.find_all("tbody", recursive=False)
    for old_tbody in old_tbodies:
        old_tbody.unwrap()
    tbody = soup.new_tag("tbody")
    for row in body_rows:
        tbody.append(row.extract())
    if not isinstance(existing_thead, Tag):
        table.append(thead)
    table.append(tbody)

    for row in tbody.find_all("tr", recursive=False):
        data_cells = row.find_all(["th", "td"], recursive=False)
        if data_cells and data_cells[0].name == "th":
            data_cells[0]["scope"] = "row"

    return inferred_headers


def apply_fix(html: str) -> FieldFixResult:
    """Fix every supported table in one code field and report review confidence.

    A table with inferred first-row headers is always marked for human review.
    Unsupported tables remain unchanged and are reported as requiring review;
    other tables in the same field are still processed.
    """
    soup = _parse_html(html)
    source_parser = _UnclosedTagParser()
    source_parser.feed(html)
    source_parser.close()
    tables = soup.find_all("table")
    if not tables:
        return FieldFixResult(
            html=html,
            confidence=0.0,
            needs_review=True,
            tables=(),
            review_reasons=("no-table-found",),
        )

    outcomes: list[TableFixResult] = []
    for index, table in enumerate(tables, start=1):
        issues_before = tuple(_scan_one_table(table, index, source_parser))
        try:
            inferred_headers = _repair_table(soup, table)
        except ValueError as error:
            outcomes.append(TableFixResult(
                index=index,
                confidence=0.0,
                needs_review=True,
                fixed=False,
                issues_before=issues_before,
                issues_after=issues_before,
                review_reasons=(str(error),),
            ))
            continue

        result_parser = _UnclosedTagParser()
        result_parser.feed(str(table))
        result_parser.close()
        issues_after = tuple(_scan_one_table(table, 1, result_parser))
        reasons: list[str] = []
        confidence = 1.0
        if inferred_headers:
            confidence = 0.5
            reasons.append("inferred-first-row-header")
        if issues_after:
            confidence = min(confidence, 0.5)
            reasons.extend(f"unresolved:{issue}" for issue in issues_after)
        outcomes.append(TableFixResult(
            index=index,
            confidence=confidence,
            needs_review=bool(reasons),
            fixed=True,
            issues_before=issues_before,
            issues_after=issues_after,
            review_reasons=tuple(dict.fromkeys(reasons)),
        ))

    result = (
        str(soup)
        if source_parser.has_document_shell
        else soup.body.decode_contents() if soup.body is not None else str(soup)
    )
    validate_cell_values(html, result)

    reasons = tuple(
        f"table-{outcome.index}:{reason}"
        for outcome in outcomes
        for reason in outcome.review_reasons
    )
    confidence = min(outcome.confidence for outcome in outcomes)
    return FieldFixResult(
        html=result,
        confidence=confidence,
        needs_review=any(outcome.needs_review for outcome in outcomes),
        tables=tuple(outcomes),
        review_reasons=reasons,
    )


def apply_fixes_to_fields(code_fields: Mapping[str, str]) -> CodeFieldsFixResult:
    """Fix every supplied code field and aggregate its human-review signal."""
    fields = {name: apply_fix(html) for name, html in code_fields.items()}
    reasons = tuple(
        f"{name}:{reason}"
        for name, result in fields.items()
        for reason in result.review_reasons
    )
    confidence = min((result.confidence for result in fields.values()), default=1.0)
    return CodeFieldsFixResult(
        fields=fields,
        confidence=confidence,
        needs_review=any(result.needs_review for result in fields.values()),
        review_reasons=reasons,
    )
