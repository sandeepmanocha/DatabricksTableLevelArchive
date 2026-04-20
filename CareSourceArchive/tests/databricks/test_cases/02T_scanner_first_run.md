# 02 — Scanner First Run

**Goal:** Run the scanner to auto-discover tables and populate `table_configs`.

**Depends on:** `01_setup_and_deploy`
**Results:** Append a new `## Run — <date> <time>` section **at the top** of `test_results/02R_scanner_first_run_results.md` (below the H1 title). Do not overwrite previous runs.

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

---

## Inputs (ask the runner before starting)

The runner must supply these values. Source of truth: the bundle target under test in `databricks.yml` (`targets.<TARGET>.variables.{config_catalog, config_schema, source_schema}`) and the runner's CLI profile.

| Placeholder | Description | How to find it |
|---|---|---|
| `<PROFILE>` | Databricks CLI profile pointing at the target workspace | `databricks auth profiles` |
| `<TARGET>` | Bundle target (e.g. `dev-serverless`, `dev`, `qa`, `stage`, `prod`) | `databricks.yml → targets:` |
| `<CONFIG_CATALOG>` | Unity Catalog holding the config tables | `databricks.yml → targets.<TARGET>.variables.config_catalog` |
| `<CONFIG_SCHEMA>` | Schema holding the config tables | `databricks.yml → targets.<TARGET>.variables.config_schema` |
| `<SOURCE_SCHEMA>` | Schema holding source tables to scan | `databricks.yml → targets.<TARGET>.variables.source_schema` |
| `<SCHEMA_ID>` | `schema_templates` row key (normally `<CONFIG_CATALOG>__<SOURCE_SCHEMA>`) | `SELECT schema_id FROM <CONFIG_CATALOG>.<CONFIG_SCHEMA>.schema_templates` |

Confirm with the runner, then substitute throughout this test. Record the chosen values at the top of the run section in the results file.

---

## Before

```bash
databricks experimental aitools tools query \
  "SELECT COUNT(*) AS cnt FROM <CONFIG_CATALOG>.<CONFIG_SCHEMA>.scanner_log" \
  --profile <PROFILE>

databricks experimental aitools tools query \
  "SELECT COUNT(*) AS cnt FROM <CONFIG_CATALOG>.<CONFIG_SCHEMA>.table_configs" \
  --profile <PROFILE>
```

**Expect:** Both `0` for a clean first run. Non-zero counts just mean this isn't a first run — not a failure.

## Steps

### 1. Run the scanner

```bash
databricks bundle run caresource_scanner -t <TARGET> --profile <PROFILE> \
  --params config_table=<CONFIG_CATALOG>.<CONFIG_SCHEMA>.global_settings
```

**Expect:** Job succeeds within ~90 seconds.

> **Known transient:** if this fails with `Unable to access the notebook .../notebooks/run_scanner` or `ModuleNotFoundError: No module named 'src.config'` immediately after a `bundle deploy`, the workspace file-sync has not finished yet. Retry the `bundle run` after 30 s; if it still fails, re-deploy and retry. Log the occurrence in the results file as a separate FAIL attempt before the PASS — do not silently hide it.

### 2. Check `scanner_log`

```bash
databricks experimental aitools tools query \
  "SELECT source_table, match_status, matched_column, is_active, merge_action
   FROM <CONFIG_CATALOG>.<CONFIG_SCHEMA>.scanner_log
   ORDER BY created_at DESC LIMIT 20" \
  --profile <PROFILE>
```

**Expect (with the default seeded patterns `event_date, start_time, query_date`):**

- `claims` → `matched` on `event_date`, `is_active=true`, `merge_action=added`
- `members` → `unmatched`, `is_active=false` (patterns don't include `start_date`)
- `providers` → `unmatched`, `is_active=false` (patterns don't include `effective_date`)

> If `members` and `providers` already show as `matched`, the seeded patterns were widened ahead of time — skip the Manual Step below.

### 3. Check `table_configs`

```bash
databricks experimental aitools tools query \
  "SELECT table_id, watermark_column, is_active, reason
   FROM <CONFIG_CATALOG>.<CONFIG_SCHEMA>.table_configs
   ORDER BY table_id" \
  --profile <PROFILE>
```

**Expect:** One row per discovered source table. Active rows have `watermark_column` set. Inactive rows carry a human-readable `reason`.

---

## Manual Steps

### Widen `watermark_column_patterns` so all three sample tables match

Run from the workspace UI or via the CLI. The catalog owner may need `MODIFY` on the schema first (only the run-as SP has it by default after `setup_config_tables`):

```sql
-- One-time, as catalog owner / admin:
GRANT MODIFY ON SCHEMA <CONFIG_CATALOG>.<CONFIG_SCHEMA> TO `<runner-user-or-group>`;

-- Widen patterns:
UPDATE <CONFIG_CATALOG>.<CONFIG_SCHEMA>.schema_templates
SET watermark_column_patterns = ARRAY('event_date','start_time','query_date','start_date','effective_date')
WHERE schema_id = '<SCHEMA_ID>';
```

Then re-run the scanner (Step 1). After the re-run:

- `claims` → `matched` on `event_date`, active, `merge_action=updated`
- `members` → `matched` on `start_date`, active, `merge_action=updated`
- `providers` → `matched` on `effective_date`, active, `merge_action=updated`
- `table_configs` has three active rows, one per source table.
