"""Prototype tools for legacy table accessibility remediation."""

from .tables import apply_fix, extract_cell_values, scan_table, validate_cell_values

__all__ = ["apply_fix", "extract_cell_values", "scan_table", "validate_cell_values"]
