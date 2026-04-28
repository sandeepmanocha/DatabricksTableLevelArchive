# 01 — Setup & Deploy

**Goal:** Deploy bundle, create config tables, seed dev config, generate test data.

**Results:** Append a new `## Run — <date> <time>` section **at the top** of `test_results/01R_setup_and_deploy_results.md` (below the H1 title). Do not overwrite previous runs.

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

## Pre-requisites

- Databricks CLI configured with DEFAULT profile
- Catalog `sandeep_manocha` exists
- Schema `sandeep_manocha.source_data_samples` exists
- External volume path exists at `/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol`

## Steps

### 1. Validate & deploy the bundle

```bash
databricks bundle validate -t dev --profile DEFAULT
databricks bundle deploy -t dev --profile DEFAULT
```

**Expect:** "Validation OK!" and files uploaded successfully.

### 2. Run the setup job (creates config Delta tables)

```bash
databricks bundle run setup_config_tables -t dev --profile DEFAULT
```

**Expect:** Job succeeds. Tables created in `sandeep_manocha.caresource_audit`:
- `global_settings`
- `schema_templates`
- `table_configs`
- `table_configs_staging`
- `archive_audit_log`
- `scanner_log`

### 3. Seed dev config

```bash
databricks bundle run seed_config -t dev --profile DEFAULT
```

**Expect:** `global_settings` has 1 row, `schema_templates` has 1 row with `watermark_column_patterns = ['event_date', 'start_time', 'query_date', 'start_date', 'effective_date']`.

### 4. Generate test data

```bash
databricks bundle run generate_test_data -t dev --profile DEFAULT \
  --params catalog="sandeep_manocha",schema="source_data_samples"
```

**Expect:** 3 tables created:

| Table | Rows | Watermark Column | Years |
|-------|------|-----------------|-------|
| claims | ~5,000 | event_date | 2018–2025 |
| members | ~3,000 | start_date | 2019–2025 |
| providers | ~1,000 | effective_date | 2020–2025 |

### 5. Verify tables exist

```bash
databricks experimental aitools tools query \
  "SELECT 'claims' AS tbl, COUNT(*) AS cnt FROM sandeep_manocha.source_data_samples.claims
   UNION ALL SELECT 'members', COUNT(*) FROM sandeep_manocha.source_data_samples.members
   UNION ALL SELECT 'providers', COUNT(*) FROM sandeep_manocha.source_data_samples.providers" \
  --profile DEFAULT
```

**Expect:** Row counts match above.

---

## Manual Steps

- If `seed_config` notebook doesn't exist as a job, run it from the workspace UI under `notebooks/seed_config.py`.
