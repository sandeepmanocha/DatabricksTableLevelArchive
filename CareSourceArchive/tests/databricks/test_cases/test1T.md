Use Databricks DEFAULT Profile
Use Databricks Plugins & CLI

Possible Prompts:
Source Catalog:sandeep_manocha
Source Schema:caresource_data_samples
Audit Schema:caresource_audit
config_table: sandeep_manocha.caresource_audit.global_settings 

**Results:** Append a new `## Run — <date> <time>` section **at the top** of `test_results/test1R_results.md` (below the H1 title). Do not overwrite previous runs.

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

## Validate & Deploy the bundle

```bash
databricks auth env --profile DEFAULT
databricks workspace list / --profile DEFAULT
databricks bundle validate -t dev --profile DEFAULT
databricks bundle deploy -t dev --profile DEFAULT
```

## During all following runs Capture before and after data from log tables

```bash
databricks experimental aitools tools query \
  "SELECT COUNT(*) AS cnt, MAX(archive_run_id) AS max_run_id, MAX(created_at) AS max_ts FROM sandeep_manocha.caresource_audit.archive_audit_log" \
  --profile DEFAULT

databricks experimental aitools tools query \
  "SELECT COUNT(*) AS cnt, MAX(scan_run_id) AS max_run_id, MAX(created_at) AS max_ts FROM sandeep_manocha.caresource_audit.scanner_log" \
  --profile DEFAULT
```

## Run The Scanner

```bash
databricks bundle run caresource_scanner -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings"
```

## Run the Archive in Dry Run Mode

```bash
databricks bundle run caresource_archive_run -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings",dry_run="true",source_catalog="sandeep_manocha",source_schema="caresource_data_samples"
```

## Run the Archive without Dry Run Mode

```bash
databricks bundle run caresource_archive_run -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings",dry_run="false",source_catalog="sandeep_manocha",source_schema="caresource_data_samples"
```
