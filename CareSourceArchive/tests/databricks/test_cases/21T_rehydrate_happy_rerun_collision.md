# 21 — Rehydration Happy Path, Re-run, and Object Collision

**Goal:** Verify full rehydration success, idempotent re-run safety, and behavior when TABLE/VIEW name collisions exist in the target schema.

**Depends on:** 05_archive_live_create (archives must exist for claims years 2020 and 2021)

**Results:** Append a new `## Run — <date> <time>` section **at the top** of `test_results/21R_rehydrate_happy_rerun_collision_results.md` (below the H1 title). Do not overwrite previous runs.

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
>    - **Archive volume (claims):** Year folders for **2020** and **2021** must exist with valid Delta data (from test 05). The rehydration job reads `{archive_base_path}/claims/year_YYYY` under `<ARCHIVE_VOL>`. If folders are missing or empty, run test 05 first.
>    - **Target schema:** `<SOURCE_CATALOG>.<REHYDRATE_TARGET_SCHEMA>` should **not** exist at the start of Phase 1 unless you intentionally run `DROP SCHEMA IF EXISTS ... CASCADE` in the steps below. If it exists from a prior run, tell the user — it may need `DROP SCHEMA ... CASCADE` cleanup before a clean Phase 1.
>    - **Rehydration audit table:** Must exist at `<REHYDRATION_AUDIT_TABLE>`. If missing, run `setup_config_tables` / config seeding per project runbooks.
>    - **Source table:** `<SOURCE_CATALOG>.<SOURCE_SCHEMA>.claims` must have current-year data so the unified view (`CREATE OR REPLACE VIEW ...` union body) can include live rows alongside archived years.

---

## Workspace Parameters

> See [`_workspace_params.md`](./_workspace_params.md) for `<PROFILE>`, `<TARGET>`, `<CONFIG_TABLE>`, `<SOURCE_CATALOG>`, `<SOURCE_SCHEMA>`, `<ARCHIVE_VOL>`, `<AUDIT_TABLE>`, and `<CONFIG_TABLES_PREFIX>`.
>
> **This test adds:**
>
> | Placeholder | Description |
> | --- | --- |
> | `<REHYDRATE_TARGET_SCHEMA>` | Schema where external tables and unified view are created (for example `caresource_rehydrated`). Must be safe to drop and recreate during this test. |
> | `<REHYDRATION_AUDIT_TABLE>` | Full name of `rehydration_audit_log` — typically `<CONFIG_TABLES_PREFIX>.rehydration_audit_log` (same audit schema as archive metadata). |
>
> **Rehydrate job parameters:** `config_table`, `archive_base_path` (use `<ARCHIVE_VOL>` as the archive base path, consistent with test 14), `source_table`, `target_catalog`, `target_schema`, `years`.

---

## Before (pre-flight)

Optional: confirm archive Delta paths are readable (same pattern as test 05):

```bash
databricks experimental aitools tools query \
  "SELECT COUNT(*) AS cnt FROM delta.\`<ARCHIVE_VOL>/claims/year_2020\`" \
  --profile <PROFILE>
```

```bash
databricks experimental aitools tools query \
  "SELECT COUNT(*) AS cnt FROM delta.\`<ARCHIVE_VOL>/claims/year_2021\`" \
  --profile <PROFILE>
```

**Expect:** Both queries succeed with `cnt > 0`.

Record source-side row counts by year (helps interpret the unified view):

```bash
databricks experimental aitools tools query \
  "SELECT YEAR(event_date) AS yr, COUNT(*) AS cnt
   FROM <SOURCE_CATALOG>.<SOURCE_SCHEMA>.claims
   GROUP BY 1 ORDER BY 1" \
  --profile <PROFILE>
```

**Expect:** Current-year rows exist so `claims_unified` is non-empty for the live slice of the union.

---

## Steps

### 1. Phase 1 — Clean target and run rehydration (fresh run)

Ensure a clean target for the first run:

```bash
databricks experimental aitools tools query \
  "DROP SCHEMA IF EXISTS <SOURCE_CATALOG>.<REHYDRATE_TARGET_SCHEMA> CASCADE" \
  --profile <PROFILE>
```

Run rehydration for 2020 and 2021:

```bash
databricks bundle run caresource_rehydrate -t <TARGET> --profile <PROFILE> \
  --params config_table="<CONFIG_TABLE>",archive_base_path="<ARCHIVE_VOL>",source_table="<SOURCE_CATALOG>.<SOURCE_SCHEMA>.claims",target_catalog="<SOURCE_CATALOG>",target_schema="<REHYDRATE_TARGET_SCHEMA>",years="2020,2021"
```

**Expect:**

- Bundle / job completes without an unhandled failure (typical exit code 0).
- Engine ends in **COMPLETED** (confirmed via audit in step 2).

---

### 2. Phase 1 — Verify tables, unified view, and audit log

List objects:

```bash
databricks experimental aitools tools query \
  "SHOW TABLES IN <SOURCE_CATALOG>.<REHYDRATE_TARGET_SCHEMA>" \
  --profile <PROFILE>
```

Query the unified view:

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

- `claims_year_2020` and `claims_year_2021` exist (external tables created with `CREATE TABLE IF NOT EXISTS ... USING DELTA LOCATION` in `rehydrator.py`).
- `claims_unified` exists and the aggregate query succeeds (`CREATE OR REPLACE VIEW` path).
- Newest audit row: `status` = **COMPLETED**, `tables_created` = **2**, `archive_path` matches the passed `archive_base_path` string (`<ARCHIVE_VOL>`), `source_table` = `<SOURCE_CATALOG>.<SOURCE_SCHEMA>.claims`, `target_catalog` and `target_schema` match parameters, `years` stores the requested years (JSON string as written by the engine), `error_message` is null.

---

### 3. Phase 2 — Capture baseline row counts (before re-run)

```bash
databricks experimental aitools tools query \
  "SELECT YEAR(event_date) AS yr, COUNT(*) AS cnt
   FROM <SOURCE_CATALOG>.<REHYDRATE_TARGET_SCHEMA>.claims_unified
   GROUP BY 1 ORDER BY 1" \
  --profile <PROFILE>
```

**Expect:** Save the full result set (yr, cnt pairs) to the results file for comparison after the re-run.

---

### 4. Phase 2 — Re-run rehydration (identical parameters)

```bash
databricks bundle run caresource_rehydrate -t <TARGET> --profile <PROFILE> \
  --params config_table="<CONFIG_TABLE>",archive_base_path="<ARCHIVE_VOL>",source_table="<SOURCE_CATALOG>.<SOURCE_SCHEMA>.claims",target_catalog="<SOURCE_CATALOG>",target_schema="<REHYDRATE_TARGET_SCHEMA>",years="2020,2021"
```

**Expect:**

- No failure from `CREATE TABLE IF NOT EXISTS ... USING DELTA LOCATION` (idempotent no-op when external tables already exist).
- `CREATE OR REPLACE VIEW` runs again and refreshes the unified view without error.
- Job completes successfully.

---

### 5. Phase 2 — Verify idempotency and second audit entry

Repeat the unified view aggregation:

```bash
databricks experimental aitools tools query \
  "SELECT YEAR(event_date) AS yr, COUNT(*) AS cnt
   FROM <SOURCE_CATALOG>.<REHYDRATE_TARGET_SCHEMA>.claims_unified
   GROUP BY 1 ORDER BY 1" \
  --profile <PROFILE>
```

Audit tail for this target:

```bash
databricks experimental aitools tools query \
  "SELECT archive_path, source_table, target_catalog, target_schema, years, tables_created, status, created_at
   FROM <REHYDRATION_AUDIT_TABLE>
   WHERE target_catalog = '<SOURCE_CATALOG>' AND target_schema = '<REHYDRATE_TARGET_SCHEMA>'
   ORDER BY created_at DESC
   LIMIT 5" \
  --profile <PROFILE>
```

(After substituting placeholders, ensure `target_catalog` / `target_schema` literals match your workspace values.)

**Expect:**

- Row counts match step **3** exactly (same yr/cnt pairs).
- A **second** **COMPLETED** row appears after the first; both show `tables_created` = **2** for this two-year run.

---

### 6. Phase 3 — TABLE occupies unified view name (setup)

Drop the target schema and recreate it empty, then pre-create a **base table** named `claims_unified` so `CREATE OR REPLACE VIEW ...` collides:

```bash
databricks experimental aitools tools query \
  "DROP SCHEMA IF EXISTS <SOURCE_CATALOG>.<REHYDRATE_TARGET_SCHEMA> CASCADE" \
  --profile <PROFILE>
```

```bash
databricks experimental aitools tools query \
  "CREATE SCHEMA IF NOT EXISTS <SOURCE_CATALOG>.<REHYDRATE_TARGET_SCHEMA>" \
  --profile <PROFILE>
```

```bash
databricks experimental aitools tools query \
  "CREATE TABLE <SOURCE_CATALOG>.<REHYDRATE_TARGET_SCHEMA>.claims_unified (id INT)" \
  --profile <PROFILE>
```

**Expect:** `claims_unified` is a **table**, not a view.

---

### 7. Phase 3 — Run rehydration (expect unified view failure)

```bash
databricks bundle run caresource_rehydrate -t <TARGET> --profile <PROFILE> \
  --params config_table="<CONFIG_TABLE>",archive_base_path="<ARCHIVE_VOL>",source_table="<SOURCE_CATALOG>.<SOURCE_SCHEMA>.claims",target_catalog="<SOURCE_CATALOG>",target_schema="<REHYDRATE_TARGET_SCHEMA>",years="2020,2021"
```

**Expect:**

- Run surfaces a failure (notebook error / non-zero outcome) when `CREATE OR REPLACE VIEW` cannot claim the name `claims_unified` because a table already occupies it (`reason=view_create_failed` in engine terms).

---

### 8. Phase 3 — Verify FAILED audit and error text

```bash
databricks experimental aitools tools query \
  "SELECT archive_path, source_table, target_catalog, target_schema, years, tables_created, status, error_message, created_at
   FROM <REHYDRATION_AUDIT_TABLE>
   ORDER BY created_at DESC
   LIMIT 5" \
  --profile <PROFILE>
```

**Expect:**

- Newest row: `status` = **FAILED**.
- `error_message` documents the unified-view failure and/or a **name conflict** involving `claims_unified` (capture the verbatim Spark/Databricks text in results).
- `tables_created` reflects how many external tables were registered before the view step failed (often **2** if both `CREATE TABLE IF NOT EXISTS` calls succeeded).

---

### 9. Phase 4 — VIEW occupies external table name (setup)

Reset the target schema and pre-create a **view** named `claims_year_2020`:

```bash
databricks experimental aitools tools query \
  "DROP SCHEMA IF EXISTS <SOURCE_CATALOG>.<REHYDRATE_TARGET_SCHEMA> CASCADE" \
  --profile <PROFILE>
```

```bash
databricks experimental aitools tools query \
  "CREATE SCHEMA IF NOT EXISTS <SOURCE_CATALOG>.<REHYDRATE_TARGET_SCHEMA>" \
  --profile <PROFILE>
```

```bash
databricks experimental aitools tools query \
  "CREATE VIEW <SOURCE_CATALOG>.<REHYDRATE_TARGET_SCHEMA>.claims_year_2020 AS SELECT 1 AS dummy" \
  --profile <PROFILE>
```

**Expect:** `claims_year_2020` exists as a **view** before rehydration.

---

### 10. Phase 4 — Run rehydration and observe `CREATE TABLE IF NOT EXISTS` behavior

```bash
databricks bundle run caresource_rehydrate -t <TARGET> --profile <PROFILE> \
  --params config_table="<CONFIG_TABLE>",archive_base_path="<ARCHIVE_VOL>",source_table="<SOURCE_CATALOG>.<SOURCE_SCHEMA>.claims",target_catalog="<SOURCE_CATALOG>",target_schema="<REHYDRATE_TARGET_SCHEMA>",years="2020,2021"
```

**Expect:**

- **Document actual behavior** in the results file: whether `CREATE TABLE IF NOT EXISTS <schema>.claims_year_2020 USING DELTA LOCATION '...'` **succeeds silently**, **fails** with an error, or **raises** — include full message, job outcome, and final `status` (**COMPLETED**, **PARTIAL_COMPLETED**, or **FAILED**). Do not assume; Spark may reject creating a table when a view holds the name.

---

### 11. Phase 4 — Verify post-state and audit

```bash
databricks experimental aitools tools query \
  "SHOW TABLES IN <SOURCE_CATALOG>.<REHYDRATE_TARGET_SCHEMA>" \
  --profile <PROFILE>
```

```bash
databricks experimental aitools tools query \
  "DESCRIBE TABLE EXTENDED <SOURCE_CATALOG>.<REHYDRATE_TARGET_SCHEMA>.claims_year_2020" \
  --profile <PROFILE>
```

```bash
databricks experimental aitools tools query \
  "SELECT archive_path, source_table, target_catalog, target_schema, years, tables_created, status, error_message, created_at
   FROM <REHYDRATION_AUDIT_TABLE>
   ORDER BY created_at DESC
   LIMIT 3" \
  --profile <PROFILE>
```

**Expect:**

- Results show whether `claims_year_2020` stayed a view, was replaced by an external table, or another outcome consistent with step 10.
- Latest audit row matches the observed `status` and any `error_message`.

---

### 12. Cleanup

```bash
databricks experimental aitools tools query \
  "DROP SCHEMA IF EXISTS <SOURCE_CATALOG>.<REHYDRATE_TARGET_SCHEMA> CASCADE" \
  --profile <PROFILE>
```

**Expect:** Target schema dropped; no leftover objects from this test in `<REHYDRATE_TARGET_SCHEMA>`.

---

## Reference (engine SQL)

From `rehydrator.py`: each year uses `CREATE TABLE IF NOT EXISTS {fq_table} USING DELTA LOCATION '{loc_path}'`; the unified view uses `CREATE OR REPLACE VIEW {view_name} AS { UNION ALL ... }`. Name collisions surface as SQL errors on the corresponding statement.
