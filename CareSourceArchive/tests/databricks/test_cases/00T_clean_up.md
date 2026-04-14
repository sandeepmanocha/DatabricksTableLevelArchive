# 00 — Clean Up

**Goal:** Reset the environment to a clean state before running the test suite. Drops audit/config tables and removes archive files, but preserves the catalog, schemas, volume, and sample data tables.

**Profile:** All commands use `--profile DEFAULT`
**Results:** Append a new `## Run — <date> <time>` section **at the top** of `test_results/00R_clean_up_results.md` (below the H1 title). Do not overwrite previous runs.

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

## 1. Drop audit tables

```bash
databricks experimental aitools tools query \
  "DROP TABLE IF EXISTS sandeep_manocha.caresource_audit.global_settings" \
  --profile DEFAULT

databricks experimental aitools tools query \
  "DROP TABLE IF EXISTS sandeep_manocha.caresource_audit.schema_templates" \
  --profile DEFAULT

databricks experimental aitools tools query \
  "DROP TABLE IF EXISTS sandeep_manocha.caresource_audit.table_configs" \
  --profile DEFAULT

databricks experimental aitools tools query \
  "DROP TABLE IF EXISTS sandeep_manocha.caresource_audit.table_configs_staging" \
  --profile DEFAULT

databricks experimental aitools tools query \
  "DROP TABLE IF EXISTS sandeep_manocha.caresource_audit.archive_audit_log" \
  --profile DEFAULT

databricks experimental aitools tools query \
  "DROP TABLE IF EXISTS sandeep_manocha.caresource_audit.scanner_log" \
  --profile DEFAULT

databricks experimental aitools tools query \
  "DROP TABLE IF EXISTS sandeep_manocha.caresource_audit.rehydration_audit_log" \
  --profile DEFAULT
```

## 2. Drop rehydrated schema contents (if exists)

```bash
databricks experimental aitools tools query \
  "DROP SCHEMA IF EXISTS sandeep_manocha.caresource_rehydrated CASCADE" \
  --profile DEFAULT
```

Then recreate the empty schema so later tests can use it:

```bash
databricks experimental aitools tools query \
  "CREATE SCHEMA IF NOT EXISTS sandeep_manocha.caresource_rehydrated" \
  --profile DEFAULT
```

## 3. Clean archive files from volume

Remove all archive Delta folders under the volume path. The volume itself is preserved.

```bash
databricks fs rm \
  dbfs:/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/caresource_data_samples \
  --recursive --profile DEFAULT
```

## 4. Verify clean state

### Audit tables dropped

```bash
databricks experimental aitools tools query \
  "SHOW TABLES IN sandeep_manocha.caresource_audit" \
  --profile DEFAULT
```

**Expect:** Empty results (sample data tables in `caresource_data_samples` are intentionally kept).

### Archive files removed

```bash
databricks fs ls \
  dbfs:/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/ \
  --profile DEFAULT
```

**Expect:** No `caresource_data_samples` directory listed. The volume root may still show other unrelated files or be empty.

---

## Notes

- This does **not** drop the catalog (`sandeep_manocha`), schemas (`caresource_audit`, `caresource_data_samples`, `caresource_archive`), or the volume (`caresource_archive_vol`).
- Sample data tables (`claims`, `members`, `providers`) are **kept** so you can skip step 4 of `01_setup_and_deploy` on subsequent runs.
- Run `01_setup_and_deploy` after this to rebuild audit tables and config from scratch.
