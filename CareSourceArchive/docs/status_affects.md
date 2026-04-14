# Archive status, audit, and run impact

This document summarizes how **archive audit statuses** and **path / folder state** interact with the archiver, and what operators should expect on the **current** and **next** run. Behavior matches `src/audit.py`, `src/archiver.py`, and `src/scanner.py`.

---

## Audit rows: insert vs update (summary)

| Question | Answer |
|----------|--------|
| Does the archiver **update** existing `archive_audit_log` rows? | **No.** There is no `UPDATE`; history is **append-only**. |
| What happens on each `log_archive` / `log_dry_run` call? | A **new row** is inserted with a new `audit_id`, the current `archive_run_id`, and `created_at` from the warehouse. |
| How is “current” status determined? | Queries use **`ORDER BY created_at DESC`** (e.g. latest row for a table+year, optionally filtered by status). Older rows are kept for traceability. |
| Same table+year, same job run — multiple rows? | **Yes** when a run logs more than one milestone (e.g. `STARTED` then `ARCHIVED` then `ARCHIVED_AND_DELETED`; or `STARTED` then `FAILED`). |

Implementation: `AuditLogger.log_archive` builds an **INSERT** via `build_insert_values_sql` in `src/audit.py`.

---

## Status lifecycles (start → end)

Each subsection is a **table+year** slice. Status names are values written to `archive_audit_log`. Unless noted, sequences use the **same** `archive_run_id` for that slice in one invocation of `_archive_table_year`.

### A. Live archive run — sequences inside one `_archive_table_year` call

| Scenario | Status sequence (in order) | Terminal for this year in this call? |
|----------|----------------------------|--------------------------------------|
| **Concurrent guard** — another run has a non-stale `STARTED` for this table+year | `STARTED` → `SKIPPED_CONCURRENT` | Yes — no archive/delete |
| **Stale foreign `STARTED`** | `STARTED` → … | No extra terminal by itself; continues into one of the rows below (warning logged) |
| **Skip** — folder exists, nothing new above watermark | `STARTED` → `SKIPPED` | Yes |
| **Resume delete** — last success was `ARCHIVED`, `delete_after_archive=true`, delete not done yet | `STARTED` → `ARCHIVED_AND_DELETED` | Yes — no second `ARCHIVED` in this call |
| **Success, keep source** — `delete_after_archive=false` | `STARTED` → `ARCHIVED` | Yes |
| **Success, delete source** — `delete_after_archive=true` | `STARTED` → `ARCHIVED` → `ARCHIVED_AND_DELETED` | Yes — **two** success rows in one call (delete only after ownership check on this run’s `ARCHIVED` row) |
| **Failure** — any exception after `STARTED` (verify, write, delete, resolve action error, etc.) | `STARTED` → `FAILED` | Yes — `error_message` populated |
| **Orphan / missing-folder errors** | `STARTED` → `FAILED` | Yes — raised as `ArchiveOperationError`, then logged as `FAILED` |

Paths that **never** reach `_archive_table_year` (e.g. config errors before per-year loop) do not insert `STARTED` for that slice.

### B. Dry run — `run(..., dry_run=True)`

| Scenario | Status sequence | Notes |
|----------|-----------------|-------|
| Per eligible year | **`DRY_RUN` only** | No `STARTED` row on this path; `log_dry_run` → `log_archive(..., status="DRY_RUN")` once per year. |

### C. Cross-run lifecycle (how prior rows influence the **next** live run)

Prior runs leave an **append-only** trail. The **next** live run always inserts a **new** `STARTED` (then more rows as in section A). Decision logic uses the latest relevant history (see [Which audit query drives decisions?](#which-audit-query-drives-decisions)).

| Prior terminal outcome (typical) | Folder / audit context | Next live run tends toward |
|----------------------------------|------------------------|----------------------------|
| `ARCHIVED_AND_DELETED` | Folder present, no new source rows above watermark | `STARTED` → `SKIPPED` |
| `ARCHIVED_AND_DELETED` | Folder present, new data above watermark | `STARTED` → `ARCHIVED` → `ARCHIVED_AND_DELETED` (append + optional delete) or `STARTED` → `ARCHIVED` if no delete |
| `ARCHIVED` (delete not done) | Folder ok | `STARTED` → `ARCHIVED_AND_DELETED` (resume delete) |
| `FAILED` | Depends on partial writes | `STARTED` → … — may hit **orphan folder** error if folder exists without successful `ARCHIVED` / `ARCHIVED_AND_DELETED` |
| `SKIPPED_CONCURRENT` | Unchanged | `STARTED` → … — retries normal path if other run finished |
| `SKIPPED` | Unchanged | `STARTED` → `SKIPPED` or append/create per watermark |
| `DRY_RUN` only (never live) | N/A | Next **live** run has no `ARCHIVED`/`ARCHIVED_AND_DELETED`; behaves like first-time subject to folder rules |

`NO_DATA` is not written by `ArchiveEngine` today; reserved for enum/concurrency completeness.

---

## Summary: situations → status → meaning → run impact

| Situation | Audit status or outcome | What it means | Effect on current / next run |
|-----------|-------------------------|---------------|------------------------------|
| Year begins processing (live run) | `STARTED` then logic continues | Run claimed this table+year for this `archive_run_id`. | Concurrency check runs next; another fresh foreign `STARTED` → this year becomes `SKIPPED_CONCURRENT`. |
| Another run holds a non-stale `STARTED` for same table+year | `SKIPPED_CONCURRENT` | Active or recent concurrent job on same slice. | **This run** does no archive/delete for that year. **Next run** tries again from scratch. |
| Foreign `STARTED` is older than `stale_started_threshold_hours` (default 4h) | (no skip; warning log) | Prior run likely abandoned mid-flight. | **This run** proceeds; may overlap with orphaned partial state—folder vs audit checks still apply. |
| Archive write + verify succeed; `delete_after_archive=false` | `ARCHIVED` | Data on archive path; source unchanged. | **Next run**: watermark/skip/append logic; folder exists → may `SKIP` or `APPEND` if new data. |
| Archive write + verify succeed; `delete_after_archive=true`; delete succeeds | `ARCHIVED_AND_DELETED` | Archive copy exists; eligible source rows removed. | **Next run**: new data above watermark → `APPEND`; no new data → `SKIP`. |
| Previous successful state was `ARCHIVED` and `delete_after_archive=true` but delete never ran | Resume path → `ARCHIVED_AND_DELETED` | Crash or stop after audit `ARCHIVED`, before delete. | **Next run** resumes: verify archive count, ownership check, then delete + final audit + metadata. |
| Folder exists, no row with `ARCHIVED` / `ARCHIVED_AND_DELETED` in audit | **Error** (`ArchiveOperationError`, not a status) | “Orphan folder”—possible failed/partial write. | **Run stops** for that year until operator inspects Delta path, fixes storage/audit per error text, re-runs. |
| Folder missing, last success was `ARCHIVED_AND_DELETED` | **Error** | Data was archived and removed from source; archive path now missing. | **Run stops**—treated as possible data loss; no silent recreate. |
| Zero rows match the archive predicate on an initial create (edge case) | Usually `ARCHIVED` with `record_count=0` (if CTAS succeeds) | No data moved; empty or minimal Delta at year path. | **Next run** uses normal folder+watermark rules. (`NO_DATA` is a **reserved** allowed audit value but is **not** emitted by `ArchiveEngine` today.) |
| Dry run completes for a year | `DRY_RUN` | Counts/report only; no data movement. | No impact on live archive state; next live run uses normal logic. |
| After write, archive row count ≠ expected | `FAILED` | Verification failed. | **Next run** sees latest `FAILED` in logs (informational); processing attempts again unless blocked by folder/audit rules. |
| Any other exception during the year | `FAILED` | Error stored in `error_message`. | Same as above; operator fixes root cause and re-runs. |
| Folder exists, watermark shows no new eligible rows | `SKIPPED` | Incremental mode: nothing above last watermark. | Fast path; **next run** re-evaluates counts/watermark. |
| NULLs in watermark column for that year | (processing may continue) | Logged at ERROR; `null_date_count` in audit. | Those rows excluded from archive predicate; operational issue to fix upstream. |
| Source row count for year drops vs last `source_year_count` | (no status change by itself) | External deletes or drift. | **Warning** only; archive logic not blocked by this alone. |

---

## Enumerated archive statuses (audit)

Writes to `archive_audit_log` must use one of:

`STARTED`, `DRY_RUN`, `ARCHIVED`, `ARCHIVED_AND_DELETED`, `FAILED`, `SKIPPED`, `SKIPPED_CONCURRENT`, `NO_DATA`.

Invalid values are rejected at insert time (`ArchiveConfigError`).

`NO_DATA` is included for a consistent enum and for concurrency “terminal” detection; live archive jobs today typically use `ARCHIVED` (possibly with zero count) or `SKIPPED` instead of logging `NO_DATA`.

---

## Which audit query drives decisions?

| Query | Used for |
|-------|----------|
| **`get_latest_status`** (latest row by `created_at`, any status) | Logging only before work: e.g. prior `FAILED` → retry message; prior `STARTED` → warning. Does not alone choose skip/resume/create. |
| **`get_last_run_state`** (latest row with `ARCHIVED` or `ARCHIVED_AND_DELETED` only) | **Resume, skip, append, create,** and the **orphan / missing-folder** error conditions in `_resolve_year_action`. |
| **`check_concurrent`** | Detects another run’s open `STARTED` without a terminal row for that `archive_run_id`, subject to stale threshold. |
| **`is_archived_by_run`** | Ensures **this** `archive_run_id` owns the `ARCHIVED` row before **source delete**. |

---

## Location and external path checks

| Check | Where | What it does |
|-------|--------|----------------|
| UC external location coverage | **Scanner** (`validate_archive_path`): `SHOW EXTERNAL LOCATIONS` | Non-`/Volumes/` paths must sit under a registered external location or configuration fails early. |
| Volume path | **Scanner** | Separate validation that the volume is external. |
| Year folder exists | **Archiver** (`archive_folder_exists` / `dbutils.fs.ls`) | Drives create vs append vs skip vs error paths; **not** a repeat of UC external-location listing on every job. |

If an external location is removed or misconfigured **after** scanning, the next archive run typically fails at list/read/write with platform errors until UC/storage is fixed.

---

## Health check helper

`ArchiveEngine.validate_archives()` checks that expected year folders exist for eligible years (from retention). It reports `missing` paths; it does **not** replace the full audit+folder consistency rules in `_resolve_year_action`.

---

## Operator messages

Error and warning paths produce diagnostic messages via `ArchiveError.diagnostic_message(status, reason, **kwargs)`. Messages are written to the `error_message` column in `archive_audit_log` and to `LOGGER` output.

| Status | Reason key | When it fires |
|--------|-----------|---------------|
| `SKIPPED_CONCURRENT` | `concurrent_skip` | Another run has a non-stale `STARTED` for this table+year |
| `FAILED` | `ownership` | Delete blocked — this run does not own the `ARCHIVED` row |
| `FAILED` | `missing_folder_after_delete` | Archive folder missing but audit shows `ARCHIVED_AND_DELETED` |
| `FAILED` | `orphan_folder` | Folder exists but no successful archive recorded |
| `FAILED` | `count_mismatch` | Archive row count does not match expected |
| `FAILED` | `operation_failure` | Generic Spark/platform exception with table+year context |

Templates are defined in `ArchiveError._DIAGNOSTIC_TEMPLATES` in `src/exceptions.py`. The method never raises — unknown status/reason or formatting errors return a catch-all fallback with all kwargs.

---

## References

- State machine and status definitions: [design.md](./design.md) (Archive Audit Status State Machine).
- Feature IDs for resume, concurrency, verification: [features.md](./features.md).
- Orphan / failed-archive rationale: [superpowers/specs/2026-04-07-handle-failed-archives.md](./superpowers/specs/2026-04-07-handle-failed-archives.md).
