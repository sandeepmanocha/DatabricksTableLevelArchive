# 23 — Rehydration Failure Cascade, Permission Denial, Audit Fallback, and Recovery

**Goal:** Verify `FAILED` status paths across escalating failure scenarios — missing params, bad archive path, permission denial, audit infrastructure failure — then prove the system recovers cleanly.

**Depends on:** 05_archive_live_create (archives must exist for claims; year folders for 2020 and 2021 must be present under the claims archive layout)

**Results:** Append a new `## Run — <date> <time>` section **at the top** of `test_results/23R_rehydrate_failure_permissions_recovery_results.md` (below the H1 title). Do not overwrite previous runs.

## Execution Rules

> **DO NOT FIX CODE.** If a step fails or produces unexpected results, do **not** modify source code, notebooks, or SQL logic to make it pass. Instead:
>
> 1. **Record** the exact error, unexpected output, or deviation from expected behavior in the results file.
> 2. **Log** the issue with enough detail for a developer to reproduce (command run, actual vs expected output, full error messages).
> 3. **Continue** with remaining steps if possible (unless a failure makes subsequent failure steps meaningless).
> 4. **Summarize** at the end of the results file under a `## What Happened` section — plain-English description of everything that occurred.
> 5. **Recommend next steps** under a `## Next Steps` section — what the developer should investigate or fix, which test to re-run after the fix, and any manual actions needed.
> 6. Mark each step as **PASS**, **FAIL**, or **SKIP** (skipped due to prior failure) in the results.
> 7. Add a **TL;DR** (max 3 lines) right below the run header summarizing what happened and the outcome.
> 8. **Pre-flight check.** Before running, verify the environment state and report findings. If cleanup is needed, **tell the user what and why** — do not clean up without approval. Check:
>    - **Archive volume (claims):** Year folders for **2020** and **2021** must exist with valid Delta data (from test 05). Paths follow `{archive_base_path}/claims/year_2020` and `{archive_base_path}/claims/year_2021` under `<ARCHIVE_VOL>`. If missing, run test 05 first.
>    - **Target schema:** `<SOURCE_CATALOG>.<REHYDRATE_TARGET_SCHEMA>` should **not** exist at the start of this test (or you must `DROP SCHEMA ... CASCADE` before Phase 1). If it exists from a prior run, tell the user — it may need cleanup.
>    - **Rehydration audit table:** `<REHYDRATION_AUDIT_TABLE>` must exist before Phase 1 (typically `<CONFIG_TABLES_PREFIX>.rehydration_audit_log`). Phases 4–5 may drop and recreate it; note the starting state in your results. If missing, run `setup_config_tables` / config seeding per project runbooks.
>    - **Audit schema:** Record whether `<CONFIG_TABLES_PREFIX>` (catalog.schema for audit/config tables) is intact. Phases 4–5 may temporarily drop only `rehydration_audit_log` or, in the fallback stress case, the whole audit schema — document what you execute.
>    - **Source table:** `<SOURCE_CATALOG>.<SOURCE_SCHEMA>.claims` must have data so a successful recovery run can build the unified view.

---

## Workspace Parameters

> See [`_workspace_params.md`](./_workspace_params.md) for `<PROFILE>`, `<TARGET>`, `<CONFIG_TABLE>`, `<SOURCE_CATALOG>`, `<SOURCE_SCHEMA>`, `<ARCHIVE_VOL>`, `<CONFIG_TABLES_PREFIX>`, and `<AUDIT_TABLE>`.
>
> **This test adds:**
>
> | Placeholder | Description |
> | --- | --- |
> | `<REHYDRATE_TARGET_SCHEMA>` | Schema where external tables and unified view are created (e.g. `caresource_rehydrated`). Must be safe to drop and recreate during this test. |
> | `<REHYDRATION_AUDIT_TABLE>` | Full name of `rehydration_audit_log` — typically `<CONFIG_TABLES_PREFIX>.rehydration_audit_log`. |
>
> **Rehydration job parameters:** `config_table`, `archive_base_path`, `source_table`, `target_catalog`, `target_schema`, `years` (see each phase).

---

## Before (baseline hygiene)

Ensure a clean rehydrate target for the scripted phases (repeat or skip if you already confirmed empty):

```bash
databricks experimental aitools tools query \
  "DROP SCHEMA IF EXISTS <SOURCE_CATALOG>.<REHYDRATE_TARGET_SCHEMA> CASCADE" \
  --profile <PROFILE>
```

Confirm audit table presence:

```bash
databricks experimental aitools tools query \
  "DESCRIBE TABLE <REHYDRATION_AUDIT_TABLE>" \
  --profile <PROFILE>
```

---

## Steps

### 1. Phase 1 — Missing required params (empty `source_table`)

Run rehydration with an **empty** `source_table` widget value (empty string). Keep other parameters valid so the failure isolates `source_table`.

```bash
databricks bundle run caresource_rehydrate -t <TARGET> --profile <PROFILE> \
  --params config_table="<CONFIG_TABLE>",archive_base_path="<ARCHIVE_VOL>",source_table="",target_catalog="<SOURCE_CATALOG>",target_schema="<REHYDRATE_TARGET_SCHEMA>",years="2020,2021"
```

**Expect:**

- **Job fails** (non-success terminal state).
- **Notebook (`run_rehydrate.py`):** The widget guard runs **before** `RehydrationEngine.run()`. With an empty `source_table`, expect `ValueError` indicating the `source_table` widget is required (non-empty). The job log / driver output should show that error. The LOG-04 HTML block (`Rehydration failed (LOG-04)`) is only emitted when `ArchiveOperationError` is raised **inside** the `try` around `engine.run()` — so it may **not** appear for this phase; record what the notebook actually displays.
- **Engine behavior (reference):** In `rehydrator.py`, `invalid_params` is raised when a **required key is absent** from the `params` dict (`archive_base_path`, `source_table`, `target_catalog`, `target_schema`, `years`, `available_archive_years`). An empty string still supplies the key, so the engine’s `invalid_params` branch is **not** reached via empty string alone. Cross-check: `tests/unit/test_rehydrator.py::test_missing_required_param_fails_with_actionable_error` covers `invalid_params` and the FAILED audit write in the exception handler when a key is truly missing.
- **Audit:** Typically **no** new row from `log_rehydrate` for this run, because the failure happens before `engine.run()`. If your job configuration ever delivered `params` missing a required key to the engine, you would expect `ArchiveOperationError` with `reason=invalid_params`, FAILED audit with partial string fields, and LOG-04 HTML — document whichever path your workspace exhibits.

---

### 2. Phase 2 — Bad archive path (`LOCATION` / no years restored)

Use a non-existent volume path for `archive_base_path`. Keep `source_table`, `target_catalog`, `target_schema`, and `years` valid (`2020,2021`).

```bash
databricks bundle run caresource_rehydrate -t <TARGET> --profile <PROFILE> \
  --params config_table="<CONFIG_TABLE>",archive_base_path="/Volumes/<SOURCE_CATALOG>/nonexistent_volume/bad_path",source_table="<SOURCE_CATALOG>.<SOURCE_SCHEMA>.claims",target_catalog="<SOURCE_CATALOG>",target_schema="<REHYDRATE_TARGET_SCHEMA>",years="2020,2021"
```

**Expect:**

- **Job fails.**
- **Either:**
  - **No archive folders** at the bad base path: the notebook builds `available_archive_years` as an empty list, the engine skips every requested year, then raises `ArchiveOperationError` with `reason=no_years_restored` after the loop; or
  - **Folders incorrectly appear present** (unlikely on a bad path): the first `CREATE TABLE ... USING DELTA LOCATION` fails and the engine raises with `reason=location_create_failed`.
- **Audit:** Latest row for this run has `status` = `FAILED`. `error_message` should reference the failure (e.g. `no requested years restored` or the Delta/location exception) and may include the bad path in the diagnostic text.
- **Notebook:** On `ArchiveOperationError`, expect LOG-04 HTML with escaped exception text.

Query audit:

```bash
databricks experimental aitools tools query \
  "SELECT archive_path, status, error_message, created_at
   FROM <REHYDRATION_AUDIT_TABLE>
   ORDER BY created_at DESC
   LIMIT 5" \
  --profile <PROFILE>
```

---

### 3. Phase 3 — Inaccessible target catalog (permission proxy)

Use a **catalog the job’s identity cannot write to** (commonly `main`) for `target_catalog`. Keep `archive_base_path`, `source_table`, `target_schema`, and `years` valid. Ensure the rehydrate target schema under that catalog does not already grant success unless you intend to test a later failure mode.

```bash
databricks bundle run caresource_rehydrate -t <TARGET> --profile <PROFILE> \
  --params config_table="<CONFIG_TABLE>",archive_base_path="<ARCHIVE_VOL>",source_table="<SOURCE_CATALOG>.<SOURCE_SCHEMA>.claims",target_catalog="main",target_schema="<REHYDRATE_TARGET_SCHEMA>",years="2020,2021"
```

**Expect:**

- **Job fails** with a permission or authorization error during `CREATE SCHEMA IF NOT EXISTS` / `CREATE TABLE ... USING DELTA LOCATION` / related DDL (exact message depends on Unity Catalog grants).
- **Audit catalog is separate** from `target_catalog`: audit still targets `<CONFIG_TABLES_PREFIX>` from `global_settings`. Expect a **FAILED** row written to `<REHYDRATION_AUDIT_TABLE>` with `error_message` containing the permission-related exception text (and stack/context appended by the engine’s failure handler).
- **Notebook:** LOG-04 HTML with the failure message.

```bash
databricks experimental aitools tools query \
  "SELECT target_catalog, target_schema, status, error_message, created_at
   FROM <REHYDRATION_AUDIT_TABLE>
   ORDER BY created_at DESC
   LIMIT 5" \
  --profile <PROFILE>
```

---

### 4. Phase 4 — Audit table unreachable (recreate vs fallback)

#### 4a. Drop the rehydration audit table

```bash
databricks experimental aitools tools query \
  "DROP TABLE IF EXISTS <REHYDRATION_AUDIT_TABLE>" \
  --profile <PROFILE>
```

#### 4b. Run rehydration with **valid** parameters (same as a happy path)

```bash
databricks bundle run caresource_rehydrate -t <TARGET> --profile <PROFILE> \
  --params config_table="<CONFIG_TABLE>",archive_base_path="<ARCHIVE_VOL>",source_table="<SOURCE_CATALOG>.<SOURCE_SCHEMA>.claims",target_catalog="<SOURCE_CATALOG>",target_schema="<REHYDRATE_TARGET_SCHEMA>",years="2020,2021"
```

**Expect (branch A — `ensure_rehydration_audit_table` succeeds):**

- `ensure_rehydration_audit_table()` runs after the engine begins processing; it recreates `rehydration_audit_log` if missing.
- Rehydration may **complete successfully** (`COMPLETED` in audit, objects in target schema). Record job outcome and latest audit row.

**Expect (branch B — ensure succeeds at table creation but audit insert still fails):** Uncommon; document if seen.

**Expect (branch C — ensure cannot recreate, e.g. audit schema also removed):**

- If you **also** drop the entire audit schema (`DROP SCHEMA IF EXISTS <CONFIG_TABLES_PREFIX> CASCADE` — **destructive**; only on disposable dev workspaces and only if you plan to restore via `setup_config_tables` / runbooks), `ensure_rehydration_audit_table` may fail or be unable to create the table.
- When the main operation fails and `log_rehydrate` fails (table missing or insert error), `rehydrator.py` logs a **JSON** payload to the structured logger with:
  - `event` = `rehydration_audit_write_failed`
  - `primary_error` — original exception
  - `audit_error` — exception from the audit insert
  - plus audit fields: `archive_path`, `source_table`, `target_catalog`, `target_schema`, `years`, `tables_created`, `created_years`, `skipped_years`, `status`, etc.
- The engine then raises `ArchiveOperationError` with `reason=operation_failure_and_audit_failure`.

**Verification for fallback JSON:** This payload is emitted via `LOGGER.error(json.dumps(...))` — it appears in **Databricks job run logs / cluster driver logs**, not in the Delta audit table. Open the job run in the UI → **Run output** / **Driver logs** — search for `rehydration_audit_write_failed` or the sorted JSON line.

**Expect:**

- Capture **which branch** occurred (A, B, or C) in the results file, with excerpts from job logs when fallback applies.

---

### 5. Phase 5 — Recovery (restore audit + successful rehydration)

#### 5a. Restore audit objects if Phase 4 dropped schema or left table missing

If `<REHYDRATION_AUDIT_TABLE>` does not exist, recreate audit metadata using project runbooks (`setup_config_tables`, seed scripts) or minimal DDL consistent with your workspace. At minimum:

```bash
databricks experimental aitools tools query \
  "CREATE SCHEMA IF NOT EXISTS <CONFIG_TABLES_PREFIX>" \
  --profile <PROFILE>
```

Then run `setup_config_tables` / equivalent from the repo’s deployment docs so `rehydration_audit_log` exists with the expected schema, **or** rely on `ensure_rehydration_audit_table()` after `global_settings` resolves — follow what your environment requires.

Confirm:

```bash
databricks experimental aitools tools query \
  "DESCRIBE TABLE <REHYDRATION_AUDIT_TABLE>" \
  --profile <PROFILE>
```

#### 5b. Clean target schema before a clean success run

```bash
databricks experimental aitools tools query \
  "DROP SCHEMA IF EXISTS <SOURCE_CATALOG>.<REHYDRATE_TARGET_SCHEMA> CASCADE" \
  --profile <PROFILE>
```

#### 5c. Re-run rehydration with fully valid parameters

```bash
databricks bundle run caresource_rehydrate -t <TARGET> --profile <PROFILE> \
  --params config_table="<CONFIG_TABLE>",archive_base_path="<ARCHIVE_VOL>",source_table="<SOURCE_CATALOG>.<SOURCE_SCHEMA>.claims",target_catalog="<SOURCE_CATALOG>",target_schema="<REHYDRATE_TARGET_SCHEMA>",years="2020,2021"
```

**Expect:**

- Job **succeeds**.
- **Audit:** New row with `status` = `COMPLETED`, `tables_created` = **2**, `error_message` null (or empty).
- **Data:** `SHOW TABLES` in `<SOURCE_CATALOG>.<REHYDRATE_TARGET_SCHEMA>` lists external year tables and `claims_unified`; a grouped count query on `claims_unified` runs successfully.

```bash
databricks experimental aitools tools query \
  "SHOW TABLES IN <SOURCE_CATALOG>.<REHYDRATE_TARGET_SCHEMA>" \
  --profile <PROFILE>
```

```bash
databricks experimental aitools tools query \
  "SELECT YEAR(event_date) AS yr, COUNT(*) AS cnt
   FROM <SOURCE_CATALOG>.<REHYDRATE_TARGET_SCHEMA>.claims_unified
   GROUP BY 1 ORDER BY 1" \
  --profile <PROFILE>
```

```bash
databricks experimental aitools tools query \
  "SELECT archive_path, source_table, target_catalog, target_schema, years, tables_created, status, error_message, created_at
   FROM <REHYDRATION_AUDIT_TABLE>
   ORDER BY created_at DESC
   LIMIT 5" \
  --profile <PROFILE>
```

---

## Cleanup

Leave the workspace safe for other tests:

1. **Drop the rehydrate target schema** used in this test:

```bash
databricks experimental aitools tools query \
  "DROP SCHEMA IF EXISTS <SOURCE_CATALOG>.<REHYDRATE_TARGET_SCHEMA> CASCADE" \
  --profile <PROFILE>
```

2. **If** `<REHYDRATION_AUDIT_TABLE>` was dropped and not yet restored, recreate it (same as Phase 5a) so subsequent tests find a normal audit table.

3. **If** you ran the destructive `DROP SCHEMA ... CASCADE` on `<CONFIG_TABLES_PREFIX>` in Phase 4, restore **all** config and audit tables via `setup_config_tables` / project runbooks before running other integration tests.

**Expect:** Rehydrate target schema gone; `rehydration_audit_log` exists and is queryable; config/audit schemas consistent with a clean dev workspace.

---

## Reference — code paths

| Concern | Location |
| --- | --- |
| Required param keys / `invalid_params` | `src/rehydrator.py` (required keys and `reason=invalid_params`) |
| Failure handler, FAILED audit, fallback JSON, `operation_failure_and_audit_failure` | `src/rehydrator.py` (`except` around `run`) |
| Rehydration audit statuses | `src/audit.py` — `ALLOWED_REHYDRATION_STATUSES`, `log_rehydrate` |
| Widget pre-check (empty widget) | `notebooks/run_rehydrate.py` |
