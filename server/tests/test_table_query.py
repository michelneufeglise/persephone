"""Deterministic table queries (server/table_query.py)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

pd = pytest.importorskip("pandas")

import sheets as _sheets
import table_query as tq


def _sheet(name: str, rows: list[list], hidden: bool = False) -> dict:
    return _sheets.build_sheet(_sheets.RawSheet(name, rows, hidden=hidden))


@pytest.fixture
def book() -> list[dict]:
    sales = _sheet("Sales", [
        ["Region", "Q1", "Q2", "Q3", "Q4"],
        ["East", 10, 20, 30, 40],
        ["West", 5, 5, 5, 5],
        ["North", 1, 2, 3, 4],
        ["South", 7, 8, 9, 10],
    ])
    staff = _sheet("Staff", [
        ["Name", "Department", "Salary", "Start date"],
        ["Ann", "Sales", 50000, "2020-01-15"],
        ["Bob", "IT", 60000, "2021-03-01"],
        ["Cid", "IT", 70000, "2019-07-01"],
        ["Dee", "HR", 45000, "2022-11-30"],
        ["Eve", "Sales", 55000, None],
    ])
    hidden = _sheet("Secret", [["Region", "Q1"], ["X", 999]], hidden=True)
    return [sales, staff, hidden]


class TestAggregates:
    def test_sum(self, book):
        r = tq.execute(book, {"aggregate": [{"column": "Q1", "fn": "sum"}]})
        assert r["sheet"] == "Sales"
        assert r["columns"] == ["sum(Q1)"] and r["rows"] == [[23]]

    def test_mean_median_min_max_count_nunique(self, book):
        spec = {"sheet": "Staff", "aggregate": [
            {"column": "Salary", "fn": "mean"}, {"column": "Salary", "fn": "median"},
            {"column": "Salary", "fn": "min"}, {"column": "Salary", "fn": "max"},
            {"column": "Start date", "fn": "count"}, {"column": "Department", "fn": "nunique"},
            {"fn": "count"},
        ]}
        r = tq.execute(book, spec)
        assert r["rows"] == [[56000, 55000, 45000, 70000, 4, 3, 5]]
        assert r["columns"][-1] == "count"

    def test_min_max_on_dates_are_lexical(self, book):
        r = tq.execute(book, {"sheet": "Staff", "aggregate": [
            {"column": "Start date", "fn": "min"}, {"column": "Start date", "fn": "max"}]})
        assert r["rows"] == [["2019-07-01", "2022-11-30"]]

    def test_aliases(self, book):
        r = tq.execute(book, {"sheet": "Staff", "aggregate": [{"column": "salary", "fn": "avg"}]})
        assert r["rows"] == [[56000]]

    def test_group_by(self, book):
        r = tq.execute(book, {"sheet": "Staff", "group_by": ["Department"],
                              "aggregate": [{"column": "Salary", "fn": "sum"}]})
        assert r["columns"] == ["Department", "sum(Salary)"]
        assert r["rows"] == [["HR", 45000], ["IT", 130000], ["Sales", 105000]]

    def test_group_by_without_aggregate_counts(self, book):
        r = tq.execute(book, {"sheet": "Staff", "group_by": "Department"})
        assert r["rows"] == [["HR", 1], ["IT", 2], ["Sales", 2]]


class TestFilters:
    def test_equals_case_insensitive(self, book):
        r = tq.execute(book, {"sheet": "Staff", "filters": [{"column": "Department", "op": "==", "value": "it"}],
                              "select": ["Name"]})
        assert r["rows"] == [["Bob"], ["Cid"]]
        assert r["matched_rows"] == 2

    def test_numeric_comparisons(self, book):
        r = tq.execute(book, {"sheet": "Staff", "filters": [{"column": "Salary", "op": ">=", "value": 55000}],
                              "select": ["Name"]})
        assert [x[0] for x in r["rows"]] == ["Bob", "Cid", "Eve"]
        r = tq.execute(book, {"sheet": "Staff", "filters": [{"column": "Salary", "op": "<", "value": "50000"}],
                              "select": ["Name"]})
        assert r["rows"] == [["Dee"]]

    def test_between_numbers_and_dates(self, book):
        r = tq.execute(book, {"sheet": "Staff", "filters": [
            {"column": "Salary", "op": "between", "value": [50000, 60000]}], "select": ["Name"]})
        assert [x[0] for x in r["rows"]] == ["Ann", "Bob", "Eve"]
        r = tq.execute(book, {"sheet": "Staff", "filters": [
            {"column": "Start date", "op": "between", "value": ["2020-01-01", "2021-12-31"]}], "select": ["Name"]})
        assert [x[0] for x in r["rows"]] == ["Ann", "Bob"]

    def test_contains_in_and_not_equal(self, book):
        r = tq.execute(book, {"sheet": "Staff", "filters": [{"column": "Name", "op": "contains", "value": "e"}],
                              "select": ["Name"]})
        assert [x[0] for x in r["rows"]] == ["Dee", "Eve"]
        r = tq.execute(book, {"sheet": "Staff", "filters": [{"column": "Department", "op": "in", "value": ["HR", "Sales"]}],
                              "aggregate": [{"fn": "count"}]})
        assert r["rows"] == [[3]]
        r = tq.execute(book, {"sheet": "Staff", "filters": [{"column": "Department", "op": "!=", "value": "Sales"}],
                              "aggregate": [{"fn": "count"}]})
        assert r["rows"] == [[3]]

    def test_date_greater_than(self, book):
        r = tq.execute(book, {"sheet": "Staff", "filters": [{"column": "Start date", "op": ">", "value": "2021-01-01"}],
                              "select": ["Name"]})
        assert [x[0] for x in r["rows"]] == ["Bob", "Dee"]


class TestSortLimitRowTotal:
    def test_sort_and_limit(self, book):
        r = tq.execute(book, {"sheet": "Staff", "select": ["Name", "Salary"],
                              "sort": [{"column": "Salary", "desc": True}], "limit": 2})
        assert r["rows"] == [["Cid", 70000], ["Bob", 60000]]
        assert r["row_count"] == 2

    def test_sort_by_aggregate_source_column(self, book):
        r = tq.execute(book, {"sheet": "Staff", "group_by": ["Department"],
                              "aggregate": [{"column": "Salary", "fn": "sum"}],
                              "sort": [{"column": "Salary", "desc": True}], "limit": 1})
        assert r["rows"] == [["IT", 130000]]
        assert r["spec"]["sort"] == [{"column": "sum(Salary)", "desc": True}]

    def test_row_total_for_a_region(self, book):
        spec = {"filters": [{"column": "Region", "op": "==", "value": "East"}],
                "row_total": {"columns": ["Q1", "Q2", "Q3", "Q4"], "as": "Total"},
                "select": ["Region", "Total"]}
        r = tq.execute(book, spec)
        assert r["rows"] == [["East", 100]]

    def test_row_total_range_syntax_and_top(self, book):
        r = tq.execute(book, {"row_total": {"columns": "Q1..Q4", "as": "Year"}, "select": ["Region"],
                              "sort": [{"column": "Year", "desc": True}], "limit": 2})
        assert r["columns"] == ["Region", "Year"]
        assert r["rows"] == [["East", 100], ["South", 34]]
        assert r["spec"]["row_total"]["columns"] == ["Q1", "Q2", "Q3", "Q4"]

    def test_result_cap(self):
        big = _sheet("Big", [["n"]] + [[i] for i in range(200)])
        r = tq.execute([big], {"select": ["n"]})
        assert r["row_count"] == 200
        assert len(r["rows"]) == tq.MAX_RESULT_ROWS
        assert r["truncated"] is True
        assert "… 150 more rows" in r["markdown"]


class TestValidation:
    def test_fuzzy_column_match(self, book):
        assert tq.resolve_column("start_date", ["Name", "Start date"]) == "Start date"
        assert tq.resolve_column("SALARY", ["Name", "Salary"]) == "Salary"
        assert tq.resolve_column("Salry", ["Name", "Salary"]) == "Salary"
        assert tq.resolve_column("dept", ["Name", "Department"]) == "Department"
        r = tq.execute(book, {"sheet": "staff", "aggregate": [{"column": "salaries", "fn": "max"}]})
        assert r["rows"] == [[70000]]

    def test_invalid_column_lists_available(self, book):
        with pytest.raises(tq.QueryError) as ei:
            tq.execute(book, {"sheet": "Staff", "aggregate": [{"column": "Bonus", "fn": "sum"}]})
        msg = str(ei.value)
        assert "Unknown column 'Bonus'" in msg
        assert "Available columns: Name, Department, Salary, Start date" in msg

    def test_invalid_sheet_op_fn(self, book):
        with pytest.raises(tq.QueryError) as ei:
            tq.execute(book, {"sheet": "Budget"})
        assert str(ei.value) == "Unknown sheet 'Budget'. Available sheets: Sales, Staff"   # hidden not listed
        with pytest.raises(tq.QueryError, match="Allowed ops"):
            tq.execute(book, {"filters": [{"column": "Q1", "op": "~=", "value": 1}]})
        with pytest.raises(tq.QueryError, match="Allowed"):
            tq.execute(book, {"aggregate": [{"column": "Q1", "fn": "stddev"}]})
        with pytest.raises(tq.QueryError):
            tq.execute(book, {"filters": [{"column": "Q1", "op": "between", "value": [1]}]})

    def test_sheet_autoselect_by_columns_skips_hidden(self, book):
        r = tq.execute(book, {"aggregate": [{"column": "Salary", "fn": "sum"}]})
        assert r["sheet"] == "Staff"
        r = tq.execute(book, {"aggregate": [{"column": "Q1", "fn": "max"}]})
        assert r["sheet"] == "Sales" and r["rows"] == [[10]]      # not the hidden 999

    def test_parse_spec(self):
        assert tq.parse_spec('```json\n{"limit": 3, "sort": [{"column": "a", "desc": true}],}\n```') == {
            "limit": 3, "sort": [{"column": "a", "desc": True}]}
        assert tq.parse_spec('Here: {"filters": [{"column": "x", "op": "==", "value": "a}b"}]} done')["filters"][0]["value"] == "a}b"
        with pytest.raises(tq.QueryError):
            tq.parse_spec("no json here")

    def test_no_code_execution(self, book):
        """Model output is data only: expressions are just unknown columns."""
        with pytest.raises(tq.QueryError):
            tq.execute(book, {"aggregate": [{"column": "__import__('os').system('echo hi')", "fn": "sum"}]})

    def test_markdown_and_summary(self, book):
        r = tq.execute(book, {"sheet": "Staff", "group_by": ["Department"],
                              "aggregate": [{"column": "Salary", "fn": "mean"}]})
        assert r["markdown"].splitlines()[0] == "| Department | mean(Salary) |"
        assert "| IT | 65000 |" in r["markdown"]
        assert tq.spec_summary(r["spec"]) == "sheet Staff · mean(Salary) by Department"

    def test_schema_text(self, book):
        text = tq.schema_text(book)
        assert 'Sheet "Sales" — 4 rows' in text
        assert "Start date (date)" in text
        assert "Secret" not in text
