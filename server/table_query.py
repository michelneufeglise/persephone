"""
Deterministic spreadsheet queries.

The answer model translates a question into a small JSON spec; this module
validates it against the real sheet schema and executes it with pandas —
no eval/exec of model-generated code, ever.

Spec (every key optional):
    {
      "sheet":     "Sales" | null,
      "filters":   [{"column": "Region", "op": "==", "value": "East"}],
                   # ops: == != > >= < <= contains in between
      "row_total": {"columns": ["Q1", "Q2", "Q3", "Q4"], "as": "Total"},
                   # adds columns only (never multiplies)
      "derive":    [{"as": "Value", "expr": {"op": "mul", "left": "Stock",
                                              "right": "Unit price"}}],
                   # ops: mul div add sub; operands: column | number | expr
      "group_by":  ["Region"],
      "aggregate": [{"column": "Salary", "fn": "mean"}],
                   # fns: sum mean min max count median nunique
      "select":    ["Name", "Salary"],
      "sort":      [{"column": "Salary", "desc": true}],
      "limit":     10
    }

Pipeline: filters → row_total → derive → group/aggregate → sort → limit →
projection (select). Sort keys resolve against the frame *before* the
projection, so "select Name; sort Salary desc; limit 1" works (the sort
column is dropped again afterwards). Hidden sheets are never queried.

``execute(sheets, spec)`` → {"columns", "rows", "row_count", "matched_rows",
"spec", "sheet", "markdown", "truncated"}; raises QueryError with a message
that lists the available columns / sheets when the spec doesn't fit.
"""

from __future__ import annotations

import difflib
import json
import math
import operator
import re
from typing import Any, Optional

import sheets as _sheets

MAX_RESULT_ROWS = 50
OPS = ("==", "!=", ">", ">=", "<", "<=", "contains", "in", "between")
OP_ALIASES = {
    "=": "==", "eq": "==", "equals": "==", "is": "==",
    "<>": "!=", "ne": "!=", "not": "!=",
    "gt": ">", "gte": ">=", "ge": ">=", "lt": "<", "lte": "<=", "le": "<=",
    "like": "contains", "includes": "contains", "has": "contains",
    "one_of": "in", "range": "between",
}
FNS = ("sum", "mean", "min", "max", "count", "median", "nunique")
FN_ALIASES = {
    "avg": "mean", "average": "mean", "total": "sum", "distinct": "nunique",
    "count_distinct": "nunique", "unique": "nunique", "minimum": "min", "maximum": "max",
}


DERIVE_OPS = ("mul", "div", "add", "sub")
DERIVE_OP_ALIASES = {
    "*": "mul", "×": "mul", "x": "mul", "times": "mul", "multiply": "mul", "product": "mul",
    "/": "div", "÷": "div", "divide": "div", "divided_by": "div", "ratio": "div",
    "+": "add", "plus": "add", "sum": "add",
    "-": "sub", "−": "sub", "minus": "sub", "subtract": "sub", "difference": "sub",
}
DERIVE_MAX_DEPTH = 3
DERIVE_MAX_COLUMNS = 10


class QueryError(ValueError):
    """The spec can't be executed against this sheet (bad column/op/…)."""


# ── Spec parsing ─────────────────────────────────────────────────────────────

def parse_spec(text: str) -> dict[str, Any]:
    """The first JSON object in a model reply (code fences / prose tolerated)."""
    if isinstance(text, dict):
        return text
    s = text or ""
    start = s.find("{")
    while start != -1:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(s)):
            ch = s[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    chunk = s[start:i + 1]
                    try:
                        obj = json.loads(chunk)
                    except json.JSONDecodeError:
                        try:
                            obj = json.loads(re.sub(r",\s*([}\]])", r"\1", chunk))
                        except json.JSONDecodeError as exc:
                            raise QueryError(f"Invalid JSON: {exc.msg}") from None
                    if not isinstance(obj, dict):
                        raise QueryError("The query must be a JSON object")
                    return obj
        break
    raise QueryError("No JSON object found in the model output")


def _as_list(v: Any) -> list:
    if v is None or v == "":
        return []
    return list(v) if isinstance(v, (list, tuple)) else [v]


# ── Column / sheet resolution ────────────────────────────────────────────────

def _norm(s: Any) -> str:
    return re.sub(r"[^0-9a-z]+", "", str(s).lower())


def _singular(n: str) -> str:
    if n.endswith("ies") and len(n) > 4:
        return n[:-3] + "y"
    if n.endswith("s") and not n.endswith("ss") and len(n) > 3:
        return n[:-1]
    return n


def _is_subsequence(short: str, long: str) -> bool:
    it = iter(long)
    return all(ch in it for ch in short)


def resolve_column(name: Any, columns: list[str], sheet: str = "") -> str:
    """Case-insensitive / fuzzy match of `name` onto a real column."""
    cols = [str(c) for c in columns]
    if not isinstance(name, str) or not name.strip():
        raise QueryError(f"Missing column name. Available columns: {', '.join(cols)}")
    if name in cols:
        return name
    low = name.strip().lower()
    for c in cols:
        if c.strip().lower() == low:
            return c
    n = _norm(name)
    by_norm: dict[str, list[str]] = {}
    for c in cols:
        by_norm.setdefault(_norm(c), []).append(c)
    if n and n in by_norm and len(by_norm[n]) == 1:
        return by_norm[n][0]
    if n:
        sing = [c for c in cols if _singular(_norm(c)) == _singular(n)]
        if len(sing) == 1:
            return sing[0]
    if n:
        close = difflib.get_close_matches(n, [k for k in by_norm if k], n=2, cutoff=0.8)
        if close and len(by_norm[close[0]]) == 1 and (len(close) == 1 or close[0] != close[1]):
            return by_norm[close[0]][0]
        if len(n) >= 3:
            subs = [c for c in cols if n in _norm(c) or (len(_norm(c)) >= 3 and _norm(c) in n)]
            if len(subs) == 1:
                return subs[0]
            # abbreviations: "dept" → "Department", "qty" → "Quantity"
            abbr = [c for c in cols if _norm(c)[:1] == n[:1] and _is_subsequence(n, _norm(c))]
            if len(abbr) == 1:
                return abbr[0]
    where = f" in sheet '{sheet}'" if sheet else ""
    raise QueryError(f"Unknown column '{name}'{where}. Available columns: {', '.join(cols)}")


def _try_resolve(name: Any, columns: list[str]) -> Optional[str]:
    try:
        return resolve_column(name, columns)
    except QueryError:
        return None


def _normalize_sheets(sheets: Any) -> list[dict[str, Any]]:
    if isinstance(sheets, dict):
        return [{"name": str(k), "df": v, "hidden": False, "dtypes": {}} for k, v in sheets.items()]
    return list(sheets or [])


def _referenced_columns(spec: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for f in _as_list(spec.get("filters")):
        if isinstance(f, dict):
            names.append(f.get("column"))
    names += [g for g in _as_list(spec.get("group_by"))]
    for a in _as_list(spec.get("aggregate")):
        if isinstance(a, dict) and a.get("column") not in (None, "*"):
            names.append(a.get("column"))
    names += [s for s in _as_list(spec.get("select"))]
    rt = spec.get("row_total")
    if isinstance(rt, dict):
        names += [c for c in _as_list(rt.get("columns")) if isinstance(c, str) and ".." not in c]
    for d in _as_list(spec.get("derive")):
        if isinstance(d, dict):
            names += _expr_columns(d.get("expr"))
    return [n for n in names if isinstance(n, str) and n.strip()]


def _expr_columns(expr: Any, depth: int = 0) -> list[str]:
    if depth > DERIVE_MAX_DEPTH:
        return []
    if isinstance(expr, str):
        return [expr]
    if isinstance(expr, dict):
        return _expr_columns(expr.get("left"), depth + 1) + _expr_columns(expr.get("right"), depth + 1)
    return []


def _match_sheet(sheets: list[dict[str, Any]], want: str) -> Optional[dict[str, Any]]:
    for s in sheets:
        if s["name"] == want or s["name"].strip().lower() == want.strip().lower():
            return s
    n = _norm(want)
    hits = [s for s in sheets if _norm(s["name"]) == n]
    if len(hits) == 1:
        return hits[0]
    close = difflib.get_close_matches(n, [_norm(s["name"]) for s in sheets], n=1, cutoff=0.8)
    if close:
        return next(s for s in sheets if _norm(s["name"]) == close[0])
    return None


def pick_sheet(sheets: list[dict[str, Any]], spec: dict[str, Any]) -> dict[str, Any]:
    """The sheet a spec runs on. Hidden sheets are never selected — not even
    when named — and never listed in error messages."""
    if not sheets:
        raise QueryError("The workbook has no sheets")
    visible = [s for s in sheets if not s.get("hidden")]
    names = [s["name"] for s in visible]
    want = spec.get("sheet")
    if isinstance(want, str) and want.strip():
        hit = _match_sheet(visible, want)
        if hit is not None:
            return hit
        hidden = [s for s in sheets if s.get("hidden")]
        if hidden and _match_sheet(hidden, want) is not None:
            raise QueryError(
                f"Sheet '{want}' is hidden and not available. "
                f"Available sheets: {', '.join(names) or '(none)'}"
            )
        raise QueryError(f"Unknown sheet '{want}'. Available sheets: {', '.join(names) or '(none)'}")
    if not visible:
        raise QueryError("The workbook has no visible sheets")
    if len(visible) == 1:
        return visible[0]
    refs = _referenced_columns(spec)
    best, best_score = visible[0], -1
    for s in visible:
        cols = [str(c) for c in s["df"].columns]
        score = sum(1 for r in refs if _try_resolve(r, cols))
        if score > best_score:
            best, best_score = s, score
    return best


# ── Execution ────────────────────────────────────────────────────────────────

def _is_num_value(v: Any) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return not (isinstance(v, float) and math.isnan(v))
    if isinstance(v, str):
        c = _sheets.coerce_text_number(v)
        return isinstance(c, (int, float)) and not isinstance(c, bool)
    return False


def _num(v: Any) -> float:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    return float(_sheets.coerce_text_number(str(v)))


def _to_numeric(series):
    import pandas as pd
    if pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series):
        return series.astype(float)
    return pd.to_numeric(
        series.map(lambda v: _sheets.coerce_text_number(v) if isinstance(v, str) else v),
        errors="coerce",
    )


def _str_key(v: Any) -> Optional[str]:
    v = _sheets.json_value(v)
    if v is None:
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return _sheets.fmt_num(v).lower()
    return str(v).strip().lower()


def _apply_filter(df, f: dict[str, Any], sheet_name: str):
    import pandas as pd
    if not isinstance(f, dict):
        raise QueryError("Each filter must be an object {column, op, value}")
    col = resolve_column(f.get("column"), list(df.columns), sheet_name)
    op_raw = str(f.get("op", "==")).strip().lower()
    op = OP_ALIASES.get(op_raw, op_raw)
    if op not in OPS:
        raise QueryError(f"Unknown filter op '{f.get('op')}'. Allowed ops: {', '.join(OPS)}")
    value = f.get("value")
    s = df[col]
    if op in ("==", "!="):
        if isinstance(value, list):
            raise QueryError(f"Filter '{op}' on '{col}' needs a single value (use 'in' for a list)")
        if _is_num_value(value) and not isinstance(value, str) or (
            _is_num_value(value) and pd.api.types.is_numeric_dtype(s)
        ):
            mask = _to_numeric(s) == _num(value)
        elif value is None:
            mask = s.isna()
        else:
            key = _str_key(value)
            mask = s.map(_str_key) == key
        if op == "!=":
            mask = ~mask
    elif op in (">", ">=", "<", "<="):
        if value is None or isinstance(value, (list, dict)):
            raise QueryError(f"Filter '{op}' on '{col}' needs a single number or date value")
        cmp = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le}[op]
        if _is_num_value(value):
            left = _to_numeric(s)
            mask = left.notna() & cmp(left, _num(value))
        else:
            right = str(value)
            mask = s.map(lambda v: _sheets.json_value(v) is not None and cmp(str(_sheets.json_value(v)), right))
    elif op == "contains":
        needles = [str(v).lower() for v in _as_list(value) if v is not None]
        if not needles:
            raise QueryError(f"Filter 'contains' on '{col}' needs a value")
        text = s.map(lambda v: "" if _sheets.json_value(v) is None else str(_sheets.json_value(v)).lower())
        mask = text.map(lambda t: any(n in t for n in needles))
    elif op == "in":
        vals = _as_list(value)
        if not vals:
            raise QueryError(f"Filter 'in' on '{col}' needs a list of values")
        if all(_is_num_value(v) for v in vals) and pd.api.types.is_numeric_dtype(s):
            mask = _to_numeric(s).isin([_num(v) for v in vals])
        else:
            keys = {_str_key(v) for v in vals}
            mask = s.map(_str_key).isin(keys)
    else:  # between
        vals = _as_list(value)
        if len(vals) != 2:
            raise QueryError(f"Filter 'between' on '{col}' needs [low, high]")
        lo, hi = vals
        if _is_num_value(lo) and _is_num_value(hi):
            left = _to_numeric(s)
            mask = left.notna() & (left >= _num(lo)) & (left <= _num(hi))
        else:
            lo_s, hi_s = str(lo), str(hi)

            def _in_range(v: Any) -> bool:
                jv = _sheets.json_value(v)
                if jv is None:
                    return False
                t = str(jv)
                # an ISO date upper bound includes that whole day ("2021-12-31 10:00")
                return lo_s <= t and (t <= hi_s or t.startswith(hi_s))

            mask = s.map(_in_range)
    return df[mask.fillna(False).astype(bool)]


def _expand_row_total_columns(raw: Any, columns: list[str], sheet_name: str) -> list[str]:
    items = _as_list(raw)
    out: list[str] = []
    for it in items:
        if isinstance(it, str) and (".." in it or re.match(r"^[^:]+:[^:]+$", it) and it not in columns):
            a, b = re.split(r"\.\.|:", it, maxsplit=1)
            ca = resolve_column(a.strip(), columns, sheet_name)
            cb = resolve_column(b.strip(), columns, sheet_name)
            ia, ib = sorted((columns.index(ca), columns.index(cb)))
            out.extend(columns[ia:ib + 1])
        else:
            out.append(resolve_column(it, columns, sheet_name))
    return list(dict.fromkeys(out))


def _agg_name(fn: str, col: Optional[str]) -> str:
    return "count" if (fn == "count" and not col) else f"{fn}({col})"


def _aggregate_series(series, fn: str, numeric_hint: bool):
    import pandas as pd
    if fn == "count":
        return int(series.notna().sum())
    if fn == "nunique":
        return int(series.nunique(dropna=True))
    if fn in ("sum", "mean", "median"):
        num = _to_numeric(series).dropna()
        if num.empty:
            return None
        return getattr(num, fn)()
    # min / max: numeric when the column is numeric-ish, else lexical (ISO dates)
    num = _to_numeric(series)
    if numeric_hint or (num.notna().sum() and num.notna().sum() >= series.notna().sum() * 0.8):
        num = num.dropna()
        return None if num.empty else getattr(num, fn)()
    txt = series.dropna().map(lambda v: str(_sheets.json_value(v)))
    return None if txt.empty else getattr(txt, fn)()


_STR_EXPR_RE = re.compile(r"^(.+?)\s+([*×x/÷+\-−])\s+(.+)$")


def _operand(v: Any, df, columns: list[str], sheet_name: str, depth: int):
    """A derive operand → a float Series (column / nested expr) or a float."""
    if isinstance(v, dict):
        return _eval_expr(v, df, columns, sheet_name, depth + 1)
    if isinstance(v, bool) or v is None:
        raise QueryError("derive operands must be a column name, a number or a nested expr")
    if isinstance(v, (int, float)):
        if isinstance(v, float) and math.isnan(v):
            raise QueryError("derive operands must be a column name, a number or a nested expr")
        return float(v)
    if isinstance(v, str):
        col = _try_resolve(v, columns)
        if col is not None:
            return _to_numeric(df[col])
        c = _sheets.coerce_text_number(v)
        if isinstance(c, (int, float)) and not isinstance(c, bool):
            return float(c)
        m = _STR_EXPR_RE.match(v.strip())
        if m and depth < DERIVE_MAX_DEPTH:
            return _eval_expr({"op": m.group(2), "left": m.group(1), "right": m.group(3)},
                              df, columns, sheet_name, depth + 1)
        resolve_column(v, columns, sheet_name)   # raises with the column list
    raise QueryError("derive operands must be a column name, a number or a nested expr")


def _eval_expr(expr: Any, df, columns: list[str], sheet_name: str, depth: int = 1):
    """Safe arithmetic over columns: only mul/div/add/sub, nesting ≤ 3."""
    import pandas as pd
    if depth > DERIVE_MAX_DEPTH:
        raise QueryError(f"derive expressions may be nested at most {DERIVE_MAX_DEPTH} levels deep")
    if isinstance(expr, str):
        m = _STR_EXPR_RE.match(expr.strip())
        if not m:
            raise QueryError('derive expr must be {"op": "mul|div|add|sub", "left": ..., "right": ...}')
        expr = {"op": m.group(2), "left": m.group(1), "right": m.group(3)}
    if not isinstance(expr, dict):
        raise QueryError('derive expr must be {"op": "mul|div|add|sub", "left": ..., "right": ...}')
    op_raw = str(expr.get("op") or "").strip().lower()
    op = DERIVE_OP_ALIASES.get(op_raw, op_raw)
    if op not in DERIVE_OPS:
        raise QueryError(f"Unknown derive op '{expr.get('op')}'. Allowed ops: {', '.join(DERIVE_OPS)}")
    if "left" not in expr or "right" not in expr:
        raise QueryError(f"derive op '{op}' needs both \"left\" and \"right\"")
    left = _operand(expr.get("left"), df, columns, sheet_name, depth)
    right = _operand(expr.get("right"), df, columns, sheet_name, depth)
    if not isinstance(left, pd.Series) and not isinstance(right, pd.Series):
        left = pd.Series(float(left), index=df.index)
    if op == "mul":
        out = left * right
    elif op == "add":
        out = left + right
    elif op == "sub":
        out = left - right
    else:
        if isinstance(right, pd.Series):
            right = right.where(right != 0)
        elif right == 0:
            right = float("nan")
        out = left / right
    if not isinstance(out, pd.Series):  # pragma: no cover - defensive
        out = pd.Series(out, index=df.index)
    return out.astype(float)


def _expr_text(expr: Any, depth: int = 0) -> str:
    sym = {"mul": "×", "div": "÷", "add": "+", "sub": "−"}
    if isinstance(expr, dict) and depth <= DERIVE_MAX_DEPTH:
        op_raw = str(expr.get("op") or "").strip().lower()
        op = DERIVE_OP_ALIASES.get(op_raw, op_raw)
        left, right = _expr_text(expr.get("left"), depth + 1), _expr_text(expr.get("right"), depth + 1)
        return f"({left} {sym.get(op, op)} {right})" if depth else f"{left} {sym.get(op, op)} {right}"
    if isinstance(expr, (int, float)) and not isinstance(expr, bool):
        return _sheets.fmt_num(expr)
    return str(expr)


def _resolved_expr(expr: Any, columns: list[str], depth: int = 0) -> Any:
    """The expr with fuzzy column names replaced by the real ones (for the
    resolved spec shown in the tile)."""
    if isinstance(expr, dict) and depth <= DERIVE_MAX_DEPTH:
        op_raw = str(expr.get("op") or "").strip().lower()
        return {
            "op": DERIVE_OP_ALIASES.get(op_raw, op_raw),
            "left": _resolved_expr(expr.get("left"), columns, depth + 1),
            "right": _resolved_expr(expr.get("right"), columns, depth + 1),
        }
    if isinstance(expr, str):
        return _try_resolve(expr, columns) or expr
    return expr


def _matching_total_column(sheet: dict[str, Any], source_cols: list[str],
                           wanted: str, columns: list[str]) -> Optional[str]:
    """An existing column that already holds this row total (a formula column
    or a column named like the requested total whose values equal the sum of
    the requested columns on every row) — reused instead of a duplicate."""
    import pandas as pd
    base = sheet["df"]
    formula = [c for c in (sheet.get("formula_columns") or []) if c in columns]
    named = [c for c in columns if _norm(c) in (_norm(wanted), "total")]
    candidates = list(dict.fromkeys(formula + named))
    full = sum((_to_numeric(base[c]).fillna(0) for c in source_cols),
               start=pd.Series(0.0, index=base.index))
    for c in candidates:
        if c in source_cols or c not in base.columns:
            continue
        existing = _to_numeric(base[c])
        both = existing.notna()
        if not both.any() or both.sum() < max(1, int(len(base) * 0.9)):
            continue
        if ((existing[both] - full[both]).abs() <= 1e-6 * (1 + full[both].abs())).all():
            return c
    return None


def execute(sheets: Any, spec: dict[str, Any], max_rows: int = MAX_RESULT_ROWS) -> dict[str, Any]:
    import pandas as pd

    if not isinstance(spec, dict):
        raise QueryError("The query must be a JSON object")
    sheet_list = _normalize_sheets(sheets)
    sheet = pick_sheet(sheet_list, spec)
    sname = sheet["name"]
    df = sheet["df"].copy()
    dtypes = dict(sheet.get("dtypes") or {})
    columns = [str(c) for c in df.columns]
    df.columns = columns
    source_columns = list(columns)

    resolved: dict[str, Any] = {"sheet": sname}
    computed_cols: list[str] = []      # row_total / derive outputs (auto-selected)

    # 1. filters
    rfilters = []
    for f in _as_list(spec.get("filters")):
        df = _apply_filter(df, f, sname)
        rfilters.append({**f, "column": resolve_column(f.get("column"), columns, sname)})
    if rfilters:
        resolved["filters"] = rfilters
    matched_rows = int(len(df))

    # 2. row-wise total (wide tables: Q1..Q4 per row) — additions only
    rt = spec.get("row_total")
    if rt:
        if not isinstance(rt, dict):
            raise QueryError('row_total must be {"columns": [...], "as": "Total"}')
        cols = _expand_row_total_columns(rt.get("columns"), columns, sname) if rt.get("columns") else [
            c for c in columns if dtypes.get(c) == "number" or pd.api.types.is_numeric_dtype(df[c])
        ]
        if not cols:
            raise QueryError(f"row_total needs columns. Available columns: {', '.join(columns)}")
        wanted = str(rt.get("as") or "Total").strip() or "Total"
        existing = _matching_total_column(sheet, cols, wanted, source_columns)
        if existing is not None:
            rt_name = existing
            resolved["row_total"] = {"columns": cols, "as": rt_name, "existing": True}
        else:
            rt_name = wanted
            if rt_name in columns:
                rt_name = f"{rt_name} (computed)"
            df[rt_name] = sum((_to_numeric(df[c]).fillna(0) for c in cols), start=pd.Series(0.0, index=df.index))
            columns = columns + [rt_name]
            dtypes[rt_name] = "number"
            resolved["row_total"] = {"columns": cols, "as": rt_name}
        computed_cols.append(rt_name)

    # 3. derived columns (products / ratios / differences)
    derive_raw = _as_list(spec.get("derive"))
    if isinstance(spec.get("derive"), dict):
        derive_raw = [spec["derive"]]
    if len(derive_raw) > DERIVE_MAX_COLUMNS:
        raise QueryError(f"At most {DERIVE_MAX_COLUMNS} derived columns")
    rderive = []
    for d in derive_raw:
        if not isinstance(d, dict):
            raise QueryError('Each derive must be {"as": "<name>", "expr": {"op": ..., "left": ..., "right": ...}}')
        name = str(d.get("as") or d.get("name") or "").strip()
        if not name:
            raise QueryError('Each derive needs a name: {"as": "<name>", "expr": {...}}')
        if name in columns or any(c.strip().lower() == name.lower() for c in columns):
            raise QueryError(f"derive name '{name}' is already a column; choose another name")
        expr = d.get("expr", d.get("expression"))
        if expr is None and "op" in d:
            expr = {k: d.get(k) for k in ("op", "left", "right")}
        df[name] = _eval_expr(expr, df, columns, sname)
        columns = columns + [name]
        dtypes[name] = "number"
        computed_cols.append(name)
        rderive.append({"as": name, "expr": _resolved_expr(expr, columns)})
    if rderive:
        resolved["derive"] = rderive

    group_by = [resolve_column(g, columns, sname) for g in _as_list(spec.get("group_by"))]
    aggs_raw = _as_list(spec.get("aggregate"))
    aggs: list[tuple[str, Optional[str], str]] = []   # (fn, column|None, output name)
    for a in aggs_raw:
        if isinstance(a, str):
            a = {"fn": a}
        if not isinstance(a, dict):
            raise QueryError('Each aggregate must be {"column": ..., "fn": ...}')
        fn = str(a.get("fn") or a.get("func") or a.get("agg") or "").strip().lower()
        fn = FN_ALIASES.get(fn, fn)
        if fn not in FNS:
            raise QueryError(f"Unknown aggregate fn '{a.get('fn')}'. Allowed: {', '.join(FNS)}")
        col_raw = a.get("column")
        col = None if col_raw in (None, "", "*") else resolve_column(col_raw, columns, sname)
        if col is None and fn != "count":
            raise QueryError(f"Aggregate '{fn}' needs a column. Available columns: {', '.join(columns)}")
        aggs.append((fn, col, _agg_name(fn, col)))
    if group_by and not aggs:
        aggs = [("count", None, "count")]
    if group_by:
        resolved["group_by"] = group_by
    if aggs:
        resolved["aggregate"] = [{"column": c, "fn": f} for f, c, _n in aggs]

    # 4. group / aggregate (row mode keeps every column until the projection)
    sel: list[str] = []
    if aggs:
        if group_by:
            rows_out = []
            grouped = df.groupby(group_by, dropna=False, sort=True)
            for keys, sub in grouped:
                keys = keys if isinstance(keys, tuple) else (keys,)
                row = list(keys)
                for fn, col, _name in aggs:
                    if col is None:
                        row.append(int(len(sub)))
                    else:
                        row.append(_aggregate_series(sub[col], fn, dtypes.get(col) == "number"))
                rows_out.append(row)
            out = pd.DataFrame(rows_out, columns=group_by + [n for _f, _c, n in aggs])
        else:
            row = []
            for fn, col, _name in aggs:
                row.append(int(len(df)) if col is None else _aggregate_series(df[col], fn, dtypes.get(col) == "number"))
            out = pd.DataFrame([row], columns=[n for _f, _c, n in aggs])
        out = out.loc[:, ~pd.Index(out.columns).duplicated()]
    else:
        sel = [resolve_column(c, columns, sname) for c in _as_list(spec.get("select"))]
        if sel:
            for c in computed_cols:
                if c not in sel:
                    sel.append(c)
            sel = list(dict.fromkeys(sel))
            resolved["select"] = sel
        out = df

    # 5. sort — keys resolve against the pre-projection frame
    out_cols = [str(c) for c in out.columns]
    rsort: list[dict[str, Any]] = []
    for srt in reversed(_as_list(spec.get("sort"))):
        if isinstance(srt, str):
            srt = {"column": srt}
        if not isinstance(srt, dict):
            raise QueryError('Each sort must be {"column": ..., "desc": true|false}')
        key = srt.get("column")
        col = _try_resolve(key, out_cols)
        if col is None and aggs:
            src = _try_resolve(key, columns)
            col = next((n for _f, c, n in aggs if c == src), None) if src else None
        if col is None:
            raise QueryError(f"Unknown sort column '{key}'. Result columns: {', '.join(out_cols)}")
        desc = bool(srt.get("desc") or str(srt.get("order", "")).lower().startswith("desc"))
        rsort.insert(0, {"column": col, "desc": desc})
        num = _to_numeric(out[col])
        use_num = num.notna().sum() >= max(1, out[col].notna().sum() * 0.8)
        try:
            out = out.sort_values(
                col, ascending=not desc, kind="mergesort", na_position="last",
                key=(lambda s: _to_numeric(s)) if use_num else (lambda s: s.map(lambda v: _str_key(v) or "")),
            )
        except Exception as exc:  # pragma: no cover - defensive
            raise QueryError(f"Could not sort by '{col}': {exc}") from None

    # 6. limit
    limit = spec.get("limit")
    if limit not in (None, ""):
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            raise QueryError("limit must be an integer") from None
        if limit < 0:
            raise QueryError("limit must be ≥ 0")
        out = out.head(limit)
        resolved["limit"] = limit
    if rsort:
        resolved["sort"] = rsort

    # 7. projection last (sort-only columns are dropped again here)
    if not aggs:
        if sel:
            out = out[sel]
        elif computed_cols:
            # no select: keep the source columns + the computed ones
            out = out[list(dict.fromkeys(source_columns + computed_cols))]
        out = out.loc[:, ~pd.Index(out.columns).duplicated()]

    row_count = int(len(out))
    shown = out.head(max_rows)
    rows = [[_sheets.json_value(v) for v in r] for r in shown.itertuples(index=False, name=None)]
    cols_out = [str(c) for c in out.columns]
    numeric = {c for c in cols_out if pd.api.types.is_numeric_dtype(out[c])}
    markdown = _sheets.markdown_table(cols_out, rows, numeric)
    if row_count > len(rows):
        markdown += f"\n… {row_count - len(rows)} more rows"
    return {
        "columns": cols_out,
        "rows": rows,
        "row_count": row_count,
        "matched_rows": matched_rows,
        "spec": resolved,
        "sheet": sname,
        "markdown": markdown,
        "truncated": row_count > len(rows),
    }


# ── Question-aware validation ────────────────────────────────────────────────

_PRODUCT_RATIO_RE = re.compile(
    r"×|÷|\*|(?:^|[\s(])x(?=[\s)])|\btimes\b|\bmultipl\w*|\bper unit\b|\bratio\w*|\bpercent\w*|%"
    r"|\bshare\b|\bdivid\w*|\bproduct of\b|\bprice\s*[×x*]\s*quantity\b|\bquantity\s*[×x*]\s*price\b"
    r"|\bkeer\b|\bvermenigvuldig\w*|\bverhouding\b|\bpercentage\b|\baandeel\b",
    re.IGNORECASE,
)


def product_ratio_hint(question: str) -> bool:
    """Does the question ask for a product / ratio / percentage?"""
    return bool(_PRODUCT_RATIO_RE.search(question or ""))


def check_spec_for_question(question: str, spec: dict[str, Any]) -> None:
    """Question-aware validation: a product/ratio question answered with
    row_total (which only adds) and no derive is rejected so the model
    retries with "derive"."""
    if not isinstance(spec, dict):
        return
    if spec.get("row_total") and not spec.get("derive") and product_ratio_hint(question):
        raise QueryError(
            "use derive for products/ratios: row_total ONLY adds columns. "
            'Use "derive": [{"as": "Value", "expr": {"op": "mul", "left": "<column>", "right": "<column>"}}] '
            "(then aggregate the derived column if a total is asked)"
        )


# ── Prompt helpers ───────────────────────────────────────────────────────────

def _v(v: Any) -> str:
    v = _sheets.json_value(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    return "null" if v is None else (_sheets.fmt_num(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else str(v))


def spec_summary(spec: dict[str, Any]) -> str:
    """One-line human summary of a spec for the UI tile."""
    parts: list[str] = []
    if spec.get("sheet"):
        parts.append(f"sheet {spec['sheet']}")
    for f in _as_list(spec.get("filters")):
        if isinstance(f, dict):
            val = f.get("value")
            vs = "[" + ", ".join(_v(x) for x in val) + "]" if isinstance(val, list) else _v(val)
            parts.append(f"where {f.get('column')} {f.get('op', '==')} {vs}")
    rt = spec.get("row_total")
    if isinstance(rt, dict):
        cols = _as_list(rt.get("columns"))
        if rt.get("existing"):
            parts.append(f"{rt.get('as')} (existing column = {' + '.join(str(c) for c in cols)})")
        else:
            parts.append(f"{rt.get('as') or 'Total'} = {' + '.join(str(c) for c in cols) or 'numeric columns'}")
    for d in ([spec["derive"]] if isinstance(spec.get("derive"), dict) else _as_list(spec.get("derive"))):
        if isinstance(d, dict):
            parts.append(f"{d.get('as') or d.get('name') or 'value'} = {_expr_text(d.get('expr', d.get('expression')))}")
    aggs = []
    for a in _as_list(spec.get("aggregate")):
        if isinstance(a, dict):
            fn = str(a.get("fn") or "").lower()
            aggs.append(_agg_name(FN_ALIASES.get(fn, fn), None if a.get("column") in (None, "", "*") else a.get("column")))
    gb = _as_list(spec.get("group_by"))
    if aggs:
        parts.append(", ".join(aggs) + (f" by {', '.join(map(str, gb))}" if gb else ""))
    elif gb:
        parts.append(f"count by {', '.join(map(str, gb))}")
    sel = _as_list(spec.get("select"))
    if sel and not aggs:
        parts.append("select " + ", ".join(map(str, sel)))
    for s in _as_list(spec.get("sort")):
        if isinstance(s, dict):
            parts.append(f"sort {s.get('column')} {'desc' if s.get('desc') else 'asc'}")
        elif isinstance(s, str):
            parts.append(f"sort {s}")
    if spec.get("limit") not in (None, ""):
        parts.append(f"limit {spec.get('limit')}")
    return " · ".join(parts) or "all rows"


def hidden_sheet_note(sheets: Any) -> str:
    """'Hidden sheet(s): N (not shown)' — a count only, never names/contents."""
    n = sum(1 for s in _normalize_sheets(sheets) if s.get("hidden"))
    return f"Hidden sheet(s): {n} (not shown)" if n else ""


def schema_text(sheets: Any, sample_rows: int = 3, max_cols: int = 60) -> str:
    """Sheet schemas (name, rows, columns with dtypes, a few sample rows) for
    the query-writing prompt. Hidden sheets appear only as a count."""
    out: list[str] = []
    for s in _normalize_sheets(sheets):
        if s.get("hidden"):
            continue
        df = s["df"]
        dtypes = s.get("dtypes") or {}
        cols = [str(c) for c in df.columns][:max_cols]
        col_desc = ", ".join(f"{c} ({dtypes.get(c, 'text')})" for c in cols)
        more = len(df.columns) - len(cols)
        lines = [f'Sheet "{s["name"]}" — {len(df)} rows', f"Columns: {col_desc}" + (f", … {more} more" if more > 0 else "")]
        formula = [c for c in (s.get("formula_columns") or []) if c in cols]
        if formula:
            lines.append("Formula columns (already computed per row): " + ", ".join(formula))
        if len(df):
            lines.append("Sample rows:")
            for r in df[cols].head(sample_rows).itertuples(index=False, name=None):
                lines.append("  " + json.dumps([_sheets.json_value(v) for v in r], ensure_ascii=False, default=str))
        out.append("\n".join(lines))
    note = hidden_sheet_note(sheets)
    if note:
        out.append(note)
    return "\n\n".join(out)


def render_row(columns: list[str], row: list[Any], max_len: int = 160) -> str:
    parts = []
    for c, v in zip(columns, row):
        v = _sheets.json_value(v)
        vs = "—" if v is None else (_sheets.fmt_num(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else str(v))
        parts.append(f"{c}: {vs}")
    s = " · ".join(parts)
    return s if len(s) <= max_len else s[:max_len - 1] + "…"
