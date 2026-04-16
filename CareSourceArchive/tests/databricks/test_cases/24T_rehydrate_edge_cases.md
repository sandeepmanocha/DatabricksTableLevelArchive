# 24 — Rehydration Edge Cases: View Toggle, Table Prefix, Single Year, Schema Reuse

**Goal:** Verify parameter-driven behavior variations — single year, unified view disabled, table prefix naming, schema reuse with existing objects, and failure when the unified view references a non-existent source table.

**Depends on:** 05_archive_live_create (archives must exist for claims)

**Results:** Append a new `## Run — <date> <time>` section **at the top** of `test_results/24R_rehydrate_edge_cases_results.md` (below the H1 title). Do not overwrite previous runs.

---

## Non-widget engine parameters (Phases 2–3)

> **`create_unified_view` and `table_prefix` are arguments to `RehydrationEngine.run(params)`, not Databricks notebook widgets.** The production notebook `notebooks/run_rehydrate.py` builds `params` from widgets only and does **not** pass these keys, so defaults apply (`create_unified_view=True`, `table_prefix=""`).
>
> **For Phases 2 and 3, the test runner must** either:
>
> - **(a)** Temporarily add optional widgets and merge them into `params` before `engine.run(params)`, or  
> - **(b)** Edit the `params` dict in a test-specific notebook cell (e.g. after the existing `params = { ... }` block, add `params["create_unified_view"] = False` or `params["table_prefix"] = "rhy_"`).
>
> Repeat this note in each Phase 2 and Phase 3 step below. After the test run, **restore** the notebook to its original state if it was modified.
>
> Reference: `src/rehydrator.py` — `table_prefix` / `create_unified_view` are read from `params` (lines 83–91); unified view SQL is built at lines 127–151.

---

## Execution order (required)

Run the numbered steps **in this order**:

1. **Step 1 — Phase 1 (single year)** establishes `<REHYDRATE_TARGET_SCHEMA>` with one year and a unified view.  
2. **Step 2 — Phase 4 (schema reuse)** must run **next**, while that schema still contains Phase 1 objects.  
3. **Steps 3–4 — Phases 2 and 3** each assume a **clean** target schema (use `DROP SCHEMA ... CASCADE` before running).  
4. **Step 5 — Phase 5** uses a clean or disposable target schema and a deliberately bad `source_table` value.

If you run steps out of order, recreate Phase 1’s schema state before Phase 4 or adjust the documented expectations.

---

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
>    - **Archive volume (claims):** Year folders for **2020** and **2021** must exist with valid Delta data (from test 05). Paths follow `{archive_base_path}/claims/year_YYYY` under `<ARCHIVE_VOL>`. This test uses those years across phases; if folders are missing, run test 05 first.
>    - **Target schema:** For a full clean run, `<SOURCE_CATALOG>.<REHYDRATE_TARGET_SCHEMA>` should **not** exist at the start of Step 1 (or run `DROP SCHEMA ... CASCADE` as in Step 1). Phases 2–3 and 5 require a clean schema before their bundle runs; Phase 4 **requires** the schema left behind by Phase 1.
>    - **Source table:** `<SOURCE_CATALOG>.<SOURCE_SCHEMA>.claims` must have data for phases where the unified view is created with a real source (Phases 1, 3, 4). Phase 5 intentionally points `source_table` at a non-existent name.
>    - **Notebook (Phases 2–3):** The runner must plan notebook changes for `create_unified_view` and `table_prefix` (see **Non-widget engine parameters** above). Bundle `--params` alone cannot set these.
>    - **Rehydration audit table:** Must exist at `<REHYDRATION_AUDIT_TABLE>` (typically `<CONFIG_TABLES_PREFIX>.rehydration_audit_log`). If missing, run `setup_config_tables` / config seeding per project runbooks.

---

## Workspace Parameters

> See [`_workspace_params.md`](./_workspace_params.md) for `<PROFILE>`, `<TARGET>`, `<CONFIG_TABLE>`, `<SOURCE_CATALOG>`, `<SOURCE_SCHEMA>`, `<ARCHIVE_VOL>`, and `<CONFIG_TABLES_PREFIX>`.
>
> **This test adds:**
>
> | Placeholder | Description |
> | --- | --- |
> | `<REHYDRATE_TARGET_SCHEMA>` | Schema where external tables and unified view are created (e.g. `caresource_rehydrated_edge`). Must be safe to drop and recreate during this test. |
> | `<REHYDRATION_AUDIT_TABLE>` | Full name of `rehydration_audit_log` — typically `<CONFIG_TABLES_PREFIX>.rehydration_audit_log`. |
>
> **Standard rehydration job widget parameters:** `config_table`, `archive_base_path` (use `<ARCHIVE_VOL>`), `source_table`, `target_catalog`, `target_schema`, `years`.

---

## Steps

### 1. Phase 1 — Single year (`years="2020"`)

Start from a clean target schema:

```bash
databricks experimental aitools tools query \
  "DROP SCHEMA IF EXISTS <SOURCE_CATALOG>.<REHYDRATE_TARGET_SCHEMA> CASCADE" \
  --profile <PROFILE>
```

Run rehydration for **exactly one** archive year:

```bash
databricks bundle run caresource_rehydrate -t <TARGET> --profile <PROFILE> \
  --params config_table="<CONFIG_TABLE>",archive_base_path="<ARCHIVE_VOL>",source_table="<SOURCE_CATALOG>.<SOURCE_SCHEMA>.claims",target_catalog="<SOURCE_CATALOG>",target_schema="<REHYDRATE_TARGET_SCHEMA>",years="2020"
```

**Expect:**

- **`tables_created` = 1** (audit row and/or engine result).
- **Unified view** logically matches: `SELECT * FROM <SOURCE_CATALOG>.<SOURCE_SCHEMA>.claims` **UNION ALL** `SELECT * FROM <SOURCE_CATALOG>.<REHYDRATE_TARGET_SCHEMA>.claims_year_2020` (order of UNION branches matches engine construction in `rehydrator.py`).
- **Status = `COMPLETED`** in the latest audit entry for this run.
- **Audit log:** Latest row in `<REHYDRATION_AUDIT_TABLE>` shows `status = COMPLETED`, `tables_created = 1`, `years` includes `2020`, `source` / `target_*` fields consistent with params.

Verify objects and counts:

```bash
databricks experimental aitools tools query \
  "SHOW TABLES IN <SOURCE_CATALOG>.<REHYDRATE_TARGET_SCHEMA>" \
  --profile <PROFILE>
```

```bash
databricks experimental aitools tools query \
  "SELECT * FROM <REHYDRATION_AUDIT_TABLE> ORDER BY created_at DESC LIMIT 3" \
  --profile <PROFILE>
```

---

### 2. Phase 4 — Same target schema with existing objects (add `2021`)

> **Do not drop** the schema from Phase 1. This step reuses `<SOURCE_CATALOG>.<REHYDRATE_TARGET_SCHEMA>` which already contains `claims_year_2020` and `claims_unified` from Step 1.

Run rehydration requesting **both** years (archive folders for 2020 and 2021 must exist):

```bash
databricks bundle run caresource_rehydrate -t <TARGET> --profile <PROFILE> \
  --params config_table="<CONFIG_TABLE>",archive_base_path="<ARCHIVE_VOL>",source_table="<SOURCE_CATALOG>.<SOURCE_SCHEMA>.claims",target_catalog="<SOURCE_CATALOG>",target_schema="<REHYDRATE_TARGET_SCHEMA>",years="2020,2021"
```

**Expect:**

- **New** external table **`claims_year_2021`** appears alongside existing **`claims_year_2020`** (existing table not dropped).
- **`claims_unified`** is recreated and includes **both** restored years in the UNION (plus source): query shows data for 2020 and 2021 from archived paths as applicable.
- **`tables_created`** equals the number of requested years that are in `available_archive_years` and processed in the loop (for `years="2020,2021"` with both folders present, expect **2** — the counter increments per year, not only when `CREATE TABLE` is a no-op on an existing table).
- **Status = `COMPLETED`** if both years are restorable; audit row consistent with run.

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
  "SELECT * FROM <REHYDRATION_AUDIT_TABLE> ORDER BY created_at DESC LIMIT 5" \
  --profile <PROFILE>
```

---

### 3. Phase 2 — View disabled (`create_unified_view=false`)

> **Notebook modification required:** Set `params["create_unified_view"] = False` before `engine.run(params)` (see **Non-widget engine parameters**). Bundle `--params` does **not** expose this. **Restore** the notebook after the test.

Reset target schema so the phase is isolated:

```bash
databricks experimental aitools tools query \
  "DROP SCHEMA IF EXISTS <SOURCE_CATALOG>.<REHYDRATE_TARGET_SCHEMA> CASCADE" \
  --profile <PROFILE>
```

After modifying the notebook, run (example with two years):

```bash
databricks bundle run caresource_rehydrate -t <TARGET> --profile <PROFILE> \
  --params config_table="<CONFIG_TABLE>",archive_base_path="<ARCHIVE_VOL>",source_table="<SOURCE_CATALOG>.<SOURCE_SCHEMA>.claims",target_catalog="<SOURCE_CATALOG>",target_schema="<REHYDRATE_TARGET_SCHEMA>",years="2020,2021"
```

**Expect:**

- External tables **`claims_year_2020`** and **`claims_year_2021`** exist.
- **`SHOW TABLES`** shows **no** unified view named `claims_unified` (no view object for the unified name — only the external tables).
- Engine return / notebook output: **`view_name`** is **`None`** (string `None` in JSON or null, depending on display).
- **Status = `COMPLETED`**; audit row matches.

```bash
databricks experimental aitools tools query \
  "SHOW TABLES IN <SOURCE_CATALOG>.<REHYDRATE_TARGET_SCHEMA>" \
  --profile <PROFILE>
```

```bash
databricks experimental aitools tools query \
  "SELECT * FROM <REHYDRATION_AUDIT_TABLE> ORDER BY created_at DESC LIMIT 3" \
  --profile <PROFILE>
```

---

### 4. Phase 3 — Table prefix (`table_prefix="rhy_"`)

> **Notebook modification required:** Set `params["table_prefix"] = "rhy_"` before `engine.run(params)` (see **Non-widget engine parameters**). Remove or override any Phase 2-only edits as needed. **Restore** the notebook after the test.

Clean schema:

```bash
databricks experimental aitools tools query \
  "DROP SCHEMA IF EXISTS <SOURCE_CATALOG>.<REHYDRATE_TARGET_SCHEMA> CASCADE" \
  --profile <PROFILE>
```

Run with prefix in notebook `params` (bundle widgets unchanged):

```bash
databricks bundle run caresource_rehydrate -t <TARGET> --profile <PROFILE> \
  --params config_table="<CONFIG_TABLE>",archive_base_path="<ARCHIVE_VOL>",source_table="<SOURCE_CATALOG>.<SOURCE_SCHEMA>.claims",target_catalog="<SOURCE_CATALOG>",target_schema="<REHYDRATE_TARGET_SCHEMA>",years="2020,2021"
```

**Expect:**

- External tables named **`rhy_claims_year_2020`** and **`rhy_claims_year_2021`**.
- Unified view named **`rhy_claims_unified`**.
- All objects queryable (no resolution errors).

```bash
databricks experimental aitools tools query \
  "SHOW TABLES IN <SOURCE_CATALOG>.<REHYDRATE_TARGET_SCHEMA>" \
  --profile <PROFILE>
```

```bash
databricks experimental aitools tools query \
  "SELECT COUNT(*) AS n FROM <SOURCE_CATALOG>.<REHYDRATE_TARGET_SCHEMA>.rhy_claims_unified" \
  --profile <PROFILE>
```

---

### 5. Phase 5 — Source table absent (simulated “dropped” source)

Point `source_table` at a **non-existent** table (name must not exist in the catalog):

```bash
databricks experimental aitools tools query \
  "DROP SCHEMA IF EXISTS <SOURCE_CATALOG>.<REHYDRATE_TARGET_SCHEMA> CASCADE" \
  --profile <PROFILE>
```

**Do not** add notebook overrides for `create_unified_view` or `table_prefix` unless intentionally testing their interaction; default is unified view **on**, prefix **empty**.

```bash
databricks bundle run caresource_rehydrate -t <TARGET> --profile <PROFILE> \
  --params config_table="<CONFIG_TABLE>",archive_base_path="<ARCHIVE_VOL>",source_table="<SOURCE_CATALOG>.<SOURCE_SCHEMA>.claims_DROPPED",target_catalog="<SOURCE_CATALOG>",target_schema="<REHYDRATE_TARGET_SCHEMA>",years="2020,2021"
```

**Expect:**

- External table creation **may succeed** (Delta `LOCATION` points at archive paths; behavior does not depend on the source table existing).
- **`CREATE OR REPLACE VIEW` fails** because the first branch is `SELECT * FROM <SOURCE_CATALOG>.<SOURCE_SCHEMA>.claims_DROPPED`, which does not exist.
- **Status = `FAILED`** in `<REHYDRATION_AUDIT_TABLE>`.
- Failure is classified with **`reason=view_create_failed`** (raised as `ArchiveOperationError` from `create_unified_view` in `rehydrator.py`).
- **`error_message`** includes the **SQL fragment** (or truncated SQL prefix) showing the bad source reference, consistent with diagnostic wrapping (`sql=...` in the exception text).

```bash
databricks experimental aitools tools query \
  "SELECT status, error_message FROM <REHYDRATION_AUDIT_TABLE> ORDER BY created_at DESC LIMIT 3" \
  --profile <PROFILE>
```

---

## Cleanup

```sql
DROP SCHEMA IF EXISTS <SOURCE_CATALOG>.<REHYDRATE_TARGET_SCHEMA> CASCADE;
```

- If **`run_rehydrate.py`** (or a copy) was edited for Phases 2–3, **revert** those changes so production params behavior remains widget-only.
- Record in the results file whether the notebook was modified and restored.

---

## Reference — engine behavior (read-only)

From `src/rehydrator.py` (prefix, view toggle, and view failure reason):

```83:151:src/rehydrator.py
            table_prefix = params.get("table_prefix", "")
            create_unified_view = params.get("create_unified_view", True)

            base_name = self._source_base_name(source_table)
            prefixed_base = f"{table_prefix}{base_name}"
            view_name = (
                f"{target_catalog}.{target_schema}.{prefixed_base}_unified"
                if create_unified_view
                else None
            )
            create_schema_if_not_exists(self._spark, target_catalog, target_schema)
            self._audit.ensure_rehydration_audit_table()

            for year in years:
                if year not in available_archive_years:
                    LOGGER.warning(
                        "archive folder missing for year %s under %s, skipping",
                        year,
                        archive_base_path,
                    )
                    skipped_years.append(year)
                    continue
                loc_path = build_archive_path(archive_base_path, base_name, year)
                ext_fq = f"{target_catalog}.{target_schema}.{prefixed_base}_year_{year}"
                self._create_external_table(source_table, year, ext_fq, loc_path)
                tables_created += 1
                created_years.append(year)

            if not created_years:
                raise ArchiveOperationError(
                    ArchiveError.diagnostic_message(
                        "FAILED",
                        "operation_failure",
                        table=source_table,
                        year="all",
                        operation="rehydrate",
                        error="no requested years restored",
                    ),
                    table=source_table,
                    year="all",
                    operation="rehydrate",
                    reason="no_years_restored",
                )

            if create_unified_view:
                select_parts = [f"SELECT * FROM {source_table}"]
                for y in created_years:
                    ext_fq = f"{target_catalog}.{target_schema}.{prefixed_base}_year_{y}"
                    select_parts.append(f"SELECT * FROM {ext_fq}")
                union_body = " UNION ALL ".join(select_parts)
                view_sql = f"CREATE OR REPLACE VIEW {view_name} AS {union_body}"
                try:
                    self._spark.sql(view_sql)
                except Exception as exc:
                    msg = ArchiveError.diagnostic_message(
                        "FAILED",
                        "operation_failure",
                        table=source_table,
                        year="all",
                        operation="create_unified_view",
                        error=f"{exc}; sql={view_sql[:500]}",
                    )
                    raise ArchiveOperationError(
                        msg,
                        table=source_table,
                        year="all",
                        operation="create_unified_view",
                        reason="view_create_failed",
                    ) from exc
```
