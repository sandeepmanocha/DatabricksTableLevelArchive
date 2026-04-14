# Information schema for scanner column discovery — plan

**Date:** 2026-04-06  
**Status:** Done  
**Related:** [bug_duplicate_matched_columns.MD](../../bug_duplicate_matched_columns.MD)

## Problem

`_list_table_columns` in `src/scanner.py` uses `DESCRIBE TABLE` and parses `col_name` rows. Databricks/Spark can append sections (e.g. `# Clustering Information`) and **repeat** partition columns, so the same logical column appears twice. That produces false `ambiguous` matches and duplicate entries in `all_matched_columns` (see bug doc).

## Goals

1. List **source** table columns from a **single, authoritative** metadata source: Unity Catalog [`INFORMATION_SCHEMA.COLUMNS`](https://docs.databricks.com/aws/en/sql/language-manual/information-schema/columns).
2. **One code path** for column discovery — **no** `DESCRIBE TABLE` fallback if `information_schema` fails.
3. Preserve **column order** using `ORDINAL_POSITION`.
4. Keep **call pattern** unchanged: `run_scanner` still calls `_list_table_columns(spark, catalog, schema, table_name)` once per table.

## Non-goals

- Changing `match_date_column`, staging merge, scanner log row shape, or `DESCRIBE DETAIL` (table size).
- Supporting non–Unity-Catalog environments in this change (project already assumes UC for archive/scanner flows).

## Design

### 1. Replace `_list_table_columns` implementation

**Query shape** (conceptual):

- **From:** `{catalog}.information_schema.columns` — the table’s **catalog** owns that `information_schema` view (Databricks UC pattern).
- **Select:** `column_name`.
- **Where:** `table_catalog`, `table_schema`, and `table_name` match the scanned table, compared using **SQL string literals** built with `sql_quote()` from `src/utils.py` (same pattern as other dynamic SQL in this repo).
- **Order by:** `ordinal_position`.

**Collect** rows into a list of column names in order. Do not parse `#` headers or partition appendix rows — they are not part of this relation.

**Imports:** Add `sql_quote` to the `src.scanner` import from `src.utils` if not already present.

### 2. `ensure_scanner_log_table` (separate concern)

Today it uses `DESCRIBE TABLE {audit_catalog}.{audit_schema}.scanner_log` only to verify that the **scanner log** table exists before the run. That is **not** source column listing.

**Phase A (this plan, minimum):** Can leave this as-is; behavior stays correct.

**Phase B (optional, for consistency):** Replace the existence check with a single query against `{audit_catalog}.information_schema.tables` (filter by `table_schema`, `table_name`), or another one-shot metadata check — **still no fallback chain**; pick one method only. Document in the same PR if done.

### 3. Tests (`tests/unit/test_scanner.py`)

Update mocks that today return `DESCRIBE TABLE` results for **data tables**:

- Branch detection: match on `information_schema.columns` (and table identity in the query string if tests rely on it).
- Mock rows: use `column_name` (and `ordinal_position` if the implementation reads it for ordering).

**Do not change** tests that assert `DESCRIBE TABLE` for **`scanner_log`** unless Phase B is implemented.

### 4. Documentation

- **Bug doc:** Short “Planned fix” pointer to this spec (already cross-linked from bug doc when both exist).
- **Optional:** One-line updates in `docs/features.md` or `docs/design.md` if they state that column discovery uses `DESCRIBE TABLE`.

## Manual verification (after implementation)

On a workspace where the bug reproduced (e.g. `bronze_table_lineage`):

1. Run the scanner with the same template/patterns.
2. Confirm `scanner_log` row: `all_matched_columns` contains **one** `event_date` for an exact pattern match, and `match_status` is **`matched`** when appropriate.

## Files expected to change

| File | Change |
|------|--------|
| `src/scanner.py` | `_list_table_columns` → `information_schema`; optional Phase B for `ensure_scanner_log_table` |
| `src/utils.py` | No change unless a small shared helper is introduced (prefer inline SQL + `sql_quote` first) |
| `tests/unit/test_scanner.py` | Mock SQL branches for column listing |
| `docs/bug_duplicate_matched_columns.MD` | Planned-fix link to this spec |
| `docs/features.md` / `docs/design.md` | Only if they mention `DESCRIBE` for column discovery |

## Approval / next step

After review, implement per this plan; then mark **Status** here as **Done** and update the bug doc with **Resolved** and PR reference.
