# 27 — Rehydrate Schema Mismatch

**Goal:** When the live source table's schema diverges from the archived year tables (or one archived year diverges from another), the rehydrate notebook must fail fast with a clear `schema_mismatch` error before building the `UNION ALL` unified view. This prevents silent data corruption where columns would line up by position but not by name/meaning.

**Covers findings:** H6 (rehydrate `UNION ALL` by position, silent misalignment).

**Depends on:** 05_archive_live_create (claims archived for at least two years, e.g. 2020 + 2021).

**Results:** Append a new `## Run — <date> <time>` section **at the top** of `test_results/27R_rehydrate_schema_mismatch_results.md` (below the H1 title). Do not overwrite previous runs.

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
> 8. **Pre-flight check.** Before running, verify the environment state and report findings. If cleanup is needed, **tell the user what and why** — do not clean up without approval. Check:
>    - **Archive volume (claims):** Year folders `year_2020` and `year_2021` must exist with valid Delta data. If missing, run test 05 first.
>    - **Audit log (claims):** ARCHIVED entries for 2020 and 2021 must exist.
>    - **Source table (claims):** Must exist with its normal schema. This test will **temporarily ALTER the source schema** and must restore it in cleanup. If a prior run crashed mid-test, verify the schema via `DESCRIBE TABLE` before proceeding and restore if columns are missing/renamed.
>    - **Target schema:** `sandeep_manocha.caresource_rehydrated` may or may not exist. If it does, note whether it has `claims_*` objects from a prior run — tell user if cleanup is needed.
>    - **Other tables:** Do NOT touch `members` or `providers`.

---

## Phase 0 — Capture source schema baseline

Before any alteration, record the current column list so cleanup can restore it.

```sql
DESCRIBE TABLE sandeep_manocha.source_data_samples.claims
```

**Record** the column names in order (e.g. `claim_id, member_id, provider_id, claim_type, diagnosis_code, amount, status, event_date, created_at`). You will add and then drop a column in Phase 2, and you'll compare against this baseline in Phase 5.

---

## Phase 1 — Confirm happy-path rehydrate (baseline sanity)

Run rehydrate with a matching schema to prove the new check does not false-positive on a well-formed setup.

### 1a. Ensure target schema is clean

```sql
DROP SCHEMA IF EXISTS sandeep_manocha.caresource_rehydrated CASCADE;
CREATE SCHEMA sandeep_manocha.caresource_rehydrated
```

### 1b. Run rehydrate, two archived years + live source

```bash
databricks bundle run caresource_rehydrate -t dev --profile DEFAULT \
  --params config_table=sandeep_manocha.caresource_audit.global_settings \
  --params archive_base_path=/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/source_data_samples \
  --params source_table=sandeep_manocha.source_data_samples.claims \
  --params target_catalog=sandeep_manocha \
  --params target_schema=caresource_rehydrated \
  --params 'years="2020,2021"'
```

**Expect:** TERMINATED SUCCESS. `claims_year_2020`, `claims_year_2021`, `claims_unified` all present.

### 1c. Verify unified view queryable

```sql
SELECT COUNT(*) AS row_count FROM sandeep_manocha.caresource_rehydrated.claims_unified
```

**Expect:** A positive row count, no error.

### 1d. Drop the unified outputs to isolate Phase 2

```sql
DROP SCHEMA sandeep_manocha.caresource_rehydrated CASCADE;
CREATE SCHEMA sandeep_manocha.caresource_rehydrated
```

---

## Phase 2 — Induce a schema mismatch on the live source

### 2a. Add a new column to the live source (not present in archives)

```sql
ALTER TABLE sandeep_manocha.source_data_samples.claims
ADD COLUMN test27_marker STRING
```

### 2b. Verify column added

```sql
DESCRIBE TABLE sandeep_manocha.source_data_samples.claims
```

**Expect:** Column `test27_marker` appears at the end. The archived `year_2020` / `year_2021` tables do **not** have it — they are frozen at their original schema.

---

## Phase 3 — Run rehydrate, expect schema_mismatch failure

### 3a. Run rehydrate (same command as Phase 1b)

```bash
databricks bundle run caresource_rehydrate -t dev --profile DEFAULT \
  --params config_table=sandeep_manocha.caresource_audit.global_settings \
  --params archive_base_path=/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/source_data_samples \
  --params source_table=sandeep_manocha.source_data_samples.claims \
  --params target_catalog=sandeep_manocha \
  --params target_schema=caresource_rehydrated \
  --params 'years="2020,2021"'
```

**Expect:** Job TERMINATES with FAILURE during the unified-view build phase. The error must contain:
- `"rehydrate schema mismatch"` (operation / reason keywords)
- `operation="rehydrate_unified_view"` or equivalent
- `reason="schema_mismatch"` or equivalent
- the mismatched table names (live source and the first archived year)
- the differing column lists (baseline vs live source with `test27_marker`)

### 3b. Verify unified view was NOT created

```sql
SHOW TABLES IN sandeep_manocha.caresource_rehydrated
```

**Expect:** `claims_unified` is **absent**. External year tables may or may not have been registered — the key guarantee is that the unified `UNION ALL` was never executed and no silently-misaligned view was created.

### 3c. Check rehydration audit log

```sql
SELECT * FROM sandeep_manocha.caresource_audit.rehydration_audit_log
ORDER BY created_at DESC LIMIT 3
```

**Expect:** The latest entry has a non-success status (FAILED/ERROR) with the schema-mismatch message in its error field.

---

## Phase 4 — Optional: cross-year mismatch (stretch case)

If Phase 3 passed, also verify the check catches mismatch **between two archive years** (not just live-vs-archive). Skip if pressed for time; Phase 3 alone is sufficient coverage.

### 4a. Restore source schema first (so live matches one of the archive years)

```sql
ALTER TABLE sandeep_manocha.source_data_samples.claims
DROP COLUMN test27_marker
```

### 4b. Manually corrupt one archived year's schema

This is harder to do cleanly because the archive is a Delta folder on a volume. A workable approach: register an external table pointing at one year's data with an explicitly different column ORDER, then copy that into position. **Because this is invasive, you may skip this phase and mark it SKIPPED in results with the reason "deferred to follow-up test".**

---

## Phase 5 — Cleanup and restore

### 5a. Restore source schema (idempotent)

```sql
ALTER TABLE sandeep_manocha.source_data_samples.claims
DROP COLUMN IF EXISTS test27_marker
```

### 5b. Verify schema matches Phase 0 baseline

```sql
DESCRIBE TABLE sandeep_manocha.source_data_samples.claims
```

**Expect:** Column list matches the Phase 0 baseline exactly (no extra `test27_marker`).

### 5c. Drop rehydrated schema

```sql
DROP SCHEMA IF EXISTS sandeep_manocha.caresource_rehydrated CASCADE
```

### 5d. Re-run Phase 1 rehydrate to confirm end state is healthy

```bash
databricks bundle run caresource_rehydrate -t dev --profile DEFAULT \
  --params config_table=sandeep_manocha.caresource_audit.global_settings \
  --params archive_base_path=/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/source_data_samples \
  --params source_table=sandeep_manocha.source_data_samples.claims \
  --params target_catalog=sandeep_manocha \
  --params target_schema=caresource_rehydrated \
  --params 'years="2020,2021"'
```

**Expect:** SUCCESS. If this fails, the schema wasn't fully restored — re-run `DESCRIBE TABLE` and reconcile manually.

### 5e. Drop rehydrated schema again (final cleanup)

```sql
DROP SCHEMA IF EXISTS sandeep_manocha.caresource_rehydrated CASCADE
```
