"""Formula engine — parse cell references and safely evaluate expressions."""

from __future__ import annotations

import ast
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from uaf.core.nodes import CellValue

CellValueType = str | int | float | bool | None


def parse_cell_ref(ref: str) -> tuple[int, int]:
    """Convert A1-style reference to (row, col). A1 -> (0, 0), B3 -> (2, 1)."""
    match = re.fullmatch(r"([A-Z])(\d+)", ref)
    if match is None:
        msg = f"Invalid cell reference: {ref!r}"
        raise ValueError(msg)
    col = ord(match.group(1)) - ord("A")
    row = int(match.group(2)) - 1
    return row, col


# --- Range references ---

_RANGE_REF_RE = re.compile(r"([A-Z])(\d+):([A-Z])(\d+)", re.IGNORECASE)
_CELL_REF_RE = re.compile(r"[A-Z]\d+", re.IGNORECASE)


def _expand_range(m: re.Match[str]) -> str:
    """Expand A1:B3 into comma-separated cell refs: A1,A2,A3,B1,B2,B3."""
    col_start = ord(m.group(1).upper())
    row_start = int(m.group(2))
    col_end = ord(m.group(3).upper())
    row_end = int(m.group(4))
    refs: list[str] = []
    for c in range(col_start, col_end + 1):
        for r in range(row_start, row_end + 1):
            refs.append(f"{chr(c)}{r}")
    return ",".join(refs)


# --- Function registry ---


def _fn_sum(*args: Any) -> int | float:
    return sum(x for x in args if isinstance(x, (int, float)) and not isinstance(x, bool))


def _fn_average(*args: Any) -> float:
    nums = [x for x in args if isinstance(x, (int, float)) and not isinstance(x, bool)]
    if not nums:
        msg = "AVERAGE requires at least one numeric argument"
        raise ValueError(msg)
    return sum(nums) / len(nums)


def _fn_min(*args: Any) -> int | float:
    nums = [x for x in args if isinstance(x, (int, float)) and not isinstance(x, bool)]
    if not nums:
        msg = "MIN requires at least one numeric argument"
        raise ValueError(msg)
    return min(nums)


def _fn_max(*args: Any) -> int | float:
    nums = [x for x in args if isinstance(x, (int, float)) and not isinstance(x, bool)]
    if not nums:
        msg = "MAX requires at least one numeric argument"
        raise ValueError(msg)
    return max(nums)


def _fn_count(*args: Any) -> int:
    return sum(
        1 for x in args if isinstance(x, (int, float)) and not isinstance(x, bool)
    )


def _fn_round(value: float, digits: int = 0) -> float:
    return round(value, digits)


def _fn_abs(value: float) -> int | float:
    return abs(value)


def _fn_if(condition: Any, true_val: Any, false_val: Any) -> Any:
    return true_val if condition else false_val


def _fn_and(*args: Any) -> bool:
    return all(bool(x) for x in args)


def _fn_or(*args: Any) -> bool:
    return any(bool(x) for x in args)


def _fn_not(value: Any) -> bool:
    return not value


def _fn_len(text: Any) -> int:
    return len(str(text))


def _fn_upper(text: Any) -> str:
    return str(text).upper()


def _fn_lower(text: Any) -> str:
    return str(text).lower()


def _fn_trim(text: Any) -> str:
    return str(text).strip()


def _fn_concatenate(*args: Any) -> str:
    return "".join(str(x) if x is not None else "" for x in args)


_FUNCTION_REGISTRY: dict[str, Callable[..., Any]] = {
    "SUM": _fn_sum,
    "AVERAGE": _fn_average,
    "MIN": _fn_min,
    "MAX": _fn_max,
    "COUNT": _fn_count,
    "ROUND": _fn_round,
    "ABS": _fn_abs,
    "IF": _fn_if,
    "AND": _fn_and,
    "OR": _fn_or,
    "NOT": _fn_not,
    "LEN": _fn_len,
    "UPPER": _fn_upper,
    "LOWER": _fn_lower,
    "TRIM": _fn_trim,
    "CONCATENATE": _fn_concatenate,
}

# --- AST safety ---

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
        # Function calls
        ast.Call,
        ast.Name,
        ast.Load,
        # Comparisons
        ast.Compare,
        ast.Gt,
        ast.Lt,
        ast.GtE,
        ast.LtE,
        ast.Eq,
        ast.NotEq,
        # Boolean operators
        ast.BoolOp,
        ast.And,
        ast.Or,
        # Inline if (for IF() eager evaluation isn't an issue)
        ast.IfExp,
    }
)


class _SafeValidator(ast.NodeVisitor):
    """Whitelist-only AST validator."""

    def generic_visit(self, node: ast.AST) -> None:
        if type(node) not in _ALLOWED_NODES:
            msg = f"Disallowed expression node: {type(node).__name__}"
            raise ValueError(msg)
        super().generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        node.id = node.id.upper()
        if node.id not in _FUNCTION_REGISTRY:
            msg = f"Unknown function: {node.id}"
            raise ValueError(msg)

    def visit_Constant(self, node: ast.Constant) -> None:
        if not isinstance(node.value, (int, float, str, bool)):
            msg = f"Disallowed constant type: {type(node.value).__name__}"
            raise ValueError(msg)


# --- Evaluation ---


def evaluate_formula(
    formula: str, cell_getter: Callable[[int, int], CellValue]
) -> CellValue:
    """Safely evaluate a formula string with function support."""
    try:
        expr = formula.lstrip("=").strip()
        if not expr:
            return "#ERROR!"

        # Step 1: Expand range references (A1:B3 → A1,A2,A3,B1,B2,B3)
        expr = _RANGE_REF_RE.sub(_expand_range, expr)

        # Step 2: Replace cell references with values
        def _replace_ref(m: re.Match[str]) -> str:
            row, col = parse_cell_ref(m.group(0).upper())
            val = cell_getter(row, col)
            if val is None or val == "":
                return "0"
            if isinstance(val, bool):
                return "True" if val else "False"
            if isinstance(val, str):
                return repr(val)
            return str(val)

        expr = _CELL_REF_RE.sub(_replace_ref, expr)

        # Step 3: Parse and validate AST (sole security boundary)
        tree = ast.parse(expr, mode="eval")
        _SafeValidator().visit(tree)

        # Step 4: Evaluate with function registry as restricted namespace
        code = compile(tree, "<formula>", "eval")
        eval_globals: dict[str, Any] = {"__builtins__": {}, **_FUNCTION_REGISTRY}
        result = eval(code, eval_globals)

        if isinstance(result, bool):
            return result
        if isinstance(result, (int, float)):
            if isinstance(result, float) and result == int(result):
                return int(result)
            return result
        if isinstance(result, str):
            return result
        return "#ERROR!"
    except Exception:
        return "#ERROR!"


def resolve_formula(
    formula: str, sheet_cells: dict[tuple[int, int], CellValue]
) -> CellValue:
    """Convenience wrapper: evaluate formula using a cell dict."""

    def getter(row: int, col: int) -> CellValue:
        return sheet_cells.get((row, col))

    return evaluate_formula(formula, getter)
