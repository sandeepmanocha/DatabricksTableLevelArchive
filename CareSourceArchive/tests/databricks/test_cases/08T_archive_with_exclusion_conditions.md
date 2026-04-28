# 08 — Archive with Exclusion Conditions (all three tables, both shapes)

**Goal:** Exercise both forms of `exclusion_conditions` in a single archive run across all three source tables. Rows matching the configured rules must be excluded from archiving; rows that don't match are archived as usual. Both the typed `(column, operator, value)` form and the raw-SQL `custom_sql` form are validated end-to-end.

| Table | Scope | Operator / Form | Rule |
|---|---|---|---|
| `claims` | `same_table` | `equals` | `status_flag = 'Active'` |
| `members` | `same_table` | `not_equals` | `enrollment_status != 'Terminated'` (archives only Terminated) |
| `providers` | `custom_sql` | raw SQL with placeholders | `EXISTS (SELECT 1 FROM <SOURCE_CATALOG>.<SOURCE_SCHEMA>.claims c WHERE c.provider_id = src.provider_id)` |

**Depends on:** 02_scanner_first_run (table_configs populated with all three tables active and watermark columns set)
**Results:** Append a new `## Run — <date> <time>` section **at the top** of `test_results/08R_archive_with_exclusion_conditions_results.md` (below the H1 title). Do not overwrite previous runs.

## Execution Rules

> **DO NOT FIX CODE.** If a step fails or produces unexpected results, do **not** modify source code, notebooks, or SQL logic to make it pass. Instead:
>
> 1. **Record** the exact error, unexpected output, or deviation from expected behavior in the results file.
> 2. **Log** the issue with enough detail for a developer to reproduce (command run, actual vs expected output, full error messages).
> 3. **Continue** with remaining steps if possible (unless a failure makes subsequent steps meaningless).
> 4. **Summarize** at the end of the results file under a `## What Happened` section — plain-English description of everything that occurred.
> 5. **Recommend next steps** under a `## Next Steps` section — what the developer should investigate or fix, which test to re-run after the fix, and any manual actions needed.
> 6. Mark each step as **PASS**, **FAIL**, or **SKIP** (skipped due to prior failure) in the results.
> 7. Add a **TL;DR** (max 3 lines) right below the run header summarizing what happened and the outcome.
> 8. **Pre-flight check.** Before running, verify the environment state and report findings. If cleanup is needed, **tell the user what and why** — do not clean up without approval. Only touch `claims`, `members`, and `providers` data. Check:
>    - **Audit log:** No `ARCHIVED`, `ARCHIVED_AND_DELETED`, `STARTED`, or `FAILED` entries for any of the three tables. If any exist, both the audit entries AND any matching archive folders must be cleared together — never one without the other (orphan = ERROR; matched = SKIP, exclusion logic never runs).
>    - **Archive volume:** No year folders under `<ARCHIVE_VOL>/{claims,members,providers}/`. Folders without matching audit entries = orphan ERROR. Folders with matching audit entries = SKIP (exclusion not exercised).
>    - **table_configs:** All three rows have `is_active = true`, `delete_after_archive = false`, and watermark columns set: `claims → event_date`, `members → start_date`, `providers → effective_date`. Their `exclusion_conditions` will be (re)set during this test; if any row already has non-NULL `exclusion_conditions`, note it and reset before proceeding.
>    - **Other tables:** Do NOT delete or modify config / audit / archive state for any table that is not `claims`, `members`, or `providers`.

---

## Workspace Parameters

> See [`_workspace_params.md`](./_workspace_params.md) for the full placeholder → value mapping per workspace.
> All commands below use `<PROFILE>`, `<TARGET>`, `<CONFIG_TABLE>`, `<SOURCE_CATALOG>`, `<SOURCE_SCHEMA>`, `<AUDIT_TABLE>`, `<TABLE_CONFIGS>`, and `<ARCHIVE_VOL>` placeholders.

`<TABLE_CONFIGS>` resolves to `<audit_catalog>.<audit_schema>.table_configs` from `global_settings` (e.g. `dev2_archive.metadata.table_configs`). `<AUDIT_TABLE>` resolves to `<audit_catalog>.<audit_schema>.archive_audit_log`.

---

## Before — Profile data and set exclusion conditions

### B.1 Distinct values per discriminator column (sanity)

```bash
databricks experimental aitools tools query \
  "SELECT 'claims' AS tbl, status_flag AS val, COUNT(*) AS cnt
     FROM <SOURCE_CATALOG>.<SOURCE_SCHEMA>.claims GROUP BY status_flag
   UNION ALL
   SELECT 'members', enrollment_status, COUNT(*)
     FROM <SOURCE_CATALOG>.<SOURCE_SCHEMA>.members GROUP BY enrollment_status
   UNION ALL
   SELECT 'providers', CAST(is_active AS STRING), COUNT(*)
     FROM <SOURCE_CATALOG>.<SOURCE_SCHEMA>.providers GROUP BY is_active
   ORDER BY tbl, val" \
  --profile <PROFILE>
```

### B.2 Expected exclusion counts per (table, year)

Confirm the conditions match a non-trivial slice of source data so per-condition counts in the audit log are non-zero:

```bash
databricks experimental aitools tools query \
  "SELECT 'claims_active_excluded' AS rule, YEAR(event_date) AS yr, COUNT(*) AS expected
     FROM <SOURCE_CATALOG>.<SOURCE_SCHEMA>.claims
     WHERE status_flag = 'Active'
     GROUP BY 2
   UNION ALL
   SELECT 'members_non_terminated_excluded', YEAR(start_date), COUNT(*)
     FROM <SOURCE_CATALOG>.<SOURCE_SCHEMA>.members
     WHERE enrollment_status != 'Terminated'
     GROUP BY 2
   UNION ALL
   SELECT 'providers_referenced_by_claims_excluded', YEAR(p.effective_date), COUNT(*)
     FROM <SOURCE_CATALOG>.<SOURCE_SCHEMA>.providers p
     WHERE EXISTS (
       SELECT 1 FROM <SOURCE_CATALOG>.<SOURCE_SCHEMA>.claims c
       WHERE c.provider_id = p.provider_id
     )
     GROUP BY 2
   ORDER BY rule, yr" \
  --profile <PROFILE>
```

Record these counts in the results file — they are the **expected values** for `per_condition_counts.<rule_name>` in step 2 and step 4.

### B.3 Set exclusion conditions on all three tables

> **Note:** `exclusion_conditions` is typed `ARRAY<STRUCT<name, scope, column, operator, value, sql>>`, not a plain string.
> Use `NAMED_STRUCT` syntax — JSON string literals fail with `DATATYPE_MISMATCH.CAST_WITHOUT_SUGGESTION`.
> The struct's `sql` field is dropped by `normalize_condition()` ([src/conditions.py](../../../src/conditions.py)); the SQL template for `scope='custom_sql'` goes in **`value`**, not `sql`. Always set `sql` to `CAST(NULL AS STRING)`.
> For `scope='custom_sql'`, `operator` and `column` are ignored at SQL-build time. Set both to `CAST(NULL AS STRING)`. (Pre-2026-04-27 builds required a placeholder operator from the strict enum — that requirement was removed in `feat/delta_config_build_v11_test_exclusions`.)

**claims — `same_table` / `equals`:**

```sql
UPDATE <TABLE_CONFIGS>
SET exclusion_conditions = ARRAY(NAMED_STRUCT(
      'name',     'active_claims',
      'scope',    'same_table',
      'column',   'status_flag',
      'operator', 'equals',
      'value',    'Active',
      'sql',      CAST(NULL AS STRING))),
    modified_by = 'manual',
    modified_at = current_timestamp(),
    change_reason = 'Test 08: exclude Active claims from archiving'
WHERE table_id = '<SOURCE_CATALOG>.<SOURCE_SCHEMA>.claims'
```

**members — `same_table` / `not_equals`:**

```sql
UPDATE <TABLE_CONFIGS>
SET exclusion_conditions = ARRAY(NAMED_STRUCT(
      'name',     'non_terminated_members',
      'scope',    'same_table',
      'column',   'enrollment_status',
      'operator', 'not_equals',
      'value',    'Terminated',
      'sql',      CAST(NULL AS STRING))),
    modified_by = 'manual',
    modified_at = current_timestamp(),
    change_reason = 'Test 08: archive only Terminated members (exclude all rows where enrollment_status != Terminated)'
WHERE table_id = '<SOURCE_CATALOG>.<SOURCE_SCHEMA>.members'
```

**providers — `custom_sql` (raw SQL predicate with `{source_alias}` / `{source_catalog}` / `{source_schema}` substitution):**

```sql
UPDATE <TABLE_CONFIGS>
SET exclusion_conditions = ARRAY(NAMED_STRUCT(
      'name',     'providers_referenced_by_claims',
      'scope',    'custom_sql',
      'column',   CAST(NULL AS STRING),
      'operator', CAST(NULL AS STRING),
      'value',    'EXISTS (SELECT 1 FROM {source_catalog}.{source_schema}.claims c WHERE c.provider_id = {source_alias}.provider_id)',
      'sql',      CAST(NULL AS STRING))),
    modified_by = 'manual',
    modified_at = current_timestamp(),
    change_reason = 'Test 08: exclude providers that are referenced by any claim (custom_sql with placeholder substitution)'
WHERE table_id = '<SOURCE_CATALOG>.<SOURCE_SCHEMA>.providers'
```

### B.4 Confirm conditions land in the config

```bash
databricks experimental aitools tools query \
  "SELECT table_id,
          element_at(exclusion_conditions, 1).name     AS rule_name,
          element_at(exclusion_conditions, 1).scope    AS rule_scope,
          element_at(exclusion_conditions, 1).operator AS rule_operator,
          element_at(exclusion_conditions, 1).value    AS rule_value
   FROM <TABLE_CONFIGS>
   WHERE table_id IN (
     '<SOURCE_CATALOG>.<SOURCE_SCHEMA>.claims',
     '<SOURCE_CATALOG>.<SOURCE_SCHEMA>.members',
     '<SOURCE_CATALOG>.<SOURCE_SCHEMA>.providers')
   ORDER BY table_id" \
  --profile <PROFILE>
```

**Expect:** Three rows with the rule names `active_claims`, `non_terminated_members`, `providers_referenced_by_claims` and the corresponding scope / operator / value.

---

## Steps

### 1. Dry run (all three tables, single bundle invocation)

```bash
databricks bundle run caresource_archive_run -t <TARGET> --profile <PROFILE> \
  --params config_table="<CONFIG_TABLE>",dry_run="true",source_catalog="<SOURCE_CATALOG>",source_schema="<SOURCE_SCHEMA>"
```

Capture the run URL.

### 2. Verify dry-run audit and per-condition counts

```bash
databricks experimental aitools tools query \
  "SELECT table_name, year, status, record_count, conditions_applied
   FROM <AUDIT_TABLE>
   WHERE status = 'DRY_RUN'
     AND archive_run_id = (SELECT MAX(archive_run_id) FROM <AUDIT_TABLE> WHERE status = 'DRY_RUN')
   ORDER BY table_name, year" \
  --profile <PROFILE>
```

**Expect (per (table, year) row):**
- `status = 'DRY_RUN'`
- `record_count` = `would_archive` (rows that survive the exclusion clause)
- `conditions_applied` JSON contains:
  - `total_eligible` = count of rows in that year above the watermark cutoff
  - `would_archive` ≤ `total_eligible`
  - `per_condition_counts.<rule_name>` = number of rows the single rule excluded that year (matches the expected value from step **B.2**)
  - `total_eligible - per_condition_counts.<rule_name> == would_archive` for each table-year

Record per-table per-year math in the results file:

| Table | Year | total_eligible | per_condition_counts.\<rule\> | would_archive | Match? |
|---|---|---|---|---|---|

### 3. Live CREATE run (same params, `dry_run="false"`)

```bash
databricks bundle run caresource_archive_run -t <TARGET> --profile <PROFILE> \
  --params config_table="<CONFIG_TABLE>",dry_run="false",source_catalog="<SOURCE_CATALOG>",source_schema="<SOURCE_SCHEMA>"
```

Capture the run URL. `delete_after_archive` is `false` in `dev`, so source rows must remain after this run.

### 4. Post-run verification

#### 4a. Audit log shows `ARCHIVED` per (table, year), with `conditions_applied` listing rule names

```bash
databricks experimental aitools tools query \
  "SELECT table_name, year, status, record_count, conditions_applied
   FROM <AUDIT_TABLE>
   WHERE status = 'ARCHIVED'
     AND archive_run_id = (SELECT MAX(archive_run_id) FROM <AUDIT_TABLE> WHERE status = 'ARCHIVED')
   ORDER BY table_name, year" \
  --profile <PROFILE>
```

**Expect:** `conditions_applied` is a JSON array containing exactly one rule name per table — `active_claims` for claims, `non_terminated_members` for members, `providers_referenced_by_claims` for providers (per `get_condition_names()` in [src/conditions.py](../../../src/conditions.py)).

#### 4b. Archive volume contains a year folder for every archived (table, year) and folder count matches `record_count`

```bash
databricks experimental aitools tools query \
  "SELECT 'claims_2020'   AS slot, COUNT(*) AS cnt FROM delta.\`<ARCHIVE_VOL>/claims/year_2020\`
   UNION ALL SELECT 'members_2020',   COUNT(*) FROM delta.\`<ARCHIVE_VOL>/members/year_2020\`
   UNION ALL SELECT 'providers_2022', COUNT(*) FROM delta.\`<ARCHIVE_VOL>/providers/year_2022\`" \
  --profile <PROFILE>
```

(Sample years above; verify each archived year. Each `cnt` must equal the `record_count` from 4a for the same `(table, year)`.)

#### 4c. Source row counts unchanged (no DELETE configured)

```bash
databricks experimental aitools tools query \
  "SELECT 'claims' AS tbl, COUNT(*) AS cnt FROM <SOURCE_CATALOG>.<SOURCE_SCHEMA>.claims
   UNION ALL SELECT 'members',   COUNT(*) FROM <SOURCE_CATALOG>.<SOURCE_SCHEMA>.members
   UNION ALL SELECT 'providers', COUNT(*) FROM <SOURCE_CATALOG>.<SOURCE_SCHEMA>.providers" \
  --profile <PROFILE>
```

**Expect:** Same totals as before the run (claims=5000, members=3000, providers=837 for the current sample dataset; substitute actual counts captured during pre-flight).

#### 4d. Excluded rows still present in source

```bash
databricks experimental aitools tools query \
  "SELECT 'claims_active_still_in_source' AS check, COUNT(*) AS cnt
     FROM <SOURCE_CATALOG>.<SOURCE_SCHEMA>.claims WHERE status_flag = 'Active'
   UNION ALL
   SELECT 'members_non_terminated_still_in_source', COUNT(*)
     FROM <SOURCE_CATALOG>.<SOURCE_SCHEMA>.members WHERE enrollment_status != 'Terminated'
   UNION ALL
   SELECT 'providers_referenced_by_claims_still_in_source', COUNT(*)
     FROM <SOURCE_CATALOG>.<SOURCE_SCHEMA>.providers p
     WHERE EXISTS (SELECT 1 FROM <SOURCE_CATALOG>.<SOURCE_SCHEMA>.claims c WHERE c.provider_id = p.provider_id)" \
  --profile <PROFILE>
```

**Expect:** All three counts equal the **B.2** totals (excluded rows were neither archived nor deleted).

#### 4e. Excluded rows are absent from the archive (spot-check)

```bash
databricks experimental aitools tools query \
  "SELECT 'claims_active_in_archive' AS check, COUNT(*) AS cnt
     FROM delta.\`<ARCHIVE_VOL>/claims/year_2020\` WHERE status_flag = 'Active'
   UNION ALL
   SELECT 'members_non_terminated_in_archive', COUNT(*)
     FROM delta.\`<ARCHIVE_VOL>/members/year_2020\` WHERE enrollment_status != 'Terminated'" \
  --profile <PROFILE>
```

**Expect:** Both counts = `0`. (Provider archive intentionally has no easy spot-check predicate because the exclusion is cross-table.)

#### 4f. Audit `record_count` matches archive folder count for every (table, year)

This is the regression check for the 2026-04-27 alias bug. Before the fix, providers / `custom_sql` rows here would show `audit=0` and `folder=N` for years 2022, 2024, 2025.

```bash
databricks experimental aitools tools query \
  "WITH audit AS (
     SELECT table_name, year, record_count
     FROM <AUDIT_TABLE>
     WHERE status='ARCHIVED'
       AND archive_run_id = (SELECT MAX(archive_run_id) FROM <AUDIT_TABLE> WHERE status='ARCHIVED')
   ),
   folders AS (
     SELECT '<SOURCE_CATALOG>.<SOURCE_SCHEMA>.claims'    AS table_name, 2018 AS year, (SELECT COUNT(*) FROM delta.\`<ARCHIVE_VOL>/claims/year_2018\`)    AS folder_count
     UNION ALL SELECT '<SOURCE_CATALOG>.<SOURCE_SCHEMA>.claims',    2019, (SELECT COUNT(*) FROM delta.\`<ARCHIVE_VOL>/claims/year_2019\`)
     UNION ALL SELECT '<SOURCE_CATALOG>.<SOURCE_SCHEMA>.claims',    2020, (SELECT COUNT(*) FROM delta.\`<ARCHIVE_VOL>/claims/year_2020\`)
     UNION ALL SELECT '<SOURCE_CATALOG>.<SOURCE_SCHEMA>.claims',    2021, (SELECT COUNT(*) FROM delta.\`<ARCHIVE_VOL>/claims/year_2021\`)
     UNION ALL SELECT '<SOURCE_CATALOG>.<SOURCE_SCHEMA>.claims',    2022, (SELECT COUNT(*) FROM delta.\`<ARCHIVE_VOL>/claims/year_2022\`)
     UNION ALL SELECT '<SOURCE_CATALOG>.<SOURCE_SCHEMA>.claims',    2023, (SELECT COUNT(*) FROM delta.\`<ARCHIVE_VOL>/claims/year_2023\`)
     UNION ALL SELECT '<SOURCE_CATALOG>.<SOURCE_SCHEMA>.claims',    2024, (SELECT COUNT(*) FROM delta.\`<ARCHIVE_VOL>/claims/year_2024\`)
     UNION ALL SELECT '<SOURCE_CATALOG>.<SOURCE_SCHEMA>.claims',    2025, (SELECT COUNT(*) FROM delta.\`<ARCHIVE_VOL>/claims/year_2025\`)
     UNION ALL SELECT '<SOURCE_CATALOG>.<SOURCE_SCHEMA>.members',   2019, (SELECT COUNT(*) FROM delta.\`<ARCHIVE_VOL>/members/year_2019\`)
     UNION ALL SELECT '<SOURCE_CATALOG>.<SOURCE_SCHEMA>.members',   2020, (SELECT COUNT(*) FROM delta.\`<ARCHIVE_VOL>/members/year_2020\`)
     UNION ALL SELECT '<SOURCE_CATALOG>.<SOURCE_SCHEMA>.members',   2021, (SELECT COUNT(*) FROM delta.\`<ARCHIVE_VOL>/members/year_2021\`)
     UNION ALL SELECT '<SOURCE_CATALOG>.<SOURCE_SCHEMA>.members',   2022, (SELECT COUNT(*) FROM delta.\`<ARCHIVE_VOL>/members/year_2022\`)
     UNION ALL SELECT '<SOURCE_CATALOG>.<SOURCE_SCHEMA>.members',   2023, (SELECT COUNT(*) FROM delta.\`<ARCHIVE_VOL>/members/year_2023\`)
     UNION ALL SELECT '<SOURCE_CATALOG>.<SOURCE_SCHEMA>.members',   2024, (SELECT COUNT(*) FROM delta.\`<ARCHIVE_VOL>/members/year_2024\`)
     UNION ALL SELECT '<SOURCE_CATALOG>.<SOURCE_SCHEMA>.members',   2025, (SELECT COUNT(*) FROM delta.\`<ARCHIVE_VOL>/members/year_2025\`)
     UNION ALL SELECT '<SOURCE_CATALOG>.<SOURCE_SCHEMA>.providers', 2021, (SELECT COUNT(*) FROM delta.\`<ARCHIVE_VOL>/providers/year_2021\`)
     UNION ALL SELECT '<SOURCE_CATALOG>.<SOURCE_SCHEMA>.providers', 2022, (SELECT COUNT(*) FROM delta.\`<ARCHIVE_VOL>/providers/year_2022\`)
     UNION ALL SELECT '<SOURCE_CATALOG>.<SOURCE_SCHEMA>.providers', 2023, (SELECT COUNT(*) FROM delta.\`<ARCHIVE_VOL>/providers/year_2023\`)
     UNION ALL SELECT '<SOURCE_CATALOG>.<SOURCE_SCHEMA>.providers', 2024, (SELECT COUNT(*) FROM delta.\`<ARCHIVE_VOL>/providers/year_2024\`)
     UNION ALL SELECT '<SOURCE_CATALOG>.<SOURCE_SCHEMA>.providers', 2025, (SELECT COUNT(*) FROM delta.\`<ARCHIVE_VOL>/providers/year_2025\`)
   )
   SELECT a.table_name, a.year, a.record_count, f.folder_count,
          CASE WHEN a.record_count = f.folder_count THEN 'PASS' ELSE 'FAIL' END AS match
   FROM audit a JOIN folders f USING (table_name, year)
   ORDER BY a.table_name, a.year" \
  --profile <PROFILE>
```

**Expect:** Every row PASS. Any FAIL → record the exact `(table, year, audit, folder)` tuple in 08R and stop. Do **not** continue to Cleanup with a FAIL.

> The folder UNION-ALL is hard-coded to the 20 (table, year) rows produced by the current sample dataset (claims 2018-2025, members 2019-2025, providers 2021-2025). If the source dataset changes, edit the folder block to match.

---

## Cleanup

Reset all three exclusions so subsequent tests start from a clean configuration:

```sql
UPDATE <TABLE_CONFIGS>
SET exclusion_conditions = NULL,
    modified_by = 'manual',
    modified_at = current_timestamp(),
    change_reason = 'Test 08: cleanup — remove test exclusions'
WHERE table_id IN (
  '<SOURCE_CATALOG>.<SOURCE_SCHEMA>.claims',
  '<SOURCE_CATALOG>.<SOURCE_SCHEMA>.members',
  '<SOURCE_CATALOG>.<SOURCE_SCHEMA>.providers');
```

> Setting to `NULL` does not require struct syntax.

Verify:

```bash
databricks experimental aitools tools query \
  "SELECT table_id, exclusion_conditions IS NULL AS is_null
   FROM <TABLE_CONFIGS>
   WHERE table_id IN (
     '<SOURCE_CATALOG>.<SOURCE_SCHEMA>.claims',
     '<SOURCE_CATALOG>.<SOURCE_SCHEMA>.members',
     '<SOURCE_CATALOG>.<SOURCE_SCHEMA>.providers')" \
  --profile <PROFILE>
```

**Expect:** All three rows have `is_null = true`.
