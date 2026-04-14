# Watermark Scope — Global vs Local Design Spec

**Date:** 2026-04-10
**Status:** FINAL
**Branch:** `feat/delta_config_build_v3_code_reduce`

---

## 1. Problem Statement

The current archiver uses a per-table-per-year watermark: each year partition tracks its own `MAX(watermark_column)` independently. On every run, the archiver loops through every eligible year and queries each one — even when nothing has changed. This is wasteful compute for tables with many archived years and no new data.

Additionally, the per-year watermark creates a subtle gap: if a record arrives in the source with a date older than the year's archived MAX (e.g., `2021-06-15` when the 2021 archive already has data through `2021-12-30`), the archiver misses it. A global watermark has a similar limitation (anything below the global MAX is missed) but avoids the per-year scan overhead.

---

## 2. Decisions

| # | Decision |
|---|----------|
| 1 | New column `watermark_scope` in `table_configs` — values: `local` (default) or `global` |
| 2 | `local` = per-table-per-year watermark (current behavior, unchanged) |
| 3 | `global` = one watermark per table; single query `WHERE wm_col > global_max`; bucket results into year folders |
| 4 | Global watermark auto-derived from audit log: `MAX(watermark_value)` across all ARCHIVED entries for the table |
| 5 | `local → global` switch: allowed, watermark auto-derived |
| 6 | `global → local` switch: **blocked** — enforced at both validation and runtime |
| 7 | Audit log stores `watermark_scope` used per run (for enforcement and observability) |
| 8 | Remove Delta folder fallback for watermarks — audit log is single source of truth |
| 9 | Implementation: minimal approach — global is a pre-filter on detection; year-level write/verify/audit path stays unchanged |
| 10 | Archive folders must stay accessible during retention window; after retention, can move to cold storage |

---

## 3. Config Change

`table_configs` gets one new column:

| Column | Type | Default | Values |
|--------|------|---------|--------|
| `watermark_scope` | STRING | `local` | `local`, `global` |

Participates in the two-tier merge (can be set in `global_settings` as a default, overridden per table in `table_configs`). Scanner sets `local` for new tables.

---

## 4. Audit Log Change

`archive_audit_log` gets one new column:

| Column | Type | Purpose |
|--------|------|---------|
| `watermark_scope` | STRING | Records which mode was used: `local` or `global`. Used for enforcement (block global→local) and observability |

Existing columns (`watermark_value`, `archive_mode`, `record_count`) are unchanged and work for both modes.

---

## 5. Global Mode Run Logic

When `watermark_scope = global`, the archiver changes how it detects new data but not how it writes:

```
1. Derive global watermark:
   MAX(watermark_value) from audit log
   WHERE table_name = X AND status IN ('ARCHIVED', 'ARCHIVED_AND_DELETED')

2. Single source query:
   SELECT * FROM source
   WHERE watermark_column > global_max
     AND watermark_column IS NOT NULL
     AND (exclusion conditions)

3. If zero rows → SKIP entire table
   - Still write SKIPPED entries for every eligible year (same audit shape as local)
   - Each entry has watermark_scope = 'global'

4. If rows found:
   - Group by YEAR(watermark_column)
   - For each year with rows:
     a. Folder exists → APPEND (existing per-year write path)
     b. Folder doesn't exist → CREATE (existing per-year write path)
   - Years with no new rows → write SKIPPED entry (same as local)

5. Audit: one entry per eligible year (same shape as local mode)
   Each entry includes watermark_scope = 'global'
```

### Performance difference

| | Local mode | Global mode |
|--|-----------|-------------|
| Detection queries | One per eligible year | One for the entire table |
| Write path | Per-year (unchanged) | Per-year (unchanged) |
| Audit entries | One per eligible year | One per eligible year (same shape) |

The audit log looks identical to the operator regardless of mode. The `watermark_scope` column tells them how the decision was made.

---

## 6. Local Mode (unchanged)

No changes to current behavior. Each year is processed independently:

1. For each eligible year, query audit for that year's watermark
2. Count new records: `WHERE YEAR(wm_col) = Y AND wm_col > last_watermark`
3. If count > 0 → APPEND; if count = 0 → SKIP; if no folder → CREATE

---

## 7. Mode Switching

### local → global (allowed)

- No special handling needed
- Global watermark = `MAX(watermark_value)` from existing per-year ARCHIVED audit entries
- First global run picks up anything above that MAX and buckets into year folders

### global → local (blocked)

- **Validation check** (`validate_config` notebook): For each table where `watermark_scope = local`, query audit log for any prior ARCHIVED entry with `watermark_scope = global`. If found → fail with: "Table X was previously archived with global watermark. Cannot switch to local."
- **Runtime check** (`ArchiveEngine.run`): Same query, raises `ArchiveConfigError`. Table is not processed; error logged to audit.
- Cannot be bypassed — even if someone manually updates `table_configs`, the archiver refuses to proceed.

### Why block global → local?

With global mode, the archiver detects new data using a single global watermark — it never calculates per-year watermarks. Although audit entries are written per year (for consistency), the watermark stored in each entry reflects the global MAX, not a year-specific value. Switching to local mode requires accurate per-year watermarks that were never independently computed. The system would have to fall back to reading Delta folders, which may be in cold storage or moved. Rather than build fragile recovery logic, we block the switch entirely.

---

## 8. Watermark Source of Truth

**Audit log only.** The current Delta folder fallback (`_get_watermark_value` reading `MAX(wm_col)` from the archive Delta table) is removed.

| Scenario | Current behavior | New behavior |
|----------|-----------------|-------------|
| Audit has watermark | Use it | Use it (same) |
| Audit has no watermark, folder exists | Read MAX from Delta folder | Treat as first-time CREATE |

This eliminates the dependency on archive folders being readable for watermark purposes. The only time folders need to be readable is for APPEND writes, verification, and rehydration.

---

## 9. Operational Constraints

### Archive folder accessibility

Archive folders must remain accessible at their original External Volume path during the active retention window. The archiver may APPEND to any year folder within retention.

After the retention window passes, CareSource may move year folders to cold storage (S3 Glacier, different bucket, etc.). This is CareSource's AWS responsibility, outside the scope of this system.

### Rehydration of cold/moved archives

The rehydrator works standalone — it only needs a folder path, target catalog/schema, and year list. It does not depend on `table_configs` or the audit log.

To rehydrate cold data:
1. CareSource restores the folder to an accessible path (AWS operation)
2. Run the rehydrator with that folder path
3. External table + unified view are created as normal

### Late-arriving data

Neither `local` nor `global` mode catches records that arrive with dates below the stored watermark. If late-arriving backdated records are a concern, use an always-increasing column (`etl_load_date`) as the watermark column instead of the business date. This is a table onboarding decision documented in A11.

---

## 10. Files Changed

| File | Change |
|------|--------|
| `notebooks/setup_config_tables.py` | DDL: add `watermark_scope` to `table_configs` and `archive_audit_log` |
| `src/archiver.py` | Global mode pre-filter in `run()`, derive global watermark, bucket results by year |
| `src/archiver.py` | Remove Delta folder fallback in `_resolve_year_action` |
| `src/audit.py` | New `get_global_watermark(table)` method; add `watermark_scope` to `log_archive` |
| `src/audit.py` | New `has_global_history(table)` method for enforcement |
| `src/config.py` | Add `watermark_scope` to merge logic and validation |
| `notebooks/manual/validate_config.py` | Add global→local switch check |
| `tests/unit/test_archiver.py` | New tests for global mode: SKIP, APPEND, CREATE, mixed years |
| `tests/unit/test_audit.py` | New tests for `get_global_watermark`, `has_global_history` |
| `tests/unit/test_config.py` | Validation of `watermark_scope` values |
| `docs/requirements-summary.md` | Update A13, add A14, A15 |

---

## 11. Test Scenarios

### Global mode:
1. No new data → all years SKIPPED, one source query only
2. New data for one year → that year APPEND, others SKIPPED
3. New data spanning multiple years → each affected year gets CREATE or APPEND
4. First run (no prior archives) → CREATE for all eligible years

### Mode switching:
5. `local → global` → derives watermark from audit, runs correctly
6. `global → local` → blocked at validation with clear error
7. `global → local` → blocked at runtime if validation was skipped

### Enforcement:
8. Manual `UPDATE table_configs SET watermark_scope = 'local'` after global archives → runtime blocks it

### Watermark source of truth:
9. Audit has watermark → used directly
10. Audit has no watermark, folder exists → treated as CREATE (no Delta fallback)
