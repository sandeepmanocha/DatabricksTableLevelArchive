# Diagnostic messaging for archive statuses

**Date:** 2026-04-09
**Status:** Design approved, pending implementation

---

## Problem

Four failure/block paths in `archiver.py` produce weak or missing operator-facing messages:

| Path | Current message quality |
|------|----------------------|
| `SKIPPED_CONCURRENT` | No `LOGGER` output, no `error_message` in audit row |
| Delete ownership failure | Terse: `Operation error: table='…', reason='ownership'` |
| Missing folder after `ARCHIVED_AND_DELETED` | Decent prose but no archive path or recovery steps |
| Generic Spark/platform failures | Raw `str(exc)` with no table/year context |

Two existing good messages (`orphan_folder`, `count_mismatch`) are built inline in `archiver.py` — scattered, not reusable.

---

## Decisions

| # | Decision |
|---|----------|
| D1 | Audience: both notebook operators and platform engineers — rich prose + structured fields |
| D2 | `SKIPPED_CONCURRENT` gets `LOGGER.warning` + `error_message` in audit row |
| D3 | Use existing `error_message` column in `archive_audit_log` — no schema change |
| D4 | Single `diagnostic_message` static method on `ArchiveError` base class |
| D5 | Templates organized as nested dict: `status → reason → template string` |
| D6 | Catch-all fallback: function never raises, dumps all kwargs on unknown status/reason/placeholder |
| D7 | Migrate existing inline messages (orphan_folder, count_mismatch) into the same template dict |
| D8 | `ArchiveOperationError` and `ArchiveVerificationError`: `msg` becomes required, kwargs stored as attributes |
| D9 | `ArchiveConfigError`: unchanged — different concern, ~30 callers, own message pattern |
| D10 | No new files, no new dependencies — all changes in `exceptions.py` and `archiver.py` |

---

## Design

### 1. `ArchiveError` — template dict + static method

Added to `src/exceptions.py` on the `ArchiveError` base class:

```python
class ArchiveError(Exception):

    _DIAGNOSTIC_TEMPLATES = {
        "SKIPPED_CONCURRENT": {
            "concurrent_skip": (
                "{table} year {year}: Skipped — concurrent run {foreign_run_id} "
                "has STARTED ({age_hours:.1f}h ago, threshold {stale_threshold_hours}h). "
                "Re-run after that job completes."
            ),
        },
        "FAILED": {
            "ownership": (
                "{table} year {year}: Delete blocked — this run ({archive_run_id}) "
                "does not own the ARCHIVED row. "
                "Check: SELECT archive_run_id, status FROM {audit_table} "
                "WHERE table_name = '{table}' AND year = {year} "
                "AND status = 'ARCHIVED' ORDER BY created_at DESC LIMIT 1"
            ),
            "missing_folder_after_delete": (
                "{table} year {year}: Archive folder missing at {path} but audit "
                "shows ARCHIVED_AND_DELETED — source rows already removed. "
                "Data may be lost.\n"
                "1. Check cloud storage recycle bin\n"
                "2. Check Delta time travel: DESCRIBE HISTORY delta.`{path}`\n"
                "3. Contact storage admin if unrecoverable"
            ),
            "orphan_folder": (
                "{table} year {year}: Archive folder exists at {path} but no "
                "successful archive is recorded in the audit table. This may be "
                "from a failed prior write with partial data.\n"
                "1. Inspect the folder: SELECT COUNT(*) FROM delta.`{path}`\n"
                "2. If partial/corrupt: DELETE the folder from cloud storage\n"
                "3. Re-run the archive job"
            ),
            "count_mismatch": (
                "{table} year {year}: Archive count {actual} does not match "
                "expected {expected}. "
                "Inspect: SELECT COUNT(*) FROM delta.`{path}`"
            ),
            "operation_failure": (
                "{table} year {year}: {operation} failed — {error}"
            ),
        },
    }

    @staticmethod
    def diagnostic_message(status, reason, **kwargs):
        reasons = ArchiveError._DIAGNOSTIC_TEMPLATES.get(status, {})
        template = reasons.get(reason)
        if template:
            try:
                return template.format(**kwargs)
            except KeyError:
                return f"{status} ({reason}): {kwargs}"
        return f"{status} ({reason}): {kwargs}"
```

### 2. `ArchiveOperationError` — msg required, kwargs as attributes

```python
class ArchiveOperationError(ArchiveError):
    def __init__(self, msg, *, table=None, year=None, operation=None, reason=None):
        super().__init__(msg)
        self.table = table
        self.year = year
        self.operation = operation
        self.reason = reason
```

### 3. `ArchiveVerificationError` — msg required, kwargs as attributes

```python
class ArchiveVerificationError(ArchiveError):
    def __init__(self, msg, *, table=None, year=None, expected=None, actual=None, reason=None):
        super().__init__(msg)
        self.table = table
        self.year = year
        self.expected = expected
        self.actual = actual
        self.reason = reason
```

### 4. `ArchiveConfigError` — unchanged

No changes. Different concern, ~30 callers, own message pattern via `field=` / `table_id=`.

---

## Call-site changes in `archiver.py`

### 4a. `SKIPPED_CONCURRENT` (~line 472–479)

Before:
```python
self._audit.log_archive(table=tid, year=year, status="SKIPPED_CONCURRENT", record_count=0)
return {"status": "SKIPPED_CONCURRENT", "record_count": 0}
```

After:
```python
msg = ArchiveError.diagnostic_message(
    "SKIPPED_CONCURRENT", "concurrent_skip",
    table=tid, year=year, foreign_run_id=foreign_run_id,
    age_hours=age_hours, stale_threshold_hours=stale_h,
)
LOGGER.warning(msg)
self._audit.log_archive(table=tid, year=year, status="SKIPPED_CONCURRENT", record_count=0, error_message=msg)
return {"status": "SKIPPED_CONCURRENT", "record_count": 0}
```

### 4b. Ownership failures (~line 529, ~line 590)

Before:
```python
raise ArchiveOperationError(table=tid, year=year, operation="delete", reason="ownership")
```

After:
```python
msg = ArchiveError.diagnostic_message(
    "FAILED", "ownership",
    table=tid, year=year,
    archive_run_id=self._ctx.archive_run_id,
    audit_table=self._audit._archive_table(),
)
raise ArchiveOperationError(msg, table=tid, year=year, operation="delete", reason="ownership")
```

### 4c. Missing folder after delete (in `_resolve_year_action` ~line 319–326)

Before: inline string without path or recovery steps.

After:
```python
msg = ArchiveError.diagnostic_message(
    "FAILED", "missing_folder_after_delete",
    table=tid, year=year,
    path=archive_path_from_config(table_config, year),
)
result["error_message"] = msg
```

### 4d. Orphan folder (in `_resolve_year_action` ~line 328–341)

Before: inline string (already good quality).

After: same content, moved to template:
```python
msg = ArchiveError.diagnostic_message(
    "FAILED", "orphan_folder",
    table=tid, year=year,
    path=archive_path_from_config(table_config, year),
)
result["error_message"] = msg
```

### 4e. Verification failure (`_verify_archive` ~line 210)

Before:
```python
raise ArchiveVerificationError(table=tid, year=year, expected=expected_count, actual=actual, reason="count_mismatch")
```

After:
```python
msg = ArchiveError.diagnostic_message(
    "FAILED", "count_mismatch",
    table=tid, year=year, expected=expected_count, actual=actual,
    path=archive_path_from_config(table_config, year),
)
raise ArchiveVerificationError(msg, table=tid, year=year, expected=expected_count, actual=actual, reason="count_mismatch")
```

### 4f. Generic exception catch (~line 621–636)

Before:
```python
raise ArchiveOperationError(msg=str(exc), table=tid, year=year, operation="archive", reason=str(exc))
```

After:
```python
msg = ArchiveError.diagnostic_message(
    "FAILED", "operation_failure",
    table=tid, year=year, operation="archive", error=str(exc),
)
raise ArchiveOperationError(msg, table=tid, year=year, operation="archive", reason="operation_failure")
```

### 4g. Rehydrator (`src/rehydrator.py` ~line 96)

Already passes `msg=str(exc)`. Update to use `diagnostic_message` for context:
```python
msg = ArchiveError.diagnostic_message(
    "FAILED", "operation_failure",
    table=source, year="all", operation="rehydrate", error=str(exc),
)
raise ArchiveOperationError(msg)
```

---

## Tests

### `tests/unit/test_exceptions.py`

| Test | What it checks |
|------|---------------|
| Known status + known reason → formatted template | Each of the 6 reason keys |
| Unknown status → catch-all fallback with all kwargs | No raise, context preserved |
| Known status + unknown reason → catch-all | No raise |
| Missing placeholder in kwargs → catch-all (KeyError caught) | No raise |
| Template dict keys match expected set | Sync check |
| `ArchiveOperationError` stores attributes | `exc.table`, `exc.year`, `exc.operation`, `exc.reason` |
| `ArchiveVerificationError` stores attributes | `exc.table`, `exc.year`, `exc.expected`, `exc.actual`, `exc.reason` |
| `ArchiveConfigError` unchanged | `field=` / `table_id=` still work as before |

### `tests/unit/test_archiver.py`

Existing tests that assert on `status` or `error_message` may need `error_message` matchers updated for the richer text. No new test files.

---

## Files changed

| File | Change |
|------|--------|
| `src/exceptions.py` | Add `_DIAGNOSTIC_TEMPLATES` + `diagnostic_message()` on `ArchiveError`; update `ArchiveOperationError` and `ArchiveVerificationError` `__init__` |
| `src/archiver.py` | 7 call sites use `diagnostic_message()` for `msg` and `error_message` |
| `src/rehydrator.py` | 1 call site |
| `tests/unit/test_exceptions.py` | New tests for `diagnostic_message`, attribute storage |
| `tests/unit/test_archiver.py` | Update `error_message` assertions where needed |
| `docs/status_affects.md` | Add "Operator messages" section referencing the template keys |

---

## Integration tests (`tests/databricks/`)

### New test files

| # | File | Goal | Setup data | Cleanup |
|---|------|------|-----------|---------|
| 19 | `19_missing_folder_after_delete.md` | Delete archive folder after `ARCHIVED_AND_DELETED`, re-run, verify `FAILED` with rich `error_message` | Depends on test 09 state; enables `delete_after_archive`, deletes folder via `dbutils.fs.rm` | Reset `delete_after_archive`, delete test STARTED/FAILED audit rows, note folder restore path |
| 20 | `20_ownership_guard.md` | Inject fake `ARCHIVED` row with foreign `archive_run_id`, re-run with `delete_after_archive=true`, verify delete blocked with rich `error_message` | Clean providers state, run archive, then UPDATE the ARCHIVED row's `archive_run_id` to a fake value and DELETE the ARCHIVED_AND_DELETED row | Delete all test audit rows, remove archive folders, reset `delete_after_archive`, restore source data |

### Updates to existing test files

| File | What to add |
|------|-------------|
| `12_orphan_folder_detection.md` | Phase 2c: add `SELECT error_message` query; expect message contains inspection SQL + recovery steps |
| `13_stale_started_detection.md` | Phase 3c: add `SELECT error_message` query for `SKIPPED_CONCURRENT` row; expect message contains foreign run id and threshold info |
| `11_failure_and_retry.md` | After FAILED row, add `SELECT error_message` query; expect message contains table/year context |

All new tests follow the existing pattern: **setup data → run → assert status + error_message → cleanup injected data**.

---

## Out of scope

- `ArchiveConfigError` changes
- Scanner or rehydrator diagnostic templates (future — add reasons to the dict when needed)
- New audit table columns
- Changes to `ALLOWED_ARCHIVE_STATUSES`
