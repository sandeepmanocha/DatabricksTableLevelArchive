# Watermark Value: STRING → DATE Type Change

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Change `watermark_value` in `archive_audit_log` from STRING to DATE, eliminating the unsafe string round-trip and making the type match its semantics.

**Architecture:** Add a `sql_date_or_null` SQL formatting helper in `utils.py`. Thread `Optional[date]` through `audit.py` → `archiver.py` instead of `Optional[str]`. Update the DDL and migrate the live dev table column. Update all unit tests to use `date` objects and updated SQL literal format.

**Tech Stack:** Python `datetime.date`, PySpark (maps `DATE` columns to `datetime.date`), Delta Lake, Databricks SQL

---

## File Map

| File | Change |
|------|--------|
| `src/utils.py` | Add `sql_date_or_null` helper + `import datetime as datetime_module` |
| `src/audit.py` | `watermark_value: Optional[date]`, use `sql_date_or_null` |
| `src/archiver.py` | Import `date`, `CAST(MAX AS DATE)`, `sql_date_or_null` in comparisons, type annotations |
| `notebooks/setup_config_tables.py` | DDL: `watermark_value DATE` |
| `tests/unit/test_utils.py` | Add tests for `sql_date_or_null` |
| `tests/unit/test_audit.py` | Use `date` objects, assert `DATE '...'` literals |
| `tests/unit/test_archiver.py` | Mock `{"wm": date(...)}`, `get_last_run_state` returns `date`, assert `DATE '...'` |

## Execution Order (parallel where noted)

```
Phase A (parallel): Task 1 (utils.py) + Task 4 (setup DDL)
Phase B (parallel): Task 2 (audit.py) + Task 3 (archiver.py)
Phase C (parallel): Task 5 (test_utils.py) + Task 6 (test_audit.py) + Task 7 (test_archiver.py)
Phase D: Run unit tests
Phase E: ALTER TABLE on live dev table
Phase F: Databricks integration tests 02, 03, 04
```

---

## Task 1: Add `sql_date_or_null` to `src/utils.py`

**Files:**
- Modify: `src/utils.py`

- [ ] **Step 1: Add import and helper**

Add `import datetime as datetime_module` near the top of `src/utils.py` (after the stdlib imports, before the local imports). Then add the function after `sql_str_or_null`:

```python
import datetime as datetime_module
```

```python
def sql_date_or_null(val) -> str:
    """Format a date or datetime value as a SQL DATE literal, or NULL."""
    if val is None:
        return "NULL"
    if isinstance(val, datetime_module.datetime):
        val = val.date()
    return f"DATE '{val.isoformat()}'"
```

- [ ] **Step 2: Verify it doesn't break existing tests**

```bash
cd /Users/sandeep.manocha/Code/CareSource/CareSourceArchive
python -m pytest tests/unit/test_utils.py -q
```

Expected: all pass (no existing tests touch `sql_date_or_null` yet).

- [ ] **Step 3: Commit**

```bash
git add src/utils.py
git commit -m "feat: add sql_date_or_null helper for DATE literal formatting"
```

---

## Task 2: Update `src/audit.py`

**Files:**
- Modify: `src/audit.py`

Depends on Task 1 (`sql_date_or_null` must exist in utils).

- [ ] **Step 1: Update imports**

In `src/audit.py`, the file already has `import datetime as datetime_module`. Add `sql_date_or_null` to the utils import block:

```python
from src.utils import (
    build_insert_values_sql,
    ensure_table_with_setup_message,
    row_value,
    sql_date_or_null,
    sql_expr,
    sql_int,
    sql_int_or_null,
    sql_quote,
    sql_str_or_null,
)
```

- [ ] **Step 2: Update `log_archive` signature and body**

Change `watermark_value: Optional[str] = None` to `Optional[datetime_module.date]`, and change the SQL value from `sql_str_or_null` to `sql_date_or_null`:

```python
def log_archive(
    self,
    table: str,
    year: int,
    status: str,
    record_count: int,
    conditions_applied: Optional[str] = None,
    null_date_count: Optional[int] = None,
    error_message: Optional[str] = None,
    watermark_value: Optional[datetime_module.date] = None,
    source_year_count: Optional[int] = None,
    archive_mode: Optional[str] = None,
) -> None:
    if status not in ALLOWED_ARCHIVE_STATUSES:
        raise ArchiveConfigError(msg=f"Invalid archive audit status: {status!r}")
    audit_id = str(uuid.uuid4())
    values = [
        sql_quote(audit_id),
        sql_quote(self._ctx.archive_run_id),
        sql_quote(table),
        sql_int(year),
        sql_quote(status),
        sql_int(record_count),
        sql_str_or_null(conditions_applied),
        sql_int_or_null(null_date_count),
        sql_str_or_null(error_message),
        sql_date_or_null(watermark_value),       # ← was sql_str_or_null
        sql_int_or_null(source_year_count),
        sql_str_or_null(archive_mode),
        *self._job_context_values(),
    ]
    sql = build_insert_values_sql(self._archive_table(), ARCHIVE_AUDIT_COLUMNS, values)
    self._spark.sql(sql)
```

- [ ] **Step 3: Update `get_last_run_state` return type annotation (doc only, no logic change)**

PySpark automatically returns `datetime.date` for DATE columns, so `r["watermark_value"]` will already be the right type. Just update the docstring/comment if any. The tuple now returns `Optional[datetime_module.date]` as the second element.

- [ ] **Step 4: Verify**

```bash
python -m pytest tests/unit/test_audit.py -q
```

Expected: some tests may fail now (test_audit uses string `"2020-12-31"` as watermark — that's fine, we fix tests in Task 6). Non-watermark tests should pass.

- [ ] **Step 5: Commit**

```bash
git add src/audit.py
git commit -m "feat: watermark_value uses DATE type in audit log insert"
```

---

## Task 3: Update `src/archiver.py`

**Files:**
- Modify: `src/archiver.py`

Depends on Task 1 (`sql_date_or_null`). Can run in parallel with Task 2.

- [ ] **Step 1: Update imports**

Change the existing `datetime` import from:
```python
from datetime import datetime, timezone
```
to:
```python
from datetime import date, datetime, timezone
```

Add `sql_date_or_null` to the utils import block:
```python
from src.utils import (
    archive_folder_exists,
    archive_path_from_config,
    source_fq_from_config,
    spark_count,
    sql_date_or_null,
    sql_quote,
)
```

- [ ] **Step 2: Update `_get_watermark_value`**

Change the SQL from `CAST(MAX AS STRING)` to `CAST(MAX AS DATE)`, and update the return type:

```python
def _get_watermark_value(self, table_config: Mapping[str, Any], year: int) -> Optional[date]:
    path = archive_path_from_config(table_config, year)
    wm_col = table_config["watermark_column"]
    q = f"SELECT CAST(MAX({wm_col}) AS DATE) AS wm FROM delta.`{path}`"
    row = self._spark.sql(q).first()
    return row["wm"] if row and row["wm"] else None
```

PySpark returns `datetime.date` for a `DATE` column — no manual parsing needed.

- [ ] **Step 3: Update `_year_where_sql`**

Change the type of `after_watermark` and the SQL comparison:

```python
def _year_where_sql(
    self,
    table_config: Mapping[str, Any],
    year: int,
    exclusion_clause: str,
    alias: str,
    after_watermark: Optional[date] = None,
) -> str:
    wm_col = table_config["watermark_column"]
    exc = _resolve_exclusion(exclusion_clause)
    parts = [
        f"YEAR({alias}.{wm_col}) = {int(year)}",
    ]
    if after_watermark:
        parts.append(f"{alias}.{wm_col} > {sql_date_or_null(after_watermark)}")
    parts.append(f"{alias}.{wm_col} IS NOT NULL")
    parts.append(f"({exc})")
    return " AND ".join(parts)
```

- [ ] **Step 4: Update `_count_new_records`**

Change the `last_watermark` type and SQL comparison:

```python
def _count_new_records(
    self,
    table_config: Mapping[str, Any],
    year: int,
    last_watermark: date,
    exclusion_clause: str,
) -> int:
    fq = source_fq_from_config(table_config)
    wm_col = table_config["watermark_column"]
    exc = _resolve_exclusion(exclusion_clause)
    q = (
        f"SELECT COUNT(*) AS count FROM {fq} src "
        f"WHERE YEAR(src.{wm_col}) = {int(year)} "
        f"AND src.{wm_col} > {sql_date_or_null(last_watermark)} "
        f"AND src.{wm_col} IS NOT NULL AND ({exc})"
    )
    return spark_count(self._spark, q)
```

- [ ] **Step 5: Commit**

```bash
git add src/archiver.py
git commit -m "feat: watermark comparison uses DATE literals, _get_watermark_value returns date"
```

---

## Task 4: Update DDL in `notebooks/setup_config_tables.py`

**Files:**
- Modify: `notebooks/setup_config_tables.py`

Independent — can run in parallel with Task 1.

- [ ] **Step 1: Change `watermark_value` column type in `DDL_ARCHIVE_AUDIT_LOG`**

Find this line:
```python
  watermark_value          STRING              COMMENT 'MAX(watermark_column) from archived records this run',
```

Replace with:
```python
  watermark_value          DATE                COMMENT 'MAX(watermark_column) from archived records this run',
```

- [ ] **Step 2: Verify the DDL string is syntactically correct**

```bash
python -c "
from notebooks.setup_config_tables import DDL_ARCHIVE_AUDIT_LOG
assert 'watermark_value          DATE' in DDL_ARCHIVE_AUDIT_LOG
assert 'STRING' not in DDL_ARCHIVE_AUDIT_LOG.split('watermark_value')[1].split('\n')[0]
print('OK')
"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add notebooks/setup_config_tables.py
git commit -m "feat: watermark_value DDL changed to DATE"
```

---

## Task 5: Add `sql_date_or_null` tests to `tests/unit/test_utils.py`

**Files:**
- Modify: `tests/unit/test_utils.py`

Depends on Task 1. Can run in parallel with Tasks 6 and 7.

- [ ] **Step 1: Add import and tests**

Add `from datetime import date, datetime` to the imports in `test_utils.py`.

Add `sql_date_or_null` to the import block from `src.utils`.

Add the following test class/functions:

```python
class TestSqlDateOrNull:
    def test_none_returns_null(self):
        from src.utils import sql_date_or_null
        assert sql_date_or_null(None) == "NULL"

    def test_date_formats_as_date_literal(self):
        from src.utils import sql_date_or_null
        assert sql_date_or_null(date(2020, 12, 31)) == "DATE '2020-12-31'"

    def test_datetime_is_truncated_to_date(self):
        from src.utils import sql_date_or_null
        assert sql_date_or_null(datetime(2022, 6, 15, 14, 30, 0)) == "DATE '2022-06-15'"

    def test_single_digit_month_and_day_zero_padded(self):
        from src.utils import sql_date_or_null
        assert sql_date_or_null(date(2021, 1, 5)) == "DATE '2021-01-05'"
```

- [ ] **Step 2: Run tests**

```bash
python -m pytest tests/unit/test_utils.py -q
```

Expected: all pass including the 4 new tests.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_utils.py
git commit -m "test: add sql_date_or_null unit tests"
```

---

## Task 6: Update `tests/unit/test_audit.py`

**Files:**
- Modify: `tests/unit/test_audit.py`

Depends on Tasks 1 and 2.

- [ ] **Step 1: Add `date` import**

Add to the existing datetime import:
```python
from datetime import date, datetime, timedelta, timezone
```

- [ ] **Step 2: Update `test_aud_log_archive_with_watermark_fields`**

Change the test to pass a `date` object and assert the SQL DATE literal format:

```python
def test_aud_log_archive_with_watermark_fields(audit_logger):
    _ctx, log, mock_spark = audit_logger
    mock_spark.reset_mock()
    log.log_archive(
        table="t",
        year=2020,
        status="ARCHIVED",
        record_count=100,
        watermark_value=date(2020, 12, 31),    # ← was "2020-12-31"
        source_year_count=500,
        archive_mode="CREATE",
    )
    mock_spark.sql.assert_called_once()
    sql = mock_spark.sql.call_args[0][0]
    assert "DATE '2020-12-31'" in sql           # ← was "'2020-12-31'"
    assert "500" in sql
    assert "'CREATE'" in sql
```

- [ ] **Step 3: Update `test_aud_get_last_run_state_returns_tuple`**

The mock returns a DATE value (Python `date` object, not string), and the assertion must reflect this:

```python
def test_aud_get_last_run_state_returns_tuple(audit_logger):
    _ctx, log, mock_spark = audit_logger
    out_df = MagicMock()
    out_df.collect.return_value = [
        {"status": "ARCHIVED", "watermark_value": date(2022, 6, 15), "source_year_count": 1000}
    ]
    mock_spark.sql.return_value = out_df
    assert log.get_last_run_state("my.table", 2022) == ("ARCHIVED", date(2022, 6, 15), 1000)
    sql = mock_spark.sql.call_args[0][0]
    assert "ORDER BY created_at DESC" in sql
    assert "LIMIT 1" in sql
    assert "ARCHIVED" in sql and "ARCHIVED_AND_DELETED" in sql
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/unit/test_audit.py -q
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_audit.py
git commit -m "test: update audit tests for DATE watermark_value type"
```

---

## Task 7: Update `tests/unit/test_archiver.py`

**Files:**
- Modify: `tests/unit/test_archiver.py`

Depends on Tasks 2 and 3. Can run in parallel with Task 6.

This file has the most changes — all watermark string values become `date` objects.

- [ ] **Step 1: Add `date` import**

Find the existing datetime import in `test_archiver.py` and add `date`:

```python
from datetime import date, datetime, timedelta, timezone
```

- [ ] **Step 2: Update all `{"wm": "date-string"}` mock returns**

Every `m.first.return_value = {"wm": "YYYY-MM-DD"}` must use `date(...)`. There are 7 occurrences. Replace each string with a `date` object:

| Line | Old | New |
|------|-----|-----|
| 294 | `{"wm": "2018-12-31"}` | `{"wm": date(2018, 12, 31)}` |
| 933 | `{"wm": "2018-11-01"}` | `{"wm": date(2018, 11, 1)}` |
| 982 | `{"wm": "2018-12-01"}` | `{"wm": date(2018, 12, 1)}` |
| 1091 | `{"wm": "2018-10-01"}` | `{"wm": date(2018, 10, 1)}` |
| 1148 | `{"wm": "2018-08-01"}` | `{"wm": date(2018, 8, 1)}` |
| 1458 | `{"wm": "2018-06-01"}` | `{"wm": date(2018, 6, 1)}` |

Run a search first to confirm all occurrences: `rg '{"wm":' tests/unit/test_archiver.py`

- [ ] **Step 3: Update all `get_last_run_state.return_value` tuples**

Every `("ARCHIVED", "date-string", count)` must use `date(...)`. There are ~12 occurrences. Replace:

| Line | Old | New |
|------|-----|-----|
| 263 | `("ARCHIVED_AND_DELETED", "2018-06-15", 500)` | `("ARCHIVED_AND_DELETED", date(2018, 6, 15), 500)` |
| 323 | `("ARCHIVED", "2018-12-31", 200)` | `("ARCHIVED", date(2018, 12, 31), 200)` |
| 954 | `("ARCHIVED", "2018-06-15", 500)` | `("ARCHIVED", date(2018, 6, 15), 500)` |
| 1007 | `("ARCHIVED", "2018-12-31", 200)` | `("ARCHIVED", date(2018, 12, 31), 200)` |
| 1172 | `("ARCHIVED", "2018-12-31", 500)` | `("ARCHIVED", date(2018, 12, 31), 500)` |
| 1213 | `("ARCHIVED_AND_DELETED", "2018-12-31", 500)` | `("ARCHIVED_AND_DELETED", date(2018, 12, 31), 500)` |
| 1685 | `("ARCHIVED", "2020-12-31", 100)` | `("ARCHIVED", date(2020, 12, 31), 100)` |
| 1708 | `("ARCHIVED", "2020-06-15", 100)` | `("ARCHIVED", date(2020, 6, 15), 100)` |
| 1730 | `("ARCHIVED_AND_DELETED", "2020-12-31", 100)` | `("ARCHIVED_AND_DELETED", date(2020, 12, 31), 100)` |
| 1754 | `("ARCHIVED", "2020-12-31", 100)` | `("ARCHIVED", date(2020, 12, 31), 100)` |
| 1768 | `("ARCHIVED", "2018-12-31", 200)` | `("ARCHIVED", date(2018, 12, 31), 200)` |
| 1802 | `("ARCHIVED", "2018-06-15", 500)` | `("ARCHIVED", date(2018, 6, 15), 500)` |

- [ ] **Step 4: Update SQL assertion strings**

Two SQL assertion lines check for `> 'date-string'` — change to `> DATE 'date-string'`:

Line 312:
```python
# Old:
assert any("src.claim_date > '2018-06-15'" in s.replace("\n", " ") for s in insert_sqls)
# New:
assert any("src.claim_date > DATE '2018-06-15'" in s.replace("\n", " ") for s in insert_sqls)
```

Line 1000:
```python
# Old:
assert any("src.claim_date > '2018-06-15'" in s.replace("\n", " ") for s in insert_sqls)
# New:
assert any("src.claim_date > DATE '2018-06-15'" in s.replace("\n", " ") for s in insert_sqls)
```

- [ ] **Step 5: Run tests**

```bash
python -m pytest tests/unit/test_archiver.py -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add tests/unit/test_archiver.py
git commit -m "test: update archiver tests for DATE watermark type"
```

---

## Task 8: Run Full Unit Test Suite

- [ ] **Step 1: Run all unit tests**

```bash
cd /Users/sandeep.manocha/Code/CareSource/CareSourceArchive
python -m pytest tests/unit/ -q
```

Expected: all pass, 0 failures.

- [ ] **Step 2: Fix any failures before proceeding**

If any failures, fix them in the relevant file, re-run, commit.

---

## Task 9: ALTER TABLE on Live Dev Table

Run this via `databricks experimental aitools tools query` or directly in the Databricks SQL editor.

The target table is `sandeep_manocha.caresource_audit.archive_audit_log`.

- [ ] **Step 1: Try the simple ALTER (DBR 14+ supports TYPE for some changes)**

```bash
databricks experimental aitools tools query \
  "ALTER TABLE sandeep_manocha.caresource_audit.archive_audit_log ALTER COLUMN watermark_value TYPE DATE" \
  --profile DEFAULT
```

If this succeeds → skip to Step 5. If it fails with a type change error → continue to Step 2.

- [ ] **Step 2: Enable column mapping (required for drop/rename)**

```bash
databricks experimental aitools tools query \
  "ALTER TABLE sandeep_manocha.caresource_audit.archive_audit_log SET TBLPROPERTIES ('delta.columnMapping.mode' = 'name', 'delta.minReaderVersion' = '2', 'delta.minWriterVersion' = '5')" \
  --profile DEFAULT
```

- [ ] **Step 3: Add new DATE column**

```bash
databricks experimental aitools tools query \
  "ALTER TABLE sandeep_manocha.caresource_audit.archive_audit_log ADD COLUMN watermark_date DATE COMMENT 'MAX(watermark_column) from archived records this run'" \
  --profile DEFAULT
```

- [ ] **Step 4: Backfill + drop old + rename**

```bash
databricks experimental aitools tools query \
  "UPDATE sandeep_manocha.caresource_audit.archive_audit_log SET watermark_date = TRY_CAST(watermark_value AS DATE) WHERE watermark_value IS NOT NULL" \
  --profile DEFAULT

databricks experimental aitools tools query \
  "ALTER TABLE sandeep_manocha.caresource_audit.archive_audit_log DROP COLUMN watermark_value" \
  --profile DEFAULT

databricks experimental aitools tools query \
  "ALTER TABLE sandeep_manocha.caresource_audit.archive_audit_log RENAME COLUMN watermark_date TO watermark_value" \
  --profile DEFAULT
```

- [ ] **Step 5: Verify column type**

```bash
databricks experimental aitools tools query \
  "DESCRIBE TABLE sandeep_manocha.caresource_audit.archive_audit_log" \
  --profile DEFAULT
```

Expected: `watermark_value` shows `date` type, not `string`.

---

## Task 10: Databricks Integration Tests

Run tests 02, 03, 04 in order (each depends on the previous).

- [ ] **Step 1: Deploy updated bundle**

```bash
databricks bundle deploy -t dev --profile DEFAULT
```

- [ ] **Step 2: Run test 02 — Scanner First Run**

Follow steps in `tests/databricks/02_scanner_first_run.md`.

Expected: scanner populates `table_configs`, `scanner_log` shows entries.

- [ ] **Step 3: Run test 03 — Scanner Re-scan Idempotent**

Follow steps in `tests/databricks/03_scanner_rescan_idempotent.md`.

Expected: re-run produces no duplicates, `merge_action = updated`.

- [ ] **Step 4: Run test 04 — Archive Dry Run**

Follow steps in `tests/databricks/04_archive_dry_run.md`.

Expected: `DRY_RUN` entries appear in `archive_audit_log`. Verify `watermark_value` column is NULL (dry runs don't compute watermarks) and the column type is `date`.

```bash
databricks experimental aitools tools query \
  "SELECT table_name, year, status, watermark_value, typeof(watermark_value) AS wm_type FROM sandeep_manocha.caresource_audit.archive_audit_log ORDER BY created_at DESC LIMIT 10" \
  --profile DEFAULT
```

Expected: `wm_type = date`, `watermark_value = null` for DRY_RUN rows.

- [ ] **Step 5: Record results**

Save results to `tests/databricks/test_results/12_watermark_date_type_results.md`.
