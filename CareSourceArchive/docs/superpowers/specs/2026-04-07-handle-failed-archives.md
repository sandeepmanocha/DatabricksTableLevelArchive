# Handling Failed Archives — Design Spec

**Date:** 2026-04-07
**Status:** FINAL
**Branch:** `feat/delta_config_build_v2_reruns`

---

## 1. Problem Statement

The archive system processes multiple tables in parallel (ForEach), and multiple years sequentially per table. Failures can occur at any level — a single year, a single table, or the entire job. The system must handle every failure scenario safely, never lose medical data, and make re-runs straightforward for the operator.

**Guiding principle:** This is medical data. When in doubt, ERROR and halt. Never silently overwrite or assume partial data is valid.

---

## 2. Use Cases and Decisions

### UC-1: Multi-year failure for same table

**Scenario:** Table `claims` has eligible years [2020, 2021, 2022]. Year 2021 fails mid-process.

**Decision: Fail fast (Option A)**

- Years are processed sequentially in `_process_year_live`. If 2021 fails, the exception propagates — 2022 is never attempted.
- The catch-all try/except (from the pending `fix_stale_started` plan) logs `FAILED` with the exact error message in the audit table before re-raising.
- Audit state after failure:

| table_name | year | status | error_message |
|---|---|---|---|
| claims | 2020 | ARCHIVED | |
| claims | 2021 | STARTED | |
| claims | 2021 | FAILED | "Spark error: OutOfMemoryError..." |
| claims | 2022 | *(no row)* | *(never attempted)* |

- On re-run, watermark logic handles recovery automatically (see UC-3).

**Why not continue to 2022?** A failure in 2021 may indicate a systemic issue (cluster problems, permissions, bad data). Continuing could produce more failures or mask the root cause. Fail fast gives the operator a clear signal.

---

### UC-2: Partial writes — folder exists but write failed midway

**Scenario:** `CREATE TABLE delta.\`path/year_2021\`` starts writing. Cluster dies mid-write. The folder exists in cloud storage with partial Delta log and incomplete parquet files. No `ARCHIVED` row in audit.

**Decision: ERROR and halt — require manual intervention (Option B)**

- Before proceeding with any year, the system checks: **does the folder exist AND is there no successful `ARCHIVED` or `ARCHIVED_AND_DELETED` status in audit for this table+year?**
- If yes → raise `ArchiveOperationError` with an actionable message.

**Detection logic (new):**

```
folder_exists = archive_folder_exists(dbutils, base_path, table, year)
last_successful = get_last_run_state(table, year)  # only returns ARCHIVED/ARCHIVED_AND_DELETED

if folder_exists and last_successful.status is None:
    ERROR: "Archive folder exists at {path} for year {year} but no successful "
           "archive is recorded in the audit table. This may be from a failed "
           "prior write with partial data.\n"
           "Steps to resolve:\n"
           "1. Inspect the folder: SELECT COUNT(*) FROM delta.`{path}`\n"
           "2. If partial/corrupt: DELETE the folder from cloud storage\n"
           "3. Re-run the archive job"
```

**Why not auto-delete?** Medical data. An orphaned folder might be from a legitimate archive whose audit entry was lost (e.g., audit write failed after a successful archive write). Auto-deleting could destroy the only copy. The operator inspects and decides.

**Why is source data safe?** The delete-from-source step only runs *after* `ARCHIVED` is logged in audit (line 524 in `archiver.py`). If there's no `ARCHIVED` row, source was never touched. Source has all the data.

---

### UC-3: Re-run behavior after failure

**Scenario:** Operator fixes the issue (deletes orphan folder, resizes cluster, etc.) and re-runs the job for the same table.

**Decision: Explicit audit check before retrying (Option B)**

On re-run, for each eligible year, the system queries the audit for the **latest status** (not just ARCHIVED) and logs context before proceeding:

**New pre-processing check per year:**

```
latest_status = get_latest_status(table, year)  # NEW: returns any status, not just ARCHIVED

if latest_status == 'FAILED':
    LOGGER.info("%s year %s: Previous run FAILED (run_id=%s). Retrying.",
                table, year, last_run_id)

if latest_status == 'STARTED':
    LOGGER.warning("%s year %s: Previous run has stale STARTED status (run_id=%s). "
                   "Prior run may not have completed cleanly. Proceeding with retry.",
                   table, year, last_run_id)
```

Then the normal decision tree runs:

| Folder? | Audit (successful)? | Action |
|---|---|---|
| No | No (FAILED/STARTED/None) | **CREATE** — clean fresh start |
| Yes | No (FAILED/STARTED/None) | **ERROR** — orphan folder, manual cleanup (UC-2) |
| Yes | ARCHIVED | Check watermark → APPEND or SKIP |
| No | ARCHIVED_AND_DELETED | **ERROR** — data loss detected (existing logic) |
| Yes | ARCHIVED + delete_after=true | Resume delete (existing logic) |

**New audit method needed:** `get_latest_status(table, year)` — returns the most recent status of *any* kind (not filtered to ARCHIVED/ARCHIVED_AND_DELETED like `get_last_run_state`). Used for informational logging only, not for decision-making.

---

### UC-4: Single table failure in parallel ForEach execution

**Scenario:** Three tables run in parallel via ForEach. `members` fails, `claims` and `providers` succeed.

**Decision: Current behavior is sufficient (Option A)**

- ForEach tasks are independent. One task's failure doesn't affect siblings.
- Databricks job UI shows which tasks succeeded and which failed.
- Audit is isolated per table — each task has its own rows.
- On re-run of the entire job, successful tables SKIP via watermark (one count query per year — fast). Failed table retries via UC-1/UC-3 logic.
- Operator can also re-run just the failed table using `table_config_filter` job parameter.

No code changes needed for this use case. The architecture already handles it.

---

### UC-5: Intentional re-run vs recovery re-run

**Scenario:** User runs the archive process, it succeeds for year 2022. A month later, they run it again. Is this a recovery from a failure, or an intentional run to pick up new data?

**Decision: No special handling needed — watermark resolves intent automatically (Option A)**

The system doesn't need to know the operator's intent. The watermark decides:

| Situation | Watermark finds | Action | Audit trail |
|---|---|---|---|
| Recovery (prior FAILED, no folder) | All records | CREATE | FAILED → CREATE |
| New data arrived | Records above watermark | APPEND | ARCHIVED → APPEND (watermark moves up) |
| Redundant re-run (nothing new) | Zero records | SKIP | ARCHIVED → SKIP (same day) |

Each scenario is **unambiguous in the audit table** without any extra columns:

- **Recovery:** FAILED row followed by ARCHIVED with `archive_mode = CREATE`
- **New data:** Two ARCHIVED rows with increasing `watermark_value` and `archive_mode = APPEND`
- **Redundant:** ARCHIVED row followed by SKIPPED with `archive_mode = SKIP`

No `run_reason` column needed — the information is derivable from existing audit fields (`archive_mode`, `watermark_value`, prior status rows). Keeping the audit schema lean.

---

### UC-6: Distinguishing stale STARTED from legitimate in-progress run

**Scenario:** The system finds a `STARTED` status in audit with a *different* `archive_run_id` and no terminal status. Is the prior run still in progress on another cluster, or did it crash?

**Decision: Fix root cause (catch-all FAILED) + time-based threshold as safety net (Option B). HALT is default if any data risk.**

**Two-layer fix:**

**Layer 1 — Eliminate most stale STARTEDs at the source:**

Implement the catch-all try/except from the pending `fix_stale_started` plan. After this, every failure path logs `FAILED` before the exception propagates. Stale STARTEDs become rare — only possible when the cluster is killed with no graceful shutdown (spot reclaim, hard OOM kill).

**Layer 2 — Time-based threshold for the rare survivor:**

When `check_concurrent` finds a foreign STARTED, check its age:

```
foreign_started_age = current_timestamp - foreign_started.created_at

if foreign_started_age < stale_threshold (default: 4 hours):
    # Might be a legitimate concurrent run — skip safely
    SKIPPED_CONCURRENT (existing behavior)

if foreign_started_age >= stale_threshold:
    # Almost certainly stale from a crashed run
    LOGGER.warning(
        "%s year %s: Found stale STARTED from run %s (%s hours ago). "
        "Treating as abandoned. Proceeding with caution.",
        table, year, foreign_run_id, age_hours
    )
    # Proceed to normal flow — which will hit UC-2 (orphan folder → ERROR)
    # or UC-3 (no folder → CREATE) as appropriate
```

**The stale threshold is configurable** via `global_settings` (field: `stale_started_threshold_hours`, default: 4). This accommodates environments where archive jobs legitimately run longer.

**Critical safety rule:** Even when treating a STARTED as stale, the system **never deletes or overwrites anything**. It simply proceeds to the normal decision tree, which has its own safety checks:

- If the stale run left a folder → UC-2 kicks in → **ERROR and halt** (orphan folder)
- If the stale run left no folder → safe to CREATE
- If the stale run fully succeeded but only the audit FAILED entry was lost → folder exists + no ARCHIVED → **ERROR and halt** (orphan folder)

**HALT is the default policy.** The time threshold only controls whether to skip (`SKIPPED_CONCURRENT`) or proceed to the normal safety checks. It never bypasses UC-2's orphan folder protection.

**New method: `check_concurrent` updated to return age info:**

```python
def check_concurrent(self, table_config, year, archive_run_id, stale_threshold_hours=4):
    """Check for concurrent runs. Returns (is_concurrent, is_stale, foreign_run_id, age_hours)."""
    # Query for STARTED with different run_id, include created_at
    # If found and age < threshold → (True, False, run_id, age)   → caller skips
    # If found and age >= threshold → (False, True, run_id, age)  → caller proceeds with warning
    # If not found → (False, False, None, None)                   → caller proceeds normally
```

---

## 3. Code Changes Required

### 3.1 `src/archiver.py`

**`_process_year_live` — add orphan folder detection (UC-2):**

Insert after the folder-exists check and before the normal flow:

```python
if folder and last_status is None:
    path = build_archive_path(merged["archive_base_path"], merged["source_table"], year)
    raise ArchiveOperationError(
        msg=(
            f"Archive folder exists at {path} for year {year} but no successful "
            f"archive is recorded in the audit table. This may be from a failed "
            f"prior write with partial data.\n"
            f"Steps to resolve:\n"
            f"1. Inspect the folder: SELECT COUNT(*) FROM delta.`{path}`\n"
            f"2. If partial/corrupt: DELETE the folder from cloud storage\n"
            f"3. Re-run the archive job"
        ),
        table=tid, year=year, operation="archive", reason="orphan_folder",
    )
```

**`_process_year_live` — add explicit retry logging (UC-3):**

Insert before the main logic, after querying audit:

```python
latest_any = self._audit.get_latest_status(tid, year)
if latest_any and latest_any[0] == "FAILED":
    LOGGER.info("%s year %s: Previous run FAILED (run_id=%s). Retrying.", tid, year, latest_any[1])
elif latest_any and latest_any[0] == "STARTED":
    LOGGER.warning(
        "%s year %s: Previous run has stale STARTED (run_id=%s). "
        "Prior run may not have completed cleanly. Proceeding with retry.",
        tid, year, latest_any[1],
    )
```

**`_process_year_live` — catch-all for FAILED logging (from pending plan):**

Wrap the body after STARTED in try/except to guarantee FAILED is logged on any unhandled exception.

### 3.2 `src/audit.py`

**New method: `get_latest_status`**

```python
def get_latest_status(self, table: str, year: int) -> Optional[tuple]:
    """Return (status, archive_run_id) for the most recent audit entry of any status."""
    sql = (
        f"SELECT status, archive_run_id "
        f"FROM {self._archive_table()} "
        f"WHERE table_name = {sql_quote(table)} AND year = {int(year)} "
        f"ORDER BY created_at DESC LIMIT 1"
    )
    rows = self._spark.sql(sql).collect()
    if not rows:
        return None
    return (rows[0]["status"], rows[0]["archive_run_id"])
```

### 3.3 Tests

| Test | Validates |
|------|-----------|
| Folder exists + no ARCHIVED in audit → `ArchiveOperationError("orphan_folder")` | UC-2 |
| FAILED in audit + no folder → CREATE proceeds with INFO log | UC-3 |
| Stale STARTED in audit + no folder → CREATE proceeds with WARNING log | UC-3 |
| FAILED in audit + folder exists → ERROR (orphan folder) | UC-2 + UC-3 |
| Multi-year: year N fails → year N+1 never attempted | UC-1 |
| Multi-year: re-run skips already-ARCHIVED years | UC-1 + UC-3 |
| `get_latest_status` returns most recent status regardless of type | UC-3 |

---

## 4. Decision Summary

| Use Case | Decision | Rationale |
|---|---|---|
| UC-1: Multi-year failure | Fail fast, stop at first error | Systemic issues shouldn't be masked |
| UC-2: Partial writes | ERROR and halt, manual cleanup | Medical data — never assume partial data is valid |
| UC-3: Re-run behavior | Explicit audit check, log prior state | Operator visibility into retry context |
| UC-4: Parallel table failure | No change needed | ForEach isolation + watermark SKIP handles it |
| UC-5: Intentional vs recovery | No change needed | Watermark resolves intent; audit trail is unambiguous |
| UC-6: Stale STARTED detection | Fix root cause + time threshold; HALT if data risk | Catch-all FAILED prevents most stale STARTEDs; threshold handles the rest safely |

---

## 5. Default Policy

**HALT is the default.** The system never deletes, overwrites, or assumes data is valid when in doubt. Every ambiguous state results in an ERROR with actionable steps for the operator. This applies across all use cases:

- Orphan folder → HALT (UC-2)
- Missing folder after ARCHIVED_AND_DELETED → HALT (existing)
- Foreign STARTED within threshold → SKIPPED_CONCURRENT (existing, safe — no data touched)
- Stale foreign STARTED beyond threshold → proceed to normal flow, which has its own HALT guards

---

## 6. Files Changed

| File | Change |
|------|--------|
| `src/archiver.py` | Orphan folder detection (UC-2), explicit retry logging (UC-3), catch-all FAILED (UC-1/UC-6), updated concurrent check caller for stale threshold (UC-6) |
| `src/audit.py` | New `get_latest_status()` method (UC-3), updated `check_concurrent()` to return age info (UC-6) |
| `tests/unit/test_archiver.py` | Tests for UC-1, UC-2, UC-3, UC-6 scenarios |
| `tests/unit/test_audit.py` | Tests for `get_latest_status`, updated `check_concurrent` with stale threshold |

---

## 7. Test Scenarios

| Test | Validates |
|------|-----------|
| Folder exists + no ARCHIVED in audit → `ArchiveOperationError("orphan_folder")` | UC-2 |
| FAILED in audit + no folder → CREATE proceeds with INFO log | UC-3 |
| Stale STARTED in audit + no folder → CREATE proceeds with WARNING log | UC-3 |
| FAILED in audit + folder exists → ERROR (orphan folder) | UC-2 + UC-3 |
| Multi-year: year N fails → year N+1 never attempted, FAILED logged | UC-1 |
| Multi-year: re-run skips already-ARCHIVED years via watermark | UC-1 + UC-3 |
| `get_latest_status` returns most recent status regardless of type | UC-3 |
| Foreign STARTED < threshold → SKIPPED_CONCURRENT | UC-6 |
| Foreign STARTED >= threshold + no folder → proceeds to CREATE with WARNING | UC-6 |
| Foreign STARTED >= threshold + folder exists → ERROR (orphan folder via UC-2) | UC-6 |
| Successful re-run after prior ARCHIVED → watermark APPEND or SKIP | UC-5 |
| Redundant same-day re-run → SKIP, no data touched | UC-5 |
