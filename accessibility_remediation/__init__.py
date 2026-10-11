"""Prototype tools for legacy table accessibility remediation."""

from .tables import (
    CodeFieldsFixResult,
    FieldFixResult,
    TableFixResult,
    apply_fix,
    apply_fixes_to_fields,
    extract_cell_values,
    scan_table,
    scan_table_notes,
    validate_cell_values,
)

__all__ = [
    "CodeFieldsFixResult",
    "FieldFixResult",
    "TableFixResult",
    "apply_fix",
    "apply_fixes_to_fields",
    "extract_cell_values",
    "scan_table",
    "scan_table_notes",
    "validate_cell_values",
]
