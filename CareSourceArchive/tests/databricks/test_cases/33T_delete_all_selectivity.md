# 33 — Delete All Selectivity (Only Archived Years Get Deleted)

**Goal:** Prove the `"delete everything I archived"` pattern from 32T is **selective** — a schema-wide live delete touches only `(table, year)` pairs that have a prior `ARCHIVED` audit row, and leaves every other eligible pair untouched with a machine-readable refusal reason. This closes the selectivity gap that 32T does not exercise because 32T archives every eligible year.

**Covers:**

- `AuditLogger.is_eligible_for_delete` (D13) applied during **dry-run and live** delete paths.
- Dry-run branching: `DRY_RUN / action=WOULD_DELETE` vs `DRY_RUN / action=SKIP_NOT_ELIGIBLE` per `(table, year)`.
- Live branching: `ARCHIVED_AND_DELETED / archive_mode=DELETE` vs `FAILED / not_eligible_for_delete / archive_mode IS NULL` per `(table, year)`.
- `generate_parameters._resolve_delete_years` returning every eligible year per table, across a schema where some tables are archived and others are not.
- `DeleteJob.run` -> `_record_delete_skip` (in `src/delete_job.py`) producing the same shape for dry-run SKIP and live FAILED branches with a shared `reason_code`.
- **Non-goal:** scope-guard behavior (covered in 30T Phase 6), single-year D13 refusal (covered in 30T Phase 5), broad-scope mechanics (covered in 32T).

**Depends on:** 01_setup_and_deploy. **Touches every table in the schema.** Do not run in parallel with 25T/29T/30T/32T.

**Results:** Append a new `## Run — <date> <time>` section **at the top** of `test_results/33R_delete_all_selectivity_results.md` (below the H1 title).

## Execution Rules

> **DO NOT FIX CODE.** Same rules as 32T. Record exact output, don't modify code to pass, continue where possible, PASS/FAIL/SKIP each step, TL;DR at the top of the run, "What Happened" and "Next Steps" sections at the bottom.

## The selectivity pivot

The difference from 32T is exactly one knob: **Phase 2 archives a strict subset of the schema.** Everything else about the schema-wide delete run is identical.

| Table | Phase 2 archived? | Phase 3 dry-run expects | Phase 4 live expects |
|---|---|---|---|
| claims    | **yes** — all eligible years | `DRY_RUN / WOULD_DELETE` per year       | `ARCHIVED_AND_DELETED / DELETE` per year; source dated rows = 0 |
| members   | **yes** — all eligible years | `DRY_RUN / WOULD_DELETE` per year       | `ARCHIVED_AND_DELETED / DELETE` per year; source dated rows = 0 |
| providers | **no**                        | `DRY_RUN / SKIP_NOT_ELIGIBLE` per year | `FAILED / not_eligible_for_delete` per year; source **unchanged** |

If any providers year ends up in `ARCHIVED_AND_DELETED` in Phase 4, or any providers source count drops, that is a selectivity regression — report it and stop.

---

## Phase 1 — Clean preconditions across the schema

Identical to 32T Phase 1. Summary:

1. `DELETE` audit rows for all three tables in `dev2_archive.metadata.archive_audit_log`.
2. `databricks fs rm -r` on the three per-table archive subfolders under `/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples/{claims,members,providers}` (each with `|| true`).
3. `UPDATE table_configs SET delete_after_archive = false` for the whole schema.
4. `databricks bundle run generate_test_data -t dev-serverless --profile fe-sandbox-manocha`.
5. Re-record per-year source baselines as `BASELINE_{tbl}_{yr}` and `BASELINE_{tbl}_NULL`.

---

## Phase 2 — Seed archive for **claims and members only** (leave providers un-archived)

### 2a. Archive run scoped to claims + members

```bash
databricks bundle run caresource_archive_run -t dev-serverless --profile fe-sandbox-manocha \
  --params config_table="dev2_archive.metadata.global_settings" \
  --params dry_run="false" \
  --params source_catalog="dev2_archive" \
  --params source_schema="source_data_samples" \
  --params table_config_filter="source_table IN ('claims','members')"
```

> The `table_config_filter` is the mechanism that cleanly excludes providers from this archive run. `load_table_configs` applies the filter verbatim against `table_configs`.

### 2b. Verify: claims + members fully archived, providers has **zero** audit rows

```sql
SELECT table_name, year, status, archive_mode, record_count, archive_run_id
FROM dev2_archive.metadata.archive_audit_log
WHERE table_name LIKE 'dev2_archive.source_data_samples.%'
  AND status = 'ARCHIVED'
ORDER BY table_name, year
```

**Expect:**

- Rows only for `claims` (2018–2025) and `members` (2019–2025). Count should equal the 8 + 7 = 15 archived `(table, year)` pairs.
- **Zero rows with `table_name = 'dev2_archive.source_data_samples.providers'`.** Record this as the critical pre-condition — if providers has any ARCHIVED row, Phase 4's selectivity proof is meaningless.
- All archived rows share a single `SEED_RUN_ID`.

### 2c. Source unchanged

Re-run the baseline query. Every count must equal `BASELINE_*`.

### 2d. Providers archive folder does **not** exist

```bash
databricks fs ls dbfs:/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples/providers \
  --profile fe-sandbox-manocha
```

**Expect:** Non-zero exit with `Error: no such directory` — the archive job never wrote any provider year. If a folder exists, the filter didn't apply as intended; stop and diagnose.

---

## Phase 3 — Dry-run delete-all across the **whole schema**

### 3a. Dry-run the delete job with schema scope only

```bash
databricks bundle run caresource_delete_source_after_archive -t dev-serverless --profile fe-sandbox-manocha \
  --params config_table="dev2_archive.metadata.global_settings" \
  --params dry_run="true" \
  --params source_catalog="dev2_archive" \
  --params source_schema="source_data_samples"
```

> Deliberately **do not** re-apply the `table_config_filter` — we want the delete job to see all three tables in `table_configs`, discover eligible years per table, and apply D13 per pair. This is the honest schema-wide scope.

### 3b. Verify branching: WOULD_DELETE for archived, SKIP_NOT_ELIGIBLE for providers

```sql
SELECT table_name, year, status,
       get_json_object(conditions_applied, '$.action') AS action,
       get_json_object(conditions_applied, '$.reason_code') AS reason,
       record_count, archive_run_id
FROM dev2_archive.metadata.archive_audit_log
WHERE table_name LIKE 'dev2_archive.source_data_samples.%'
  AND status = 'DRY_RUN'
ORDER BY table_name, year
```

**Expect:**

- `claims` rows: `action = 'WOULD_DELETE'`, `record_count` equals `BASELINE_claims_{yr}`, `reason IS NULL`.
- `members` rows: same shape, claims-analog.
- `providers` rows: `action = 'SKIP_NOT_ELIGIBLE'`, `reason = 'not_archived_state'` (exact code depends on `is_eligible_for_delete` — record whatever reason_code appears), `record_count = 0`.
- All rows share a single `DRY_RUN_ID` distinct from `SEED_RUN_ID`.
- Count totals: 8 (claims) + 7 (members) + 6 (providers) = **21 DRY_RUN rows**, but split 15 `WOULD_DELETE` / 6 `SKIP_NOT_ELIGIBLE`.

### 3c. Source unchanged after dry-run

Re-run the baseline query. Every count must equal `BASELINE_*` still.

---

## Phase 4 — Live delete-all across the **whole schema**

### 4a. Live delete, schema scope only

```bash
databricks bundle run caresource_delete_source_after_archive -t dev-serverless --profile fe-sandbox-manocha \
  --params config_table="dev2_archive.metadata.global_settings" \
  --params dry_run="false" \
  --params source_catalog="dev2_archive" \
  --params source_schema="source_data_samples"
```

### 4b. Audit: claims + members got DELETE; providers got FAILED / not_eligible_for_delete

```sql
SELECT table_name, year, status, archive_mode, archive_delta_version, record_count,
       SUBSTRING(error_message, 1, 180) AS message_excerpt,
       archive_run_id
FROM dev2_archive.metadata.archive_audit_log
WHERE table_name LIKE 'dev2_archive.source_data_samples.%'
  AND archive_run_id = '<DELETE_RUN_ID>'   -- fill in from job output
ORDER BY table_name, year
```

**Expect:**

- **claims + members (15 rows):** `status = 'ARCHIVED_AND_DELETED'`, `archive_mode = 'DELETE'`, `archive_delta_version IS NULL`, `record_count = BASELINE_{tbl}_{yr}`, `message_excerpt` references `SEED_RUN_ID`.
- **providers (6 rows):** `status = 'FAILED'`, `archive_mode IS NULL`, `archive_delta_version IS NULL`, `record_count = 0`, `message_excerpt` contains `not_eligible_for_delete` and a reason (e.g. `not_archived_state`) and a pointer to `docs/runbooks/delete-source-after-archive.md`.
- Total new rows under `DELETE_RUN_ID`: 15 + 6 = **21**. No row of any kind with `archive_mode = 'DELETE'` for providers.

### 4c. Source: claims + members dated rows gone, providers **entirely unchanged**

```sql
SELECT 'claims' AS tbl, YEAR(event_date) AS yr, COUNT(*) AS cnt FROM dev2_archive.source_data_samples.claims GROUP BY 1,2
UNION ALL
SELECT 'members', YEAR(start_date), COUNT(*) FROM dev2_archive.source_data_samples.members GROUP BY 1,2
UNION ALL
SELECT 'providers', YEAR(effective_date), COUNT(*) FROM dev2_archive.source_data_samples.providers GROUP BY 1,2
ORDER BY tbl, yr
```

**Expect:**

- `claims`: `yr IS NULL` only (`cnt = BASELINE_claims_NULL`); every dated year row gone (`cnt = 0`, which means the GROUP BY yields no row for that year).
- `members`: same pattern as claims.
- `providers`: **every** year row still present with `cnt = BASELINE_providers_{yr}`, including `yr IS NULL`. If any providers year shows `cnt < BASELINE`, that is a selectivity bug — capture immediately.

### 4d. Providers archive folder still does not exist

```bash
databricks fs ls dbfs:/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples/providers \
  --profile fe-sandbox-manocha
```

**Expect:** Still `Error: no such directory`. The delete job does not create archive artifacts; if a providers folder appears, something wrote to it unexpectedly.

### 4e. claims + members archive Deltas unchanged

Spot-check a few `(table, year)` archive paths to confirm the delete did not rewrite them. `cnt` must still equal `BASELINE_{tbl}_{yr}` for every spot-checked path.

---

## Phase 5 — Cleanup + restore

Identical to 32T Phase 5 — clear the 21 new audit rows, drop the claims + members archive folders (providers has nothing to drop), run `generate_test_data` to restore source data (claims + members dated rows were deleted by this test).

```sql
DELETE FROM dev2_archive.metadata.archive_audit_log
WHERE table_name IN (
  'dev2_archive.source_data_samples.claims',
  'dev2_archive.source_data_samples.members',
  'dev2_archive.source_data_samples.providers'
)
```

```bash
databricks fs rm -r dbfs:/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples/claims  --profile fe-sandbox-manocha || true
databricks fs rm -r dbfs:/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples/members --profile fe-sandbox-manocha || true
databricks bundle run generate_test_data -t dev-serverless --profile fe-sandbox-manocha
```

---

## What this test proves (that 32T does not)

1. **The delete-all is selective.** Of 21 eligible `(table, year)` pairs in the schema, only the 15 with a prior `ARCHIVED` row receive an `ARCHIVED_AND_DELETED / DELETE` row; the remaining 6 — the providers pairs — receive `FAILED / not_eligible_for_delete` with `archive_mode IS NULL`.
2. **Dry-run is honest about selectivity.** Operators previewing a schema-wide delete see `SKIP_NOT_ELIGIBLE` rows upfront for any `(table, year)` that would be refused live. No operator should ever be surprised by a live FAILED row that didn't appear as SKIP in the dry-run.
3. **The selectivity enforcement lives in `AuditLogger.is_eligible_for_delete` (D13).** Both paths route through the same check, which is why the dry-run and live outcomes for providers line up row-for-row.
4. **The delete job never writes to un-archived tables.** Providers source is byte-identical before and after Phase 4; no archive folder is created; no audit row with `archive_mode = 'DELETE'` ever mentions providers.
