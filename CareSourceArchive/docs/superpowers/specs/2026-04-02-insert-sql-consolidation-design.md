# INSERT SQL Consolidation — Design Spec

**Date:** 2026-04-02
**Scope:** `src/utils.py`, `src/audit.py`, `src/scanner.py`, `tests/unit/test_utils.py`
**Goal:** Eliminate duplicated SQL quoting helpers and hand-built INSERT string assembly across audit and scanner modules.

---

## Problem

Six private SQL quoting functions are split across two files doing the same work under different names:

| `src/audit.py` | `src/scanner.py` | Purpose |
|----------------|-------------------|---------|
| `_sql_quote(s)` | `_sql_str(value)` | Quote a string, escape `'` |
| `_sql_str_or_null(val)` | (inline None checks) | String or NULL |
| `_sql_bigint(val)` | — | Strict int cast |
| `_sql_bigint_or_null(val)` | — | Int or NULL |
| — | `_sql_bool(b)` | `true` / `false` literal |

Additionally, four methods hand-build `INSERT INTO ... VALUES (...)` SQL via multi-line f-strings with 15-16 interpolated values each:

- `audit.AuditLogger.log_archive` — 15 columns, single row
- `audit.AuditLogger.log_rehydrate` — 16 columns, single row
- `scanner.write_staging` — 13 columns, multi-row batch
- `scanner.write_scanner_log` — 19 columns, multi-row batch

Both audit methods also repeat a 6-value "job context tail" (`current_user()`, `workspace_id`, `job_id`, `job_run_id`, `task_run_id`, `current_timestamp()`).

---

## Design

### 1. Unified quoting helpers in `src/utils.py`

Add 6 pure functions:

```python
def sql_quote(s: str) -> str:
    """Quote a string value for SQL, escaping single quotes."""
    return "'" + str(s).replace("'", "''") + "'"

def sql_str_or_null(val) -> str:
    """Quote a string value or return NULL."""
    if val is None:
        return "NULL"
    return sql_quote(str(val))

def sql_int(val: int) -> str:
    """Cast to int for SQL."""
    return str(int(val))

def sql_int_or_null(val) -> str:
    """Cast to int or return NULL."""
    if val is None:
        return "NULL"
    return str(int(val))

def sql_bool(b: bool) -> str:
    """SQL boolean literal."""
    return "true" if b else "false"

def sql_expr(expr: str) -> str:
    """Passthrough for fixed internal SQL expressions like current_timestamp().
    Not for user-supplied input — no escaping is applied."""
    return expr
```

### 2. INSERT builder functions in `src/utils.py`

```python
def build_insert_values_sql(table: str, columns: list[str], values: list[str]) -> str:
    """Build a single-row INSERT INTO ... VALUES (...) statement.

    Args:
        table: Fully-qualified table name.
        columns: Column names.
        values: Already-quoted SQL value strings (same length as columns).

    Raises:
        ValueError: If columns and values have different lengths, or if either is empty.
    """
    if not columns:
        raise ValueError("columns must not be empty")
    if len(columns) != len(values):
        raise ValueError(f"columns ({len(columns)}) and values ({len(values)}) length mismatch")
    cols = ", ".join(columns)
    vals = ", ".join(values)
    return f"INSERT INTO {table} ({cols}) VALUES ({vals})"

def build_multi_insert_values_sql(table: str, columns: list[str], rows: list[list[str]]) -> str:
    """Build a multi-row INSERT INTO ... VALUES (...), (...) statement.

    Args:
        table: Fully-qualified table name.
        columns: Column names.
        rows: List of value lists, each already-quoted (same length as columns).

    Raises:
        ValueError: If rows is empty, columns is empty, or any row length mismatches columns.
    """
    if not columns:
        raise ValueError("columns must not be empty")
    if not rows:
        raise ValueError("rows must not be empty")
    for i, row in enumerate(rows):
        if len(row) != len(columns):
            raise ValueError(f"row {i} has {len(row)} values, expected {len(columns)}")
    cols = ", ".join(columns)
    row_strs = [f"({', '.join(row)})" for row in rows]
    return f"INSERT INTO {table} ({cols}) VALUES {', '.join(row_strs)}"
```

### 3. Changes to `src/audit.py`

**Delete:** `_sql_quote`, `_sql_str_or_null`, `_sql_bigint`, `_sql_bigint_or_null` (4 functions).

**Add imports from `src/utils`:**
`sql_quote`, `sql_str_or_null`, `sql_int`, `sql_int_or_null`, `sql_expr`, `build_insert_values_sql`

**Add module-level column constants:**

```python
ARCHIVE_AUDIT_COLUMNS = [
    "audit_id", "archive_run_id", "table_name", "year", "status",
    "record_count", "conditions_applied", "null_date_count", "error_message",
    "archived_by", "workspace_id", "job_id", "job_run_id", "task_run_id",
    "created_at",
]

REHYDRATION_AUDIT_COLUMNS = [
    "audit_id", "archive_run_id", "archive_path", "source_table",
    "target_catalog", "target_schema", "years", "tables_created",
    "status", "error_message", "rehydrated_by", "workspace_id",
    "job_id", "job_run_id", "task_run_id", "created_at",
]
```

**Add private method to `AuditLogger`:**

```python
def _job_context_values(self) -> list[str]:
    jc = self._ctx.job_context
    return [
        sql_expr("current_user()"),
        sql_str_or_null(jc.get("workspace_id")),
        sql_str_or_null(jc.get("job_id")),
        sql_str_or_null(jc.get("job_run_id")),
        sql_str_or_null(jc.get("task_run_id")),
        sql_expr("current_timestamp()"),
    ]
```

**Refactor `log_archive`:** Build values list using helpers + `_job_context_values()`, call `build_insert_values_sql`.

**Refactor `log_rehydrate`:** Same pattern.

**Mechanical rename only (logic unchanged):** `check_resume_state` and `check_concurrent` use `_sql_quote` — replace with imported `sql_quote`. No behavioral change.

**No changes to:** `log_dry_run` (delegates to `log_archive`), `ensure_archive_audit_table`, `ensure_rehydration_audit_table`.

### 4. Changes to `src/scanner.py`

**Delete:** `_sql_str`, `_sql_bool` (2 functions).

**Add imports from `src/utils`:**
`sql_quote`, `sql_str_or_null`, `sql_bool`, `sql_expr`, `build_multi_insert_values_sql`

**Refactor `write_staging` (line 159-184):**
- Loop builds each row as a list of quoted values using shared helpers
- Pass list of rows to `build_multi_insert_values_sql`

**Refactor `write_scanner_log` (line ~308-342):**
- Same pattern: per-row value lists → `build_multi_insert_values_sql`

**Mechanical rename in `merge_staging_to_final` (line 187-225):** Uses `_sql_str` in 6 places inside MERGE and UPDATE statements. Replace each with `sql_quote` (equivalent behavior). No structural change — same SQL output.

**Preserve `CAST(NULL AS STRING)` in `write_staging`:** The `date_column` NULL case currently emits `CAST(NULL AS STRING)` (typed NULL), not plain `NULL`. The refactored code must preserve this — use `sql_expr("CAST(NULL AS STRING)")` instead of `sql_str_or_null(None)` for this specific field.

**No changes to:** `_list_table_columns`, scanning/matching logic.

### 5. Test plan

**Existing tests (no changes expected):**
- `tests/unit/test_audit.py` — 23 tests assert on SQL strings passed to `mock_spark.sql()`. Output is identical since we're changing assembly, not output.
- `tests/unit/test_scanner.py` — 23 tests, same reasoning.

**New tests in `tests/unit/test_utils.py`:**
- `sql_quote` — normal string, string with single quotes, empty string
- `sql_str_or_null` — string value, None
- `sql_int` — valid int, float-that-is-int, raises on non-numeric
- `sql_int_or_null` — int value, None
- `sql_bool` — True, False
- `sql_expr` — passthrough
- `build_insert_values_sql` — correct SQL shape, raises `ValueError` on column/value count mismatch, raises on empty columns
- `build_multi_insert_values_sql` — single row, multiple rows, raises `ValueError` on empty rows, raises on row length mismatch

**Verification:** Run full test suite (`uv run pytest`) after changes — all 145 existing + new tests must pass.

### 6. Files touched

| File | Action |
|------|--------|
| `src/utils.py` | Add 8 functions |
| `src/audit.py` | Delete 4 helpers, add imports + 2 constants + 1 method, refactor 2 methods |
| `src/scanner.py` | Delete 2 helpers, add imports, refactor 2 functions, mechanical rename in `merge_staging_to_final` |
| `tests/unit/test_utils.py` | Add ~12-15 tests for new helpers |

### 7. Out of scope

- `src/archiver.py` INSERT-SELECT in `_archive_year` — structurally different pattern, tracked in `docs/tracker.md` backlog (Category C)
- `notebooks/setup_config_tables.py` seed INSERTs — DDL/seed layer, not core code
- `notebooks/seed_config.py` — same, setup layer

---

## Decisions

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | Helpers in `src/utils.py`, not a new file | Already the shared utility module; 8 small pure functions don't justify a new module |
| D2 | Callers quote values explicitly | Keeps the builder simple — no type dispatch system |
| D3 | Separate single-row and multi-row builders | Audit needs single-row, scanner needs multi-row. One function with overloaded behavior would be less clear |
| D4 | Column lists as module-level constants | Single source of truth; easy to update when schema changes |
| D5 | `_job_context_values` as private method on AuditLogger | Shared by exactly 2 methods on the same class — method, not module function |
