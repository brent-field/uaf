"""Tests for the formula engine — parsing, evaluation, and security."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from uaf.core.formula import evaluate_formula, parse_cell_ref, resolve_formula

if TYPE_CHECKING:
    from uaf.core.nodes import CellValue


class TestParseCellRef:
    def test_a1(self) -> None:
        assert parse_cell_ref("A1") == (0, 0)

    def test_b3(self) -> None:
        assert parse_cell_ref("B3") == (2, 1)

    def test_z26(self) -> None:
        assert parse_cell_ref("Z26") == (25, 25)

    def test_invalid_lowercase(self) -> None:
        with pytest.raises(ValueError, match="Invalid cell reference"):
            parse_cell_ref("a1")

    def test_invalid_no_digits(self) -> None:
        with pytest.raises(ValueError, match="Invalid cell reference"):
            parse_cell_ref("A")

    def test_invalid_multi_letter(self) -> None:
        with pytest.raises(ValueError, match="Invalid cell reference"):
            parse_cell_ref("AA1")

    def test_invalid_empty(self) -> None:
        with pytest.raises(ValueError, match="Invalid cell reference"):
            parse_cell_ref("")


class TestEvaluateFormula:
    def _no_cells(self, row: int, col: int) -> CellValue:
        return None

    def test_simple_addition(self) -> None:
        assert evaluate_formula("=1+2", self._no_cells) == 3

    def test_multiplication(self) -> None:
        assert evaluate_formula("=3*4", self._no_cells) == 12

    def test_division_float(self) -> None:
        assert evaluate_formula("=7/2", self._no_cells) == 3.5

    def test_division_whole(self) -> None:
        assert evaluate_formula("=6/2", self._no_cells) == 3

    def test_parentheses(self) -> None:
        assert evaluate_formula("=(2+3)*4", self._no_cells) == 20

    def test_negative(self) -> None:
        assert evaluate_formula("=-5+3", self._no_cells) == -2

    def test_cell_reference(self) -> None:
        def getter(row: int, col: int) -> CellValue:
            if (row, col) == (0, 0):
                return 10
            return None

        assert evaluate_formula("=A1+5", getter) == 15

    def test_multiple_cell_refs(self) -> None:
        def getter(row: int, col: int) -> CellValue:
            cells: dict[tuple[int, int], CellValue] = {
                (0, 0): 10,
                (0, 1): 20,
            }
            return cells.get((row, col))

        assert evaluate_formula("=A1+B1", getter) == 30

    def test_none_cell_treated_as_zero(self) -> None:
        assert evaluate_formula("=A1+1", self._no_cells) == 1

    def test_empty_string_cell_treated_as_zero(self) -> None:
        def getter(row: int, col: int) -> CellValue:
            return ""

        assert evaluate_formula("=A1+1", getter) == 1

    def test_bool_cell_conversion(self) -> None:
        def getter(row: int, col: int) -> CellValue:
            return True

        assert evaluate_formula("=A1+1", getter) == 2

    def test_empty_formula(self) -> None:
        assert evaluate_formula("=", self._no_cells) == "#ERROR!"

    def test_empty_formula_whitespace(self) -> None:
        assert evaluate_formula("=  ", self._no_cells) == "#ERROR!"


class TestFormulaSecurity:
    def _no_cells(self, row: int, col: int) -> CellValue:
        return None

    def test_import_blocked(self) -> None:
        result = evaluate_formula("=__import__('os')", self._no_cells)
        assert result == "#ERROR!"

    def test_unknown_function_blocked(self) -> None:
        result = evaluate_formula("=print(1)", self._no_cells)
        assert result == "#ERROR!"

    def test_attribute_access_blocked(self) -> None:
        result = evaluate_formula("=''.__class__", self._no_cells)
        assert result == "#ERROR!"

    def test_string_literal_returns_string(self) -> None:
        result = evaluate_formula("='hello'", self._no_cells)
        assert result == "hello"

    def test_list_blocked(self) -> None:
        result = evaluate_formula("=[1,2,3]", self._no_cells)
        assert result == "#ERROR!"

    def test_eval_blocked(self) -> None:
        result = evaluate_formula("=eval('1+1')", self._no_cells)
        assert result == "#ERROR!"

    def test_exec_blocked(self) -> None:
        result = evaluate_formula("=exec('pass')", self._no_cells)
        assert result == "#ERROR!"

    def test_dunder_name_blocked(self) -> None:
        result = evaluate_formula("=__builtins__", self._no_cells)
        assert result == "#ERROR!"


class TestResolveFormula:
    def test_basic(self) -> None:
        cells: dict[tuple[int, int], CellValue] = {
            (0, 0): 5,
            (0, 1): 10,
        }
        assert resolve_formula("=A1+B1", cells) == 15

    def test_missing_cell(self) -> None:
        cells: dict[tuple[int, int], CellValue] = {}
        assert resolve_formula("=A1+1", cells) == 1

    def test_chained_arithmetic(self) -> None:
        cells: dict[tuple[int, int], CellValue] = {
            (0, 0): 2,
            (0, 1): 3,
            (0, 2): 4,
        }
        assert resolve_formula("=A1*B1+C1", cells) == 10


# --- Range references ---


class TestRangeReferences:
    def test_sum_column_range(self) -> None:
        cells: dict[tuple[int, int], CellValue] = {
            (0, 0): 1,
            (1, 0): 2,
            (2, 0): 3,
        }
        assert resolve_formula("=SUM(A1:A3)", cells) == 6

    def test_sum_2d_range(self) -> None:
        cells: dict[tuple[int, int], CellValue] = {
            (0, 0): 1,
            (1, 0): 2,
            (2, 0): 3,
            (0, 1): 4,
            (1, 1): 5,
            (2, 1): 6,
        }
        assert resolve_formula("=SUM(A1:B3)", cells) == 21

    def test_single_cell_range(self) -> None:
        cells: dict[tuple[int, int], CellValue] = {(0, 0): 42}
        assert resolve_formula("=SUM(A1:A1)", cells) == 42


# --- Math functions ---


class TestMathFunctions:
    def _no_cells(self, row: int, col: int) -> CellValue:
        return None

    def test_sum_literals(self) -> None:
        assert evaluate_formula("=SUM(1,2,3)", self._no_cells) == 6

    def test_sum_with_cell_refs(self) -> None:
        cells: dict[tuple[int, int], CellValue] = {
            (0, 0): 10,
            (0, 1): 20,
        }
        assert resolve_formula("=SUM(A1,B1)", cells) == 30

    def test_sum_ignores_non_numeric(self) -> None:
        cells: dict[tuple[int, int], CellValue] = {
            (0, 0): 10,
            (0, 1): "text",
        }
        assert resolve_formula("=SUM(A1,B1)", cells) == 10

    def test_average(self) -> None:
        assert evaluate_formula("=AVERAGE(2,4,6)", self._no_cells) == 4.0

    def test_average_with_range(self) -> None:
        cells: dict[tuple[int, int], CellValue] = {
            (0, 0): 10,
            (1, 0): 20,
            (2, 0): 30,
        }
        assert resolve_formula("=AVERAGE(A1:A3)", cells) == 20.0

    def test_min(self) -> None:
        assert evaluate_formula("=MIN(5,2,8)", self._no_cells) == 2

    def test_max(self) -> None:
        assert evaluate_formula("=MAX(5,2,8)", self._no_cells) == 8

    def test_count(self) -> None:
        cells: dict[tuple[int, int], CellValue] = {
            (0, 0): 1,
            (1, 0): "text",
            (2, 0): 3,
        }
        assert resolve_formula("=COUNT(A1:A3)", cells) == 2

    def test_round_default(self) -> None:
        assert evaluate_formula("=ROUND(3.7)", self._no_cells) == 4

    def test_round_with_digits(self) -> None:
        assert evaluate_formula("=ROUND(3.14159,2)", self._no_cells) == 3.14

    def test_abs_negative(self) -> None:
        assert evaluate_formula("=ABS(-5)", self._no_cells) == 5

    def test_abs_positive(self) -> None:
        assert evaluate_formula("=ABS(5)", self._no_cells) == 5

    def test_sum_range(self) -> None:
        cells: dict[tuple[int, int], CellValue] = {
            (0, 0): 1,
            (1, 0): 2,
            (2, 0): 3,
            (3, 0): 4,
            (4, 0): 5,
        }
        assert resolve_formula("=SUM(A1:A5)", cells) == 15


# --- Logic functions ---


class TestLogicFunctions:
    def _no_cells(self, row: int, col: int) -> CellValue:
        return None

    def test_if_true(self) -> None:
        cells: dict[tuple[int, int], CellValue] = {(0, 0): 15}
        assert resolve_formula('=IF(A1>10,"high","low")', cells) == "high"

    def test_if_false(self) -> None:
        cells: dict[tuple[int, int], CellValue] = {(0, 0): 5}
        assert resolve_formula('=IF(A1>10,"high","low")', cells) == "low"

    def test_if_numeric_results(self) -> None:
        assert evaluate_formula("=IF(1>0,100,0)", self._no_cells) == 100

    def test_nested_if(self) -> None:
        result = evaluate_formula(
            '=IF(5>10,"a",IF(5>3,"b","c"))', self._no_cells
        )
        assert result == "b"

    def test_and_true(self) -> None:
        assert evaluate_formula("=AND(1,1,1)", self._no_cells) is True

    def test_and_false(self) -> None:
        assert evaluate_formula("=AND(1,0,1)", self._no_cells) is False

    def test_or_true(self) -> None:
        assert evaluate_formula("=OR(0,0,1)", self._no_cells) is True

    def test_or_false(self) -> None:
        assert evaluate_formula("=OR(0,0,0)", self._no_cells) is False

    def test_not_true(self) -> None:
        assert evaluate_formula("=NOT(0)", self._no_cells) is True

    def test_not_false(self) -> None:
        assert evaluate_formula("=NOT(1)", self._no_cells) is False

    def test_if_with_comparison(self) -> None:
        assert evaluate_formula("=IF(3>=3,1,0)", self._no_cells) == 1

    def test_if_with_equality(self) -> None:
        assert evaluate_formula("=IF(3==3,1,0)", self._no_cells) == 1


# --- Text functions ---


class TestTextFunctions:
    def _no_cells(self, row: int, col: int) -> CellValue:
        return None

    def test_len(self) -> None:
        assert evaluate_formula("=LEN('hello')", self._no_cells) == 5

    def test_upper(self) -> None:
        assert evaluate_formula("=UPPER('hello')", self._no_cells) == "HELLO"

    def test_lower(self) -> None:
        assert evaluate_formula("=LOWER('HELLO')", self._no_cells) == "hello"

    def test_trim(self) -> None:
        assert evaluate_formula("=TRIM('  hi  ')", self._no_cells) == "hi"

    def test_concatenate(self) -> None:
        result = evaluate_formula(
            "=CONCATENATE('hello',' ','world')", self._no_cells
        )
        assert result == "hello world"

    def test_concatenate_with_numbers(self) -> None:
        result = evaluate_formula("=CONCATENATE('val:',42)", self._no_cells)
        assert result == "val:42"

    def test_text_from_cell_ref(self) -> None:
        def getter(row: int, col: int) -> CellValue:
            return "hello world"

        assert evaluate_formula("=UPPER(A1)", getter) == "HELLO WORLD"

    def test_len_from_cell_ref(self) -> None:
        def getter(row: int, col: int) -> CellValue:
            return "test"

        assert evaluate_formula("=LEN(A1)", getter) == 4


# --- Function security ---


class TestFunctionSecurity:
    def _no_cells(self, row: int, col: int) -> CellValue:
        return None

    def test_unknown_function_rejected(self) -> None:
        assert evaluate_formula("=print(1)", self._no_cells) == "#ERROR!"

    def test_nested_calls_work(self) -> None:
        cells: dict[tuple[int, int], CellValue] = {
            (0, 0): 1,
            (1, 0): 2,
            (2, 0): 3,
            (3, 0): 4,
            (4, 0): 5,
        }
        assert resolve_formula("=ROUND(AVERAGE(A1:A5),2)", cells) == 3.0

    def test_attribute_access_still_blocked(self) -> None:
        assert evaluate_formula("=''.__class__", self._no_cells) == "#ERROR!"

    def test_subscript_blocked(self) -> None:
        assert evaluate_formula("='abc'[0]", self._no_cells) == "#ERROR!"

    def test_import_via_call_blocked(self) -> None:
        assert evaluate_formula("=__import__('os')", self._no_cells) == "#ERROR!"
