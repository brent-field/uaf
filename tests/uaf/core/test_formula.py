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

    def test_function_call_blocked(self) -> None:
        result = evaluate_formula("=print(1)", self._no_cells)
        assert result == "#ERROR!"

    def test_attribute_access_blocked(self) -> None:
        result = evaluate_formula("=''.__class__", self._no_cells)
        assert result == "#ERROR!"

    def test_string_literal_blocked(self) -> None:
        result = evaluate_formula("='hello'", self._no_cells)
        assert result == "#ERROR!"

    def test_list_blocked(self) -> None:
        result = evaluate_formula("=[1,2,3]", self._no_cells)
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
