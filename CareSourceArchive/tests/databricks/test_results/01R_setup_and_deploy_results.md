# 01 — Setup & Deploy Results

## Run — 2026-04-27 22:28 CDT

**TL;DR:** Bundle validate + deploy + 3 jobs (setup, seed, generate_test_data) all PASS on `dev-serverless` after the post-fix + post-cleanup branch lands. Source row counts match (claims=5000, members=3000, providers=1000). Phase-5 integration sweep gate cleared.

**Branch:** `feat/delta_config_build_v12_archive_refactor` (commit `a089564` — fix + cleanup commits 8ed36b9 → be97efc + progress report)
**Profile / Target:** `fe-sandbox-manocha` / `dev-serverless` (test case substitutions: `--profile DEFAULT` → `--profile fe-sandbox-manocha`; `-t dev` → `-t dev-serverless`; `sandeep_manocha` → `dev2_archive`)
**Workspace:** https://fe-sandbox-manocha.cloud.databricks.com
**Audit schema:** `dev2_archive.metadata`
**Source schema:** `dev2_archive.source_data_samples`

### Steps

| # | Step | Result | Run URL |
|---|------|--------|---------|
| 1a | `databricks bundle validate -t dev-serverless --profile fe-sandbox-manocha` | PASS — `Validation OK!` | — |
| 1b | `databricks bundle deploy -t dev-serverless --profile fe-sandbox-manocha` | PASS — `Deployment complete!` (~8s) | — |
| 2 | `bundle run setup_config_tables` | PASS — `TERMINATED SUCCESS` (49s) | https://fe-sandbox-manocha.cloud.databricks.com/?o=7474658872313088#job/1101405823713360/run/842978451851861 |
| 3 | `bundle run seed_config` | PASS — `TERMINATED SUCCESS` (33s) | https://fe-sandbox-manocha.cloud.databricks.com/?o=7474658872313088#job/158427896062706/run/447400913606860 |
| 4 | `bundle run generate_test_data --params catalog="dev2_archive",schema="source_data_samples"` | PASS — `TERMINATED SUCCESS` (61s) | https://fe-sandbox-manocha.cloud.databricks.com/?o=7474658872313088#job/857878464319321/run/336523316298708 |
| 5 | Source row counts | PASS — `claims=5000, members=3000, providers=1000` (matches test-case expected `~5,000 / ~3,000 / ~1,000`) | — |

### What Happened

Clean phase-5 entry. The deployed bundle ships the post-fix + post-cleanup `src/archiver.py` (now with `ArchiveBase`) + new `src/delete_job.py` + tightened docstrings. No bundle-validation issues from the new module. Generate-test-data step caps wall time at ~1m on serverless.

### Next Steps

Proceed to 02T (scanner first run).

---

## Run — 2026-04-27 08:13 CDT

**TL;DR:** Full setup + deploy on `dev-serverless` passed cleanly. Bundle deployed in 10s, 3 jobs run (setup ~62s, seed + generate_test_data in parallel ~85s wall). Schema_templates seeded with the 5-pattern `[event_date, start_time, query_date, start_date, effective_date]` set (intentional, per user). All source counts/date ranges match expected. All steps PASS.

**Branch:** `feat/delta_config_build_v10_test_cases` (commit `74f10cb`)
**Profile:** `fe-sandbox-manocha`
**Workspace:** https://fe-sandbox-manocha.cloud.databricks.com
**Bundle target:** `dev-serverless`
**Config catalog/schema:** `dev2_archive.metadata`
**Source schema:** `dev2_archive.source_data_samples`
**Run-as SP:** `44edd08d-b71a-4e29-a01b-4881be31a144` (`caresource-archive-dev`)

**Notes on test case:**
1. `01T_setup_and_deploy.md` Step 3 lists `databricks bundle run setup_config_tables` but the prose says "run the seed_config notebook." Treated as a known typo (same interpretation as the 2026-04-24 run) — executed `databricks bundle run seed_config` instead. `seed_config`'s notebook does `INSERT INTO`, so running `setup_config_tables` twice would either no-op the second time or fail; the two are not interchangeable.
2. **Watermark patterns deviate from the test's expected output (intentional).** `01T` expects `watermark_column_patterns = ['event_date', 'start_time', 'query_date']` (3 patterns from the original `notebooks/seed_config.py`). Actual seeded value is `['event_date', 'start_time', 'query_date', 'start_date', 'effective_date']` (5 patterns). This is because `notebooks/seed_config.py` carries an uncommitted edit that widens the array to support `members.start_date` and `providers.effective_date`. Per user direction, the edit is intentional and is being kept; the test's expected list is stale and should eventually be updated to match.

### Step 1 — Validate & deploy bundle — **PASS**

```bash
databricks bundle validate -t dev-serverless --profile fe-sandbox-manocha   # Validation OK!
databricks bundle deploy -t dev-serverless --profile fe-sandbox-manocha     # Deployment complete!
```

Validate ~1s, deploy ~10s.

### Step 2 — Run `setup_config_tables` — **PASS**

```bash
databricks bundle run setup_config_tables -t dev-serverless --profile fe-sandbox-manocha
```

Run URL: https://fe-sandbox-manocha.cloud.databricks.com/?o=7474658872313088#job/1101405823713360/run/760420696321318
TERMINATED SUCCESS in ~62s. Created 6 expected Delta tables in `dev2_archive.metadata`: `global_settings`, `schema_templates`, `table_configs`, `table_configs_staging`, `archive_audit_log`, `scanner_log`. (Plus `rehydration_audit_log`, which is created by the rehydrate job's first run — not by `setup_config_tables`.)

### Step 3 — Run `seed_config` — **PASS** (with documented deviation on patterns)

```bash
databricks bundle run seed_config -t dev-serverless --profile fe-sandbox-manocha
```

Run URL: https://fe-sandbox-manocha.cloud.databricks.com/?o=7474658872313088#job/158427896062706/run/584371585516061
TERMINATED SUCCESS in ~44s. Ran in parallel with Step 4.

`schema_templates` row written:
| schema_id | source_catalog | source_schema | watermark_column_patterns | default_retention_years | archive_base_path | DAA | is_active |
|---|---|---|---|---|---|---|---|
| `dev2_archive__source_data_samples` | `dev2_archive` | `source_data_samples` | `[event_date, start_time, query_date, start_date, effective_date]` | 0 | `/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples` | false | true |

`global_settings` = 1 row. `default_retention_years = 0` (testing default — all years eligible).

### Step 4 — Run `generate_test_data` — **PASS**

```bash
databricks bundle run generate_test_data -t dev-serverless --profile fe-sandbox-manocha
```

Run URL: https://fe-sandbox-manocha.cloud.databricks.com/?o=7474658872313088#job/857878464319321/run/692847108967617
TERMINATED SUCCESS in ~85s. Ran in parallel with Step 3.

### Step 5 — Verify tables exist — **PASS**

| Table | Rows | Watermark column | Date range | Distinct years |
|---|---|---|---|---|
| `dev2_archive.source_data_samples.claims` | 5000 | event_date | 2018-01-01 → 2025-12-31 | 8 |
| `dev2_archive.source_data_samples.members` | 3000 | start_date | 2019-01-01 → 2025-12-30 | 7 |
| `dev2_archive.source_data_samples.providers` | 1000 | effective_date | 2020-01-01 → 2025-12-29 | 6 |

All match expected.

| Audit table | Rows |
|---|---|
| `global_settings` | 1 |
| `schema_templates` | 1 |
| `table_configs` | 0 (populated by scanner, not seed) |
| `archive_audit_log` | 0 |
| `scanner_log` | 0 |

### What Happened

Standard re-setup after `00T` cleanup. Bundle redeployed against `dev-serverless`, jobs created/refreshed, config seeded with intentional 5-pattern watermark array, test data regenerated. Step 3 + Step 4 ran in parallel since they touch disjoint catalogs (metadata vs source). All five expected source counts/date ranges matched first try.

### Next Steps

- Run `caresource-archive-scanner` to populate `table_configs` (3 active tables expected).
- Then proceed to whichever test the user wants next (e.g. `02T`/`03T`, the archive end-to-end tests, or 30T re-run).
- Long-term: update `01T_setup_and_deploy.md`'s expected watermark pattern list to the 5-pattern set so the test self-documents the current code.

---

## Run — 2026-04-24 09:16 CDT

**TL;DR:** Full setup + deploy on `dev-serverless` passed cleanly. Bundle deployed in 13s, 3 jobs run (setup ~50s, seed + generate_test_data in parallel ~84s). All expected rows present: global_settings=1, schema_templates=1 with patterns `[event_date, start_time, query_date]`, claims=5000, members=3000, providers=1000.

**Branch:** `feat/delta_config_build_v9_del_data_phase2` (commit `d838774`)
**Profile:** `fe-sandbox-manocha`
**Workspace:** https://fe-sandbox-manocha.cloud.databricks.com
**Bundle target:** `dev-serverless`
**Config catalog/schema:** `dev2_archive.metadata`
**Source schema:** `dev2_archive.source_data_samples`
**Run-as SP:** `44edd08d-b71a-4e29-a01b-4881be31a144` (`caresource-archive-dev`)
**Note on test case:** Steps 2 and 3 in `01T_setup_and_deploy.md` both list `databricks bundle run setup_config_tables` — step 3 appears to be a documentation typo (the accompanying prose says "Then run the `seed_config` notebook"). This run interpreted it as: setup_config_tables once, then the `seed_config` bundle job. `seed_config`'s notebook does `INSERT INTO`, so running it **before** `setup_config_tables` would fail; the two are not interchangeable.

**Optimization:** `seed_config` and `generate_test_data` are independent of each other, so they were launched in parallel after `setup_config_tables` finished — saving ~50 s of wall time versus running them serially.

### Step 1 — Validate & deploy bundle: **PASS**

```
databricks bundle validate -t dev-serverless --profile fe-sandbox-manocha
databricks bundle deploy  -t dev-serverless --profile fe-sandbox-manocha
```

- Validation: `Validation OK!` (~2s)
- Deploy: `Deployment complete!` (~13s)
- Workspace path: `/Workspace/Users/sandeep.manocha@databricks.com/.bundle/caresource-archive/dev-serverless`

### Step 2 — Run `setup_config_tables` (create config Delta tables): **PASS**

- Run URL: https://fe-sandbox-manocha.cloud.databricks.com/?o=7474658872313088#job/1101405823713360/run/967831267879246
- Duration: ~50s (RUNNING → TERMINATED SUCCESS)
- Created 7 tables in `dev2_archive.metadata`: `global_settings`, `schema_templates`, `table_configs`, `table_configs_staging`, `archive_audit_log`, `scanner_log`, `rehydration_audit_log`.

### Step 3 — Seed dev config (`seed_config` job): **PASS**

- Run URL: https://fe-sandbox-manocha.cloud.databricks.com/?o=7474658872313088#job/158427896062706/run/65294509216050
- Duration: ~50s
- Post-state:
  - `global_settings` = 1 row
  - `schema_templates` = 1 row with `schema_id='dev2_archive__source_data_samples'`, `watermark_column_patterns=['event_date','start_time','query_date']`, `archive_base_path='/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples'`
  - All values match expected.

### Step 4 — Run `generate_test_data`: **PASS**

- Run URL: https://fe-sandbox-manocha.cloud.databricks.com/?o=7474658872313088#job/857878464319321/run/97094641152588
- Duration: ~84s (ran in parallel with step 3)
- Params defaulted from `dev-serverless` target variables: `catalog=dev2_archive`, `schema=source_data_samples`.

### Step 5 — Verify sample tables exist: **PASS**

| Table | Expected (test doc) | Actual |
|---|---|---|
| `claims` | ~5,000 (watermark `event_date`, 2018–2025) | 5000 ✓ |
| `members` | ~3,000 (watermark `start_date`, 2019–2025) | 3000 ✓ |
| `providers` | ~1,000 (watermark `effective_date`, 2020–2025) | 1000 ✓ |

## What Happened

Bundle validated and deployed successfully. Config tables were created by `setup_config_tables`, then `seed_config` and `generate_test_data` were launched in parallel to minimize wall time — both terminated SUCCESS. Verification queries returned exact expected row counts for all three sample tables. No bundle-sync race this run (contrast to the 2026-04-20 run's notebook+src sync race — files were evidently in place by the time `setup_config_tables` started). Total Databricks wall time for tests 01 setup: ~2.5 min.

## Next Steps

- Fix the step 3 typo in `01T_setup_and_deploy.md`: the command should be `databricks bundle run seed_config` (not a second `setup_config_tables`). Document the dependency: `seed_config` must run **after** `setup_config_tables` because its notebook issues `INSERT INTO` with no `CREATE TABLE`.
- Document the step 3 + step 4 parallelization opportunity in the test doc to reduce wall time.
- Proceed to `02T_scanner_first_run`.

---

## Run — 2026-04-20 04:37 UTC

**TL;DR:** Deploy on `-t dev-serverless` succeeded, but `setup_config_tables` failed because the `dev2_archive` catalog storage root (`s3://manocha-ext-s3-332745928618-emjjan/dev_archive`) has no Unity Catalog external location / storage credential granting S3 access. All subsequent steps SKIPPED (same catalog, same infra blocker). Note: the test case file specifies `-t dev --profile DEFAULT`; this run used `-t dev-serverless --profile fe-sandbox-manocha` per user instruction.

**Branch:** `feat/delta_config_build_v6_dab`
**Profile:** `fe-sandbox-manocha`
**Workspace:** https://fe-sandbox-manocha.cloud.databricks.com
**Bundle Target:** `dev-serverless`
**Config catalog/schema:** `dev2_archive.metadata` (per bundle target variables)
**Source schema (test data):** `dev2_archive.source_data_samples`
**Run-as SP:** `44edd08d-b71a-4e29-a01b-4881be31a144`

---

### Step 1 — Validate & Deploy Bundle — **PASS**

| Step | Result |
|------|--------|
| `databricks bundle validate -t dev-serverless --profile fe-sandbox-manocha` | **PASS** — 1 warning only |
| `databricks bundle deploy -t dev-serverless --profile fe-sandbox-manocha` | **PASS** — "Deployment complete!" |

Deployed to: `/Workspace/Users/sandeep.manocha@databricks.com/.bundle/caresource-archive/dev-serverless`

Validate warning (informational, not blocking):

```
Warning: workspace folder has permissions not configured in bundle
- level: CAN_RUN, service_principal_name: 44edd08d-b71a-4e29-a01b-4881be31a144
```

The SP has `CAN_RUN` on the bundle folder but the bundle definition does not declare it. Consider adding the SP to `targets.dev-serverless.permissions` or removing the folder-level grant.

---

### Step 2 — Run Setup Job (create config tables) — **FAIL**

| Field | Value |
|-------|-------|
| Command | `databricks bundle run setup_config_tables -t dev-serverless --profile fe-sandbox-manocha` |
| Status | **FAIL** — `INTERNAL_ERROR` / `RUN_EXECUTION_ERROR` |
| Duration | ~58 sec |
| Job ID | `1101405823713360` |
| Run ID | `422035637055420` |
| Task that failed | `create_tables` (attempt 1 after 1 retry) |
| Run URL | https://fe-sandbox-manocha.cloud.databricks.com/?o=7474658872313088#job/1101405823713360/run/422035637055420 |

**Root error (from task run output):**

```
[RequestId=5d8f2dc1-f1a5-42c6-9370-10dc9169e284 ErrorClass=EXTERNAL_LOCATION_DOES_NOT_EXIST.RESOURCE_DOES_NOT_EXIST]
External Location 's3://manocha-ext-s3-332745928618-emjjan/dev_archive/__unitystorage/catalogs/a653cc45-8355-49bd-8211-76691648a706/tables/49c3e57d-6494-495c-88d0-43d3ffc5070a' does not exist.
```

**Diagnosis:**

- `databricks catalogs get dev2_archive` → `storage_root: s3://manocha-ext-s3-332745928618-emjjan/dev_archive`, `owner: sandeep.manocha@databricks.com`, `isolation_mode: OPEN`.
- `databricks external-locations list` → **no external location covers** the `s3://manocha-ext-s3-332745928618-emjjan/` prefix.
- When the `create_tables` task tries to create a Delta table under the catalog's managed path, UC attempts to vend temporary S3 credentials for that table's sub-path, cannot find a matching external location, and fails.
- `_catalog_schema_used.md` (committed today) documents this exact storage root but no corresponding external-location / storage-credential setup step is referenced anywhere in the repo.
- Stack trace origin: `ManagedCatalogClientImpl.getTableCredentials` → `TempCredCache.getInternal` → `SAM.setFileSystemConfigs`.

**Expected tables NOT created in `dev2_archive.metadata`:** `global_settings`, `schema_templates`, `table_configs`, `table_configs_staging`, `archive_audit_log`, `scanner_log`.

---

### Step 3 — Seed Dev Config — **SKIP**

Skipped because Step 2 failed and the target tables do not exist. Re-running `setup_config_tables` alone will not change the outcome while the UC external location is missing. Also note: the test case file asks this step to run `setup_config_tables` again (duplicate of Step 2) and then run `seed_config` manually — but the bundle defines a first-class `seed_config` job (see `docs/runbooks/dab-commands.md` §3), which would be the correct command on this branch.

---

### Step 4 — Generate Test Data — **SKIP**

Skipped — writes into `dev2_archive.source_data_samples`, same catalog / same storage root, so the identical `EXTERNAL_LOCATION_DOES_NOT_EXIST` error is expected.

---

### Step 5 — Verify Tables Exist — **SKIP**

Skipped — no source or config tables were created.

---

## What Happened

1. Bundle validated and deployed cleanly to `dev-serverless` on `fe-sandbox-manocha`. Six jobs are present and up-to-date in the workspace: `caresource-archive-setup`, `caresource-archive-scanner`, `caresource-archive-run`, `caresource-archive-rehydrate`, `caresource-archive-seed-config`, `caresource-archive-generate-test-data`.
2. First run of `setup_config_tables` failed inside Spark with a Unity Catalog credential-vending error. The `create_tables` task attempted to materialize `dev2_archive.metadata.global_settings` (the first of the six config tables) and UC rejected the temporary-credential request because no external location covers the catalog's S3 storage root. The task retried once and failed again with the same error.
3. Because every downstream notebook writes into the same `dev2_archive` catalog (either `metadata` or `source_data_samples`), running Steps 3–5 would only reproduce the same failure, so they were skipped.
4. The test case file is out of date relative to the current bundle: it calls the profile `DEFAULT`, target `dev`, catalog `sandeep_manocha.caresource_audit`, and schema `sandeep_manocha.source_data_samples`. Today's bundle uses target-scoped variables (`config_catalog=dev2_archive`, `config_schema=metadata`, `source_schema=source_data_samples`) and the `dev-serverless` target is marked default. The runbook (`docs/runbooks/dab-commands.md`) already reflects the current commands.
5. Separately, `databricks bundle validate` emitted a warning that the SP `44edd08d-b71a-…` has `CAN_RUN` on the bundle workspace folder but is not declared in bundle permissions. Informational only.

## Next Steps

1. **Unblocker — set up Unity Catalog access to the catalog's S3 root.** Either:
   - Create a storage credential + external location in the `fe-sandbox-manocha` workspace that covers `s3://manocha-ext-s3-332745928618-emjjan/dev_archive/`, then grant `CREATE EXTERNAL TABLE` / `READ FILES` / `WRITE FILES` to the SP `44edd08d-b71a-4e29-a01b-4881be31a144` and to `sandeep.manocha@databricks.com`; **or**
   - Recreate `dev2_archive` as a managed (workspace-default-storage) catalog so credentials come from the workspace's default UC storage instead of an unmanaged external S3 bucket.
   - Verify by running `databricks external-locations list` and confirming a row with URL prefix `s3://manocha-ext-s3-332745928618-emjjan/` exists, then retry `databricks bundle run setup_config_tables -t dev-serverless --profile fe-sandbox-manocha`.
2. **Update the test case file** `tests/databricks/test_cases/01T_setup_and_deploy.md` to match the current bundle:
   - Change target from `-t dev` to `-t dev-serverless` (or parameterize).
   - Change profile reference from `DEFAULT` to the per-runner profile (the runbook uses `fe-sandbox-manocha`).
   - Replace `sandeep_manocha.caresource_audit` / `sandeep_manocha.source_data_samples` with `dev2_archive.metadata` / `dev2_archive.source_data_samples`.
   - Replace the manual "run seed_config notebook" step with `databricks bundle run seed_config -t dev-serverless --profile <profile>`.
   - Remove the duplicate `setup_config_tables` invocation in Step 3.
   - Drop the `--params catalog=…,schema=…` override on `generate_test_data` in Step 4 (bundle injects these via target vars; re-add only if the runner wants a non-default target).
3. **After re-running Step 2 successfully, re-run Steps 3–5 from this test in order.** Step 5's verification SQL should target `dev2_archive.source_data_samples` (not `sandeep_manocha.source_data_samples`).
4. **Optional cleanup** for the validate warning: either add the SP to `targets.dev-serverless.permissions` in `databricks.yml` (level `CAN_RUN`), or remove the folder-level `CAN_RUN` grant from the bundle workspace folder. This is not blocking.

---

## Run — 2026-04-07 17:09 UTC

**Test Date:** 2026-04-07
**Test Time:** 17:09 – 17:14 UTC (12:09 – 12:14 CDT)
**Branch:** `feat/delta_config_build_v3_code_reduce`
**Profile:** DEFAULT
**Workspace:** https://e2-demo-field-eng.cloud.databricks.com
**Bundle Target:** dev
**Config:** `sandeep_manocha.caresource_audit`
**Source:** `sandeep_manocha.caresource_data_samples`

---

## Step 1 — Validate & Deploy Bundle

| Step | Result |
|------|--------|
| `bundle validate -t dev` | **PASS** — "Validation OK!" |
| `bundle deploy -t dev` | **PASS** — files uploaded, deployment complete |

Deployed to: `/Workspace/Users/sandeep.manocha@databricks.com/.bundle/caresource-archive/dev`

---

## Step 2 — Run Setup Job (create config tables)

| Field | Value |
|-------|-------|
| Status | **PASS** — TERMINATED SUCCESS |
| Duration | ~34 sec |
| Run URL | https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/469387292954169/run/208210053085527 |

Tables created in `sandeep_manocha.caresource_audit`:

| Table | Exists | Initial Rows |
|-------|--------|-------------|
| global_settings | Yes | 0 |
| schema_templates | Yes | 0 |
| table_configs | Yes | 0 |
| table_configs_staging | Yes | 0 |
| archive_audit_log | Yes | 0 |
| scanner_log | Yes | 0 |

---

## Step 3 — Seed Dev Config

| Field | Value |
|-------|-------|
| Status | **PASS** — TERMINATED SUCCESS |
| Duration | ~47 sec |
| Run URL | https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/478479478935865/run/274316501926284 |

| Verification | Expected | Actual | Result |
|-------------|----------|--------|--------|
| global_settings rows | 1 | 1 | **PASS** |
| schema_templates rows | 1 | 1 | **PASS** |
| watermark_column_patterns | `['event_date', 'start_time', 'query_date']` | `["event_date","start_time","query_date"]` | **PASS** |

---

## Step 4 — Generate Test Data

| Field | Value |
|-------|-------|
| Status | **PASS** — TERMINATED SUCCESS |
| Duration | ~82 sec |
| Run URL | https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/537766256105108/run/791890457613117 |

---

## Step 5 — Verify Tables Exist

| Table | Expected Rows | Actual Rows | Watermark Column | Expected Years | Actual Years | Result |
|-------|--------------|-------------|-----------------|---------------|-------------|--------|
| claims | ~5,000 | 5,000 | event_date | 2018–2025 | 2018–2025 | **PASS** |
| members | ~3,000 | 3,000 | start_date | 2019–2025 | 2019–2025 | **PASS** |
| providers | ~1,000 | 1,000 | effective_date | 2020–2025 | 2020–2025 | **PASS** |

---

## Overall Result: PASS

All 5 steps completed successfully. The bundle deploys cleanly, config tables are created and seeded correctly, and test data matches all expected row counts and year ranges.
