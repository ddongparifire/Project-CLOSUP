"""Scanning and conservative deterministic fixes for simple HTML tables."""

from __future__ import annotations

from html.parser import HTMLParser

from bs4 import BeautifulSoup, Tag

_STRUCTURAL_TAGS = {"table", "caption", "thead", "tbody", "tfoot", "tr", "th", "td"}
_VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}


class _UnclosedTagParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.malformed = False
        self.seen: set[str] = set()
        self.has_document_shell = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"html", "head"}:
            self.has_document_shell = True
        if tag in _STRUCTURAL_TAGS:
            self.seen.add(tag)
        if tag in _STRUCTURAL_TAGS and tag not in _VOID_TAGS:
            if tag in {"td", "th"} and self.stack and self.stack[-1] in {"td", "th"}:
                self.malformed = True
            self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag not in _STRUCTURAL_TAGS:
            return
        if tag in self.stack:
            if self.stack[-1] != tag:
                self.malformed = True
            index = len(self.stack) - 1 - self.stack[::-1].index(tag)
            self.stack = self.stack[:index]
        else:
            self.malformed = True

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        return

    def handle_decl(self, decl: str) -> None:
        self.has_document_shell = True


def _parse_table(html: str) -> tuple[BeautifulSoup, Tag]:
    soup = BeautifulSoup(html, "html5lib")
    table = soup.find("table")
    if not isinstance(table, Tag):
        raise ValueError("Input must contain an HTML table.")
    return soup, table


def _cell_text(cell: Tag) -> str:
    return " ".join(cell.get_text(" ", strip=True).split())


def extract_cell_values(html: str) -> list[str]:
    """Return ordered, normalized text values from all cells in the first table."""
    _, table = _parse_table(html)
    return [_cell_text(cell) for cell in table.find_all(["th", "td"])]


def validate_cell_values(before_html: str, after_html: str) -> None:
    """Raise ValueError unless ordered cell values are identical before and after."""
    before = extract_cell_values(before_html)
    after = extract_cell_values(after_html)
    if before != after:
        raise ValueError(f"Table cell values changed: before={before!r}, after={after!r}")


def scan_table(html: str) -> list[str]:
    """List detectable accessibility and structural issues in the first table."""
    try:
        _, table = _parse_table(html)
    except ValueError as error:
        return [str(error)]

    issues: list[str] = []
    parser = _UnclosedTagParser()
    parser.feed(html)
    parser.close()
    if parser.stack or parser.malformed:
        issues.append("unclosed-tag")

    if table.find("caption") is None:
        issues.append("missing-caption")

    cells = table.find_all(["th", "td"])
    if not any(cell.name == "th" for cell in cells):
        issues.append("no-headers")
    if any(cell.name == "th" and not cell.has_attr("scope") for cell in cells):
        issues.append("header-missing-scope")

    if "thead" not in parser.seen:
        issues.append("missing-thead")
    if "tbody" not in parser.seen:
        issues.append("missing-tbody")

    if any(cell.has_attr("rowspan") or cell.has_attr("colspan") for cell in cells):
        issues.append("merged-cells")

    rows = table.find_all("tr")
    if any(not any(_cell_text(cell) for cell in row.find_all(["th", "td"], recursive=False))
           for row in rows):
        issues.append("spacer-row")
    widths = {
        len(row.find_all(["th", "td"], recursive=False))
        for row in rows
    }
    has_merged_cells = any(
        cell.has_attr("rowspan") or cell.has_attr("colspan")
        for cell in cells
    )
    if len(widths) > 1 and not has_merged_cells:
        issues.append("irregular-rows")

    thead = table.find("thead")
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


def apply_fix(html: str) -> str:
    """Repair a simple table; reject ambiguous structures for human remediation.

    This rule treats a non-empty first row of an otherwise regular table as its
    column-header row. Captions are intentionally not invented.
    """
    soup, table = _parse_table(html)
    cells = table.find_all(["th", "td"])
    has_merged_cells = any(
        cell.has_attr("rowspan") or cell.has_attr("colspan")
        for cell in cells
    )
    if has_merged_cells:
        raise ValueError("Merged cells require manual remediation.")

    rows = [
        row for row in table.find_all("tr")
        if row.find_parent("table") is table
    ]
    if not rows:
        raise ValueError("Table has no rows.")
    if len(table.find_all("tr")) != len(rows):
        raise ValueError("Nested tables require manual remediation.")
    if table.find("tfoot") is not None:
        raise ValueError("Footer rows require manual remediation.")

    row_cells = [row.find_all(["th", "td"], recursive=False) for row in rows]
    widths = {len(row) for row in row_cells}
    if 0 in widths or len(widths) != 1:
        raise ValueError("Irregular or spacer rows require manual remediation.")
    if any(not any(_cell_text(cell) for cell in cells_in_row) for cells_in_row in row_cells):
        raise ValueError("Irregular or spacer rows require manual remediation.")
    if any(not _cell_text(cell) for cell in row_cells[0]):
        raise ValueError("An empty first row cannot be safely inferred as a header.")

    existing_thead = table.find("thead")
    if isinstance(existing_thead, Tag) and len(existing_thead.find_all("tr")) > 1:
        raise ValueError("Multi-level headers require manual remediation.")

    header_row = rows[0]
    if any(cell.name == "th" for cell in row_cells[0]) and not all(
        cell.name == "th" for cell in row_cells[0]
    ):
        raise ValueError("Mixed header and data cells in the first row are ambiguous.")
    for cell in row_cells[0]:
        if cell.name != "th":
            cell.name = "th"
        cell["scope"] = "col"

    # Keep an existing, single header row if present; otherwise promote row one.
    if isinstance(existing_thead, Tag):
        if header_row.parent is not existing_thead:
            raise ValueError("Existing header structure is ambiguous.")
        thead = existing_thead
        body_rows = [row for row in rows if row.find_parent("thead") is not thead]
    else:
        thead = soup.new_tag("thead")
        body_rows = rows[1:]
        header_row.extract()
        thead.append(header_row)

    old_tbody = table.find("tbody")
    if old_tbody is not None:
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

    source_parser = _UnclosedTagParser()
    source_parser.feed(html)
    source_parser.close()
    if source_parser.has_document_shell:
        result = str(soup)
    else:
        result = soup.body.decode_contents() if soup.body is not None else str(soup)
    validate_cell_values(html, result)
    return result
