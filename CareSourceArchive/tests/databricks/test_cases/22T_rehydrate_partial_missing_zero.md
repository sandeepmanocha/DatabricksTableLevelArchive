# 22 — Rehydration Partial Success, Missing Folders, and Zero-Restore

**Goal:** Verify `PARTIAL_COMPLETED` when some requested archive year folders are missing, `FAILED` when every requested year is missing (zero-restore), and behavior when an archive folder is deleted between runs (orphan external table + unified view).

**Depends on:** 05_archive_live_create (claims archives must exist for **2020** and **2021**). Year **2019** must **not** have a `claims/year_2019` folder under the archive base path—this test relies on that absence.

**Results:** Append a new `## Run — <date> <time>` section **at the top** of `test_results/22R_rehydrate_partial_missing_zero_results.md` (below the H1 title). Do not overwrite previous runs.

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
>    - **Archive volume (claims):** Under `<ARCHIVE_VOL>`, folders `claims/year_2020` and `claims/year_2021` must exist with valid Delta data (from test 05). Folder `claims/year_2019` must **not** exist—if it does, remove it only if your workspace policy allows, or pick another missing year consistent with your data; this procedure assumes 2019 is absent.
>    - **Target schema (Phases 1, 3, 4):** `<SOURCE_CATALOG>.<REHYDRATE_TARGET_SCHEMA>` should **not** exist at the start of Phase 1. If it exists from a prior run, tell the user — it needs `DROP SCHEMA IF EXISTS ... CASCADE` before a clean Phase 1.
>    - **Rehydration audit table:** Must exist at `<REHYDRATION_AUDIT_TABLE>` (typically `<CONFIG_TABLES_PREFIX>.rehydration_audit_log`). If missing, run `setup_config_tables` / config seeding per project runbooks.
>    - **Source table:** `<SOURCE_CATALOG>.<SOURCE_SCHEMA>.claims` must have data so the unified view’s `SELECT * FROM` source branch is valid.
>    - **Phase 2 isolation:** Phase 2 uses a separate disposable schema `<REHYDRATE_ZERO_TEST_SCHEMA>` (see steps). It must not exist before Phase 2, or drop it first so zero-restore is verified against an empty schema.

---

## Workspace Parameters

> See [`_workspace_params.md`](./_workspace_params.md) for `<PROFILE>`, `<TARGET>`, `<CONFIG_TABLE>`, `<SOURCE_CATALOG>`, `<SOURCE_SCHEMA>`, `<ARCHIVE_VOL>`, and `<CONFIG_TABLES_PREFIX>`.
>
> **This test adds:**
>
> | Placeholder | Description |
> | --- | --- |
> | `<REHYDRATE_TARGET_SCHEMA>` | Primary schema for Phases 1, 3, and 4 (e.g. `caresource_rehydrated_22`). Must be safe to `DROP SCHEMA ... CASCADE` in cleanup. |
> | `<REHYDRATION_AUDIT_TABLE>` | Full name of `rehydration_audit_log` — typically `<CONFIG_TABLES_PREFIX>.rehydration_audit_log`. |
> | `<REHYDRATE_ZERO_TEST_SCHEMA>` | **Phase 2 only:** disposable schema used to verify zero-restore without dropping Phase 1 objects. Example: `caresource_rehydrated_22_zero`. Must not exist before Phase 2 (or drop before running Phase 2). |
>
> **Notebook behavior (folder scan):** `run_rehydrate.py` builds `available_archive_years` by calling `archive_folder_exists` for each requested year under `<ARCHIVE_VOL>` (see notebook logic around the `available_archive_years` list). Only years with existing folders are passed to the engine; others are skipped in `rehydrator.py` with a warning.
>
> **Rehydration job parameters:** `config_table`, `archive_base_path` (use `<ARCHIVE_VOL>`), `source_table`, `target_catalog`, `target_schema`, `years`.

---

## Before (optional)

Confirm archive layout and absence of 2019:

**Manual step** — run in a workspace notebook (adjust `<ARCHIVE_VOL>`):

```python
dbutils.fs.ls("<ARCHIVE_VOL>/claims/year_2020")
dbutils.fs.ls("<ARCHIVE_VOL>/claims/year_2021")
# Expect exception or empty / missing path for 2019:
try:
    dbutils.fs.ls("<ARCHIVE_VOL>/claims/year_2019")
except Exception as e:
    print("expected: no folder or error", e)
```

---

## Steps

### 1. Phase 1 — Clean primary target and run rehydration (partial: three years, one folder missing)

Drop any prior primary schema, then request **2020, 2021, 2019**. Only 2020 and 2021 have folders; 2019 is excluded from `available_archive_years` by the notebook.

```bash
databricks experimental aitools tools query \
  "DROP SCHEMA IF EXISTS <SOURCE_CATALOG>.<REHYDRATE_TARGET_SCHEMA> CASCADE" \
  --profile <PROFILE>
```

```bash
databricks bundle run caresource_rehydrate -t <TARGET> --profile <PROFILE> \
  --params config_table="<CONFIG_TABLE>",archive_base_path="<ARCHIVE_VOL>",source_table="<SOURCE_CATALOG>.<SOURCE_SCHEMA>.claims",target_catalog="<SOURCE_CATALOG>",target_schema="<REHYDRATE_TARGET_SCHEMA>",years="2020,2021,2019"
```

**Expect:**

- Job completes successfully (bundle exit 0); engine returns **`PARTIAL_COMPLETED`** because `tables_created` (2) `< len(years)` (3).
- Notebook HTML summary shows `tables_created` = **2** (not 3).

---

### 2. Phase 1 — Verify PARTIAL_COMPLETED, objects, and audit

List tables:

```bash
databricks experimental aitools tools query \
  "SHOW TABLES IN <SOURCE_CATALOG>.<REHYDRATE_TARGET_SCHEMA>" \
  --profile <PROFILE>
```

Query unified view (should reflect **source + two external years** only—no 2019 branch, because only restored years are unioned):

```bash
databricks experimental aitools tools query \
  "SELECT YEAR(event_date) AS yr, COUNT(*) AS cnt
   FROM <SOURCE_CATALOG>.<REHYDRATE_TARGET_SCHEMA>.claims_unified
   GROUP BY 1 ORDER BY 1" \
  --profile <PROFILE>
```

Latest audit rows:

```bash
databricks experimental aitools tools query \
  "SELECT archive_path, source_table, target_catalog, target_schema, years, tables_created, status, error_message, created_at
   FROM <REHYDRATION_AUDIT_TABLE>
   ORDER BY created_at DESC
   LIMIT 5" \
  --profile <PROFILE>
```

**Expect:**

- `status` = **`PARTIAL_COMPLETED`**, `tables_created` = **2**, `error_message` IS NULL.
- `years` column stores the **requested** list (JSON string including 2019)—audit does not persist a separate `skipped_years` column; infer skips from requested years vs `tables_created`, or capture job/cluster logs for warnings about missing 2019.
- `claims_year_2020` and `claims_year_2021` exist; **`claims_year_2019` does not** (no folder → no external table).
- `claims_unified` exists and queries succeed; logical union is **source + 2020 + 2021** (engine unions only `created_years`).

---

### 3. Phase 2 — Prepare disposable schema for zero-restore

Phase 2 must **not** drop `<REHYDRATE_TARGET_SCHEMA>` (Phase 3 needs Phase 1 external tables). Use an isolated schema for the zero-restore case:

```bash
databricks experimental aitools tools query \
  "DROP SCHEMA IF EXISTS <SOURCE_CATALOG>.<REHYDRATE_ZERO_TEST_SCHEMA> CASCADE" \
  --profile <PROFILE>
```

---

### 4. Phase 2 — Run rehydration with years that have no archive folders

Use years **2015** and **2016** (adjust only if those folders accidentally exist in your volume—pick any two years with **no** `claims/year_YYYY` folders):

```bash
databricks bundle run caresource_rehydrate -t <TARGET> --profile <PROFILE> \
  --params config_table="<CONFIG_TABLE>",archive_base_path="<ARCHIVE_VOL>",source_table="<SOURCE_CATALOG>.<SOURCE_SCHEMA>.claims",target_catalog="<SOURCE_CATALOG>",target_schema="<REHYDRATE_ZERO_TEST_SCHEMA>",years="2015,2016"
```

**Expect:**

- Notebook raises **`ArchiveOperationError`** / job fails: engine has empty `created_years` and raises with **`reason=no_years_restored`** (no requested years restored).
- No external tables created in this run; **`CREATE OR REPLACE VIEW` is not reached** for success path.

---

### 5. Phase 2 — Verify FAILED audit and empty target

```bash
databricks experimental aitools tools query \
  "SHOW TABLES IN <SOURCE_CATALOG>.<REHYDRATE_ZERO_TEST_SCHEMA>" \
  --profile <PROFILE>
```

```bash
databricks experimental aitools tools query \
  "SELECT archive_path, source_table, target_schema, years, tables_created, status, error_message, created_at
   FROM <REHYDRATION_AUDIT_TABLE>
   WHERE target_schema = '<REHYDRATE_ZERO_TEST_SCHEMA>'
   ORDER BY created_at DESC
   LIMIT 3" \
  --profile <PROFILE>
```

**Expect:**

- **`status`** = **`FAILED`**, **`tables_created`** = **0**.
- **`error_message`** is non-null; it should include exception context (and typically `skipped_years` / `created_years` in the message text from the failure path in `rehydrator.py`).
- `SHOW TABLES` on `<REHYDRATE_ZERO_TEST_SCHEMA>` is **empty** (schema may exist from `CREATE SCHEMA IF NOT EXISTS`, but no tables/views from this failed run).
- Primary schema `<REHYDRATE_TARGET_SCHEMA>` from Phase 1 is **unchanged** (still has `claims_year_2020`, `claims_year_2021`, `claims_unified`).

---

### 6. Phase 3 — Delete `year_2021` archive folder (manual)

**Manual step** — run in a workspace notebook (same pattern as test 19 — `dbutils.fs.rm` with `recurse=True`):

```python
dbutils.fs.rm("<ARCHIVE_VOL>/claims/year_2021", recurse=True)
```

**Expect:**

- Folder no longer listed under `claims/` (or `ls` fails).

---

### 7. Phase 3 — Re-run Phase 1 parameters (folder scan sees only 2020)

Re-run with the **same** years as Phase 1. The notebook recomputes `available_archive_years`; after deletion, only **2020** should remain.

```bash
databricks bundle run caresource_rehydrate -t <TARGET> --profile <PROFILE> \
  --params config_table="<CONFIG_TABLE>",archive_base_path="<ARCHIVE_VOL>",source_table="<SOURCE_CATALOG>.<SOURCE_SCHEMA>.claims",target_catalog="<SOURCE_CATALOG>",target_schema="<REHYDRATE_TARGET_SCHEMA>",years="2020,2021,2019"
```

**Expect:**

- **`PARTIAL_COMPLETED`** again: `tables_created` = **1** (only 2020 restored this run: `IF NOT EXISTS` may no-op on `claims_year_2020`; 2021 and 2019 skipped—2021 missing folder, 2019 never had a folder).
- Engine rebuilds **`claims_unified`** using **`created_years` only** (source + `claims_year_2020` in the union). It does **not** include `claims_year_2021` in the view definition when 2021 is not in `created_years`.

---

### 8. Phase 3 — Document orphan table and view/query behavior

Inspect objects:

```bash
databricks experimental aitools tools query \
  "SHOW TABLES IN <SOURCE_CATALOG>.<REHYDRATE_TARGET_SCHEMA>" \
  --profile <PROFILE>
```

Try direct external table (may fail if Delta metadata cannot read deleted storage):

```bash
databricks experimental aitools tools query \
  "SELECT COUNT(*) AS n FROM <SOURCE_CATALOG>.<REHYDRATE_TARGET_SCHEMA>.claims_year_2021" \
  --profile <PROFILE>
```

Query unified view:

```bash
databricks experimental aitools tools query \
  "SELECT COUNT(*) AS n FROM <SOURCE_CATALOG>.<REHYDRATE_TARGET_SCHEMA>.claims_unified" \
  --profile <PROFILE>
```

**Expect (document actual outcomes in results file):**

- **`claims_year_2021` may still exist** as a catalog object from Phase 1, with **LOCATION** pointing at removed storage — direct query may **fail** or return errors depending on Delta/storage behavior.
- **`claims_unified`:** Per engine logic, the view should **`CREATE OR REPLACE`** to a union that **omits** 2021 when 2021 ∉ `created_years` — record whether the view DDL **succeeds** and whether **`SELECT` from the view** succeeds (it should if the view only unions source + `claims_year_2020`).
- If your observation differs (e.g. view creation error), capture full error text — **do not change code**; log under **What Happened**.

---

### 9. Phase 4 — Restore `year_2021` folder

Restore the deleted archive data. Typical options:

- Re-run **05_archive_live_create** (or the project’s archive workflow) so `claims/year_2021` exists again under `<ARCHIVE_VOL>`, **or**
- Restore from backup / recycle bin per your storage policy.

Verify:

```python
dbutils.fs.ls("<ARCHIVE_VOL>/claims/year_2021")
```

**Expect:** Folder exists with Delta files.

---

### 10. Phase 4 — Re-run Phase 1 parameters (idempotent second partial)

No schema drop — same params as Phase 1:

```bash
databricks bundle run caresource_rehydrate -t <TARGET> --profile <PROFILE> \
  --params config_table="<CONFIG_TABLE>",archive_base_path="<ARCHIVE_VOL>",source_table="<SOURCE_CATALOG>.<SOURCE_SCHEMA>.claims",target_catalog="<SOURCE_CATALOG>",target_schema="<REHYDRATE_TARGET_SCHEMA>",years="2020,2021,2019"
```

**Expect:**

- **`PARTIAL_COMPLETED`** again; **`tables_created`** = **2** (external table creates are `IF NOT EXISTS` no-ops where objects already exist; 2019 still missing).
- **Second** audit row for this schema with **`PARTIAL_COMPLETED`** (most recent run after Phase 3’s row).
- `claims_unified` recreated with union **source + 2020 + 2021** (2019 still absent).

```bash
databricks experimental aitools tools query \
  "SELECT archive_path, target_schema, years, tables_created, status, created_at
   FROM <REHYDRATION_AUDIT_TABLE>
   WHERE target_schema = '<REHYDRATE_TARGET_SCHEMA>'
   ORDER BY created_at DESC
   LIMIT 5" \
  --profile <PROFILE>
```

---

## Cleanup

Drop both the primary and Phase 2 disposable schemas:

```bash
databricks experimental aitools tools query \
  "DROP SCHEMA IF EXISTS <SOURCE_CATALOG>.<REHYDRATE_TARGET_SCHEMA> CASCADE" \
  --profile <PROFILE>
```

```bash
databricks experimental aitools tools query \
  "DROP SCHEMA IF EXISTS <SOURCE_CATALOG>.<REHYDRATE_ZERO_TEST_SCHEMA> CASCADE" \
  --profile <PROFILE>
```

If you created **`claims/year_2019`** for any reason during testing, remove it only if policy allows, so the next run retains the “2019 missing” precondition.

---

## Reference (engine behavior)

- **Partial vs completed:** `rehydrator.py` sets `PARTIAL_COMPLETED` when `tables_created != len(years)`; `COMPLETED` when equal.
- **Zero-restore:** If no year is restored (`created_years` empty), **`ArchiveOperationError`** with **`reason=no_years_restored`** is raised before unified view creation.
- **Unified view:** Built only from **`created_years`** (plus source `SELECT`); skipped years are not unioned even if an older external table object still exists.
