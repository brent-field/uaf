"""Formula engine — parse cell references and safely evaluate expressions."""

from __future__ import annotations

import ast
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from uaf.core.nodes import CellValue


def parse_cell_ref(ref: str) -> tuple[int, int]:
    """Convert A1-style reference to (row, col). A1 -> (0, 0), B3 -> (2, 1)."""
    match = re.fullmatch(r"([A-Z])(\d+)", ref)
    if match is None:
        msg = f"Invalid cell reference: {ref!r}"
        raise ValueError(msg)
    col = ord(match.group(1)) - ord("A")
    row = int(match.group(2)) - 1
    return row, col


_CELL_REF_RE = re.compile(r"[A-Z]\d+")
_SAFE_EXPR_RE = re.compile(r"^[\d\s\+\-\*\/\.\(\)]+$")

_ALLOWED_NODES: frozenset[type[ast.AST]] = frozenset(
    {
        ast.Expression,
        ast.Constant,
        ast.BinOp,
        ast.UnaryOp,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.FloorDiv,
        ast.Mod,
        ast.Pow,
        ast.UAdd,
        ast.USub,
    }
)


class _SafeValidator(ast.NodeVisitor):
    """Whitelist-only AST validator."""

    def generic_visit(self, node: ast.AST) -> None:
        if type(node) not in _ALLOWED_NODES:
            msg = f"Disallowed expression node: {type(node).__name__}"
            raise ValueError(msg)
        super().generic_visit(node)


def evaluate_formula(formula: str, cell_getter: Callable[[int, int], CellValue]) -> CellValue:
    """Safely evaluate a formula string. Returns int|float or '#ERROR!'."""
    try:
        expr = formula.lstrip("=").strip()
        if not expr:
            return "#ERROR!"

        # Replace cell references with numeric values
        def _replace_ref(m: re.Match[str]) -> str:
            row, col = parse_cell_ref(m.group(0))
            val = cell_getter(row, col)
            if val is None or val == "":
                return "0"
            if isinstance(val, bool):
                return "1" if val else "0"
            return str(val)

        expr = _CELL_REF_RE.sub(_replace_ref, expr)

        # Validate only safe characters remain
        if not _SAFE_EXPR_RE.fullmatch(expr):
            return "#ERROR!"

        # Parse and validate AST
        tree = ast.parse(expr, mode="eval")
        _SafeValidator().visit(tree)

        # Evaluate
        code = compile(tree, "<formula>", "eval")
        result = eval(code)
        if isinstance(result, (int, float)):
            # Return int if result is whole number
            if isinstance(result, float) and result == int(result):
                return int(result)
            return result
        return "#ERROR!"
    except Exception:
        return "#ERROR!"


def resolve_formula(formula: str, sheet_cells: dict[tuple[int, int], CellValue]) -> CellValue:
    """Convenience wrapper: evaluate formula using a cell dict."""

    def getter(row: int, col: int) -> CellValue:
        return sheet_cells.get((row, col))

    return evaluate_formula(formula, getter)
