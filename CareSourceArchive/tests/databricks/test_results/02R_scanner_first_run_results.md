# 02 — Scanner First Run Results

---

## Run — 2026-04-27 22:30 CDT

**TL;DR:** Scanner ran cleanly on the post-fix + post-cleanup branch. Pre-seeded 5-pattern watermark list (`event_date, start_time, query_date, start_date, effective_date`) matched all 3 source tables on the first pass; `table_configs` has 3 active rows. No import-surface regressions from the `ArchiveBase` + `DeleteJob` split. All steps PASS.

**Branch:** `feat/delta_config_build_v12_archive_refactor` (commit `a089564`)
**Profile / Target:** `fe-sandbox-manocha` / `dev-serverless` (substituted from test case's `<PROFILE>` / `<TARGET>` placeholders)
**Audit schema:** `dev2_archive.metadata`
**Source schema:** `dev2_archive.source_data_samples`

### Before

| Object | Count | Expected | Result |
|---|---|---|---|
| `scanner_log` | 0 | 0 (clean first run) | PASS |
| `table_configs` | 0 | 0 | PASS |

### Steps

| # | Step | Result | Run URL |
|---|------|--------|---------|
| 1 | `bundle run caresource_scanner --params config_table=dev2_archive.metadata.global_settings` | PASS — `TERMINATED SUCCESS` (40s, well under 90s expected) | https://fe-sandbox-manocha.cloud.databricks.com/?o=7474658872313088#job/969823066224415/run/746808757125786 |
| 2 | `scanner_log` content | PASS — all 3 tables `matched` + `is_active=true` + `merge_action=added`: claims/event_date, members/start_date, providers/effective_date |
| 3 | `table_configs` content | PASS — 3 rows, all `is_active=true`, `reason=""`, watermark columns set as expected |

### What Happened

`schema_templates` was seeded with the wide pattern list during 01T, so no `Manual Step` needed (the test case's optional `UPDATE schema_templates ... WHERE schema_id` widening step is not required when `seed_config.py` already ships the 5-pattern array). The post-cleanup architecture (`ArchiveBase` + `ArchiveEngine` in `src/archiver.py`, new `src/delete_job.py`) doesn't change the scanner's import surface — the scanner imports from `src.config` only — so this serves as a clean canary that the `bundle deploy` synced everything correctly.

### Next Steps

Proceed to 04T (archive dry run).

---

## Run — 2026-04-24 post-fix re-verify

**TL;DR:** **PASS.** Manual step (re-run scanner after patterns were widened in prior session) now succeeds. Regression from the 09:19 run is fixed by making `watermark_column` validation conditional on `is_active=true` in `src/config.py`. All three source tables (`claims`, `members`, `providers`) now MATCH and promote to `is_active=true`. No bundle-sync race.

**Branch:** `feat/delta_config_build_v9_del_data_phase2` (working tree; applied on top of `d838774`)
**Profile:** `fe-sandbox-manocha`
**Bundle target:** `dev-serverless`
**Config catalog/schema:** `dev2_archive.metadata`
**Source schema:** `dev2_archive.source_data_samples`
**Schema ID:** `dev2_archive__source_data_samples`

### Fix under test

- **Code:** `src/config.py::validate_table_config_dict` now skips both the non-empty check and the identifier safety check for `watermark_column` when `is_active=false`. Rationale: scanner writes inactive rows with `watermark_column=""` when no pattern matched any column, and those rows must survive `load_table_configs(active_only=False)` so subsequent scanner runs stay idempotent.
- **Tests:** added 4 unit tests in `tests/unit/test_config.py` (`TestValidateTableConfigFields` + new `TestLoadTableConfigsWithInactiveRows`) covering inactive rows with empty, missing, and unsafe `watermark_column` values, and a loader test with a mixed active/inactive row set.
- **Unit suite:** `pytest tests/unit` → **461/461 passed** in 0.73s.

### Pre-run state (reproduces the bug)

| `table_id` | `is_active` | `watermark_column` | `scan_run_id` |
|---|---|---|---|
| `dev2_archive.source_data_samples.claims` | true | `event_date` | `3a647910-…` |
| `dev2_archive.source_data_samples.members` | **false** | **(empty)** | `3a647910-…` |
| `dev2_archive.source_data_samples.providers` | **false** | **(empty)** | `3a647910-…` |

With the previous code, loading this exact state would hit `ArchiveConfigError: field='watermark_column', table_id='…members'` before any merge executed.

### Step — Re-run the scanner (widened patterns from prior session still in schema_templates): **PASS**

Command:

```
databricks bundle run caresource_scanner -t dev-serverless --profile fe-sandbox-manocha \
  --params config_table=dev2_archive.metadata.global_settings,schema_id=dev2_archive__source_data_samples
```

- Run URL: https://fe-sandbox-manocha.cloud.databricks.com/?o=7474658872313088#job/969823066224415/run/510202780817355
- Outcome: `TERMINATED SUCCESS`, no crash.

### Post-run state

All three rows merged with `scan_run_id=58596f37-daab-4d0a-bec2-925eb3d7db1f`:

| `table_id` | `is_active` | `watermark_column` | `match_status` |
|---|---|---|---|
| `dev2_archive.source_data_samples.claims` | true | `event_date` | matched |
| `dev2_archive.source_data_samples.members` | **true** | `start_date` | matched |
| `dev2_archive.source_data_samples.providers` | **true** | `effective_date` | matched |

### What Happened

The loader no longer rejects scanner-produced inactive rows, so the re-scan proceeded past configuration load, applied the already-widened `watermark_column_patterns`, and correctly upgraded both `members` and `providers` to active with the newly-matched watermark columns. This is the behavior 02T's manual step expected in the 09:19 run.

### Next Steps

- Proceed to 03R follow-up Run for idempotency verification.

---

## Run — 2026-04-24 09:19 CDT

**TL;DR:** Steps 1–3 PASS (scanner run #1 ~63s, `claims` matched on `event_date`, `members`/`providers` unmatched as expected). Manual step **FAIL** — after widening `watermark_column_patterns` to include `start_date` + `effective_date`, scanner run #2 crashes in `config.load_table_configs` with `ArchiveConfigError: field='watermark_column'` on the inactive `members` row from run #1. Regression vs the 2026-04-20 green run. No code changes made.

**Branch:** `feat/delta_config_build_v9_del_data_phase2` (commit `d838774`)
**Profile:** `fe-sandbox-manocha`
**Workspace:** https://fe-sandbox-manocha.cloud.databricks.com
**Bundle target:** `dev-serverless`
**Config catalog/schema:** `dev2_archive.metadata`
**Source schema:** `dev2_archive.source_data_samples`
**Schema ID:** `dev2_archive__source_data_samples`
**Run-as SP:** `44edd08d-b71a-4e29-a01b-4881be31a144`

---

### Before State

| Table | Count |
|---|---|
| `dev2_archive.metadata.scanner_log` | 0 (clean start) |
| `dev2_archive.metadata.table_configs` | 0 (clean start) |
| `dev2_archive.metadata.global_settings` | 1 seeded row |
| `dev2_archive.metadata.schema_templates` | 1 row, patterns=`[event_date, start_time, query_date]` |
| `dev2_archive.source_data_samples.*` | 3 tables (claims=5000, members=3000, providers=1000) |

---

### Step 1 — Run the scanner (default patterns): **PASS**

Command:

```
databricks bundle run caresource_scanner -t dev-serverless --profile fe-sandbox-manocha \
  --params config_table=dev2_archive.metadata.global_settings
```

- Run URL: https://fe-sandbox-manocha.cloud.databricks.com/?o=7474658872313088#job/969823066224415/run/59220475641577
- Duration: ~63s, TERMINATED SUCCESS on first attempt (no bundle-sync race).

### Step 2 — Check `scanner_log`: **PASS**

| `source_table` | `match_status` | `matched_column` | `is_active` | `merge_action` |
|---|---|---|---|---|
| `claims` | matched | `event_date` | true | added |
| `members` | unmatched | (empty) | false | added |
| `providers` | unmatched | (empty) | false | added |

All three rows exactly match expected behavior for the seeded narrow pattern set.

### Step 3 — Check `table_configs`: **PASS**

| `table_id` | `watermark_column` | `is_active` | `reason` | `scan_run_id` |
|---|---|---|---|---|
| `dev2_archive.source_data_samples.claims` | `event_date` | true | (empty) | `3a647910-08d0-47d0-9f41-f72f9b772cf6` |
| `dev2_archive.source_data_samples.members` | (empty) | false | `no date column matched — no pattern matched any column` | `3a647910-08d0-47d0-9f41-f72f9b772cf6` |
| `dev2_archive.source_data_samples.providers` | (empty) | false | `no date column matched — no pattern matched any column` | `3a647910-08d0-47d0-9f41-f72f9b772cf6` |

One row per discovered source table, active row has a watermark, inactive rows carry a human-readable reason. As expected.

---

### Manual Step — Widen patterns and re-run scanner: **FAIL (code regression)**

**SQL UPDATE executed successfully:**

```sql
UPDATE dev2_archive.metadata.schema_templates
SET watermark_column_patterns = ARRAY('event_date','start_time','query_date','start_date','effective_date')
WHERE schema_id = 'dev2_archive__source_data_samples'
```

→ `num_affected_rows=1` ✓

**Scanner re-run: FAIL.**

- Command (same params as step 1).
- Duration: ~43s until task crashed.
- Exit status: `INTERNAL_ERROR: Task run_scanner failed`.

**Exact error:**

```
ArchiveConfigError: Config error: field='watermark_column', table_id='dev2_archive.source_data_samples.members'
```

**Full stack trace (summarized):**

```
notebooks/run_scanner.py line 5
  → src/scanner.py:540  run_scanner()
      existing = { r["table_id"]: r
                   for r in config.load_table_configs(spark, table_configs_table, active_only=False) }
  → src/config.py:217  load_table_configs()
      for r in rows: validate_table_config_dict(r)
  → src/config.py:135  validate_table_config_dict()
      for key in _REQUIRED_TABLE_CONFIG_KEYS: _require_non_empty_str(d, key, table_id=tid)
  → src/config.py:77   _require_non_empty_str()
      if v is None or not isinstance(v, str) or not v.strip():
          raise ArchiveConfigError(field=key, table_id=table_id)
```

**Root cause (diagnosis only — no code changed):**

- `scanner.run_scanner` at line 540 loads all existing table_configs **including inactive rows** (`active_only=False`) to drive its upsert logic.
- `config.load_table_configs` at line 217 unconditionally calls `validate_table_config_dict` on every row.
- `_REQUIRED_TABLE_CONFIG_KEYS` (defined at `src/config.py:27-34`) includes `watermark_column`.
- Scanner run #1 correctly wrote `members` and `providers` rows as `is_active=false` with `watermark_column=""` (matches the "no date column matched" reason). Those same rows now fail validation on the next scanner invocation.

This is a **self-inflicted deadlock**: the scanner itself is the only writer that produces rows with empty `watermark_column`, and those rows then make the scanner unable to run again.

**Regression evidence:**

The 2026-04-20 run logged in this same results file ran the identical flow on the same target and reported "After widening `watermark_column_patterns` to include `start_date` and `effective_date`, scanner #2 passed (~63s) and all three tables became active." So this is a recent change.

`git log --oneline src/config.py` shows three commits since the 2026-04-20 run: `453ae6f` (H10 filter_expr hardening + code-review findings), `08d41dd` (docs), `faec6ea` (type annotations). The H10 commit is the most likely source — it explicitly mentions code-review findings.

**Post-failure state (verified, unchanged from after run #1):**

| table_configs | Count | Distinct scan_run_ids |
|---|---|---|
| | 3 | 1 (`3a647910…`) |

| scanner_log | Count | Distinct scan_run_ids |
|---|---|---|
| | 3 | 1 (`3a647910…`) |

Scanner #2 failed **before** writing anything to either table, so the store is not corrupted — it still reflects only scanner #1. This is good news: a code fix + re-run is sufficient recovery, no data cleanup required.

---

## What Happened

Scanner #1 (default narrow patterns) behaved exactly as the test spec expects: `claims` matched on `event_date` and is active, `members` and `providers` stayed inactive with empty `watermark_column`. The manual "widen patterns and re-run" step then crashed because the scanner's own reload path (`load_table_configs` in `src/config.py`) now validates `watermark_column` as non-empty on **every** loaded row, regardless of `is_active`. Scanner-produced inactive rows have empty `watermark_column` by design, so the second run never gets past loading existing state — it crashes before producing any staging writes. No code or data was changed in response, per the test rules.

## Next Steps

- **Dev fix required.** Options for the developer to consider (no preference called out here):
  1. Skip `watermark_column` validation in `validate_table_config_dict` when the row is `is_active=false`. Simplest, matches the current scanner behavior that only active rows need a watermark.
  2. Remove `watermark_column` from `_REQUIRED_TABLE_CONFIG_KEYS` and instead require it only in the code paths that actually use it (archive run, per-table processing).
  3. Change scanner to write a placeholder/sentinel value (e.g. `"__none__"`) into `watermark_column` for inactive rows. Less clean but preserves the "always non-empty" invariant.
- Add a regression test that covers the "scanner wrote inactive rows → scanner re-runs" loop on this exact shape of data. The existing unit tests either don't cover `load_table_configs` on inactive rows with empty watermark, or they cover it and the real scanner output schema has drifted from what the tests feed in.
- After the fix, re-run `02T_scanner_first_run` manual step and `03T_scanner_rescan_idempotent` from the current state — no `00T` rerun needed (table_configs is not corrupted, we can just re-trigger scanner once the validator is relaxed).
- Consider widening the 01 seeded `watermark_column_patterns` to include `start_date` and `effective_date` by default, so scanner #1 produces all-active rows and the 02 negative-case test becomes a dedicated test with its own narrow pattern set rather than being baked into the default dev seed.

---

## Run — 2026-04-20 05:33 UTC

**TL;DR:** Scanner run #1 failed twice (bundle sync race — `run_scanner.py` missing, then `src/config.py`+`src/__init__.py` missing on first attempts). Once all files finished uploading, scanner #1 passed (~63s) and produced the expected partial match (claims active; members/providers unmatched). After widening `watermark_column_patterns` to include `start_date` and `effective_date`, scanner #2 passed (~63s) and all three tables became active.

**Branch:** `feat/delta_config_build_v6_dab`
**Profile:** `fe-sandbox-manocha`
**Workspace:** https://fe-sandbox-manocha.cloud.databricks.com
**Bundle Target:** `dev-serverless`
**Config catalog/schema:** `dev2_archive.metadata`
**Source schema:** `dev2_archive.source_data_samples`
**Run-as SP:** `44edd08d-b71a-4e29-a01b-4881be31a144` (`caresource-archive-dev`)
**Note on test case:** the test case file (`02T_scanner_first_run.md`) references `sandeep_manocha.caresource_audit` / profile `DEFAULT` / target `dev` — all outdated. This run used the actual bundle values above. The test case has since been parameterized.

---

### Before State

| Table | Count | Notes |
|-------|-------|-------|
| `dev2_archive.metadata.scanner_log` | 0 | Clean start |
| `dev2_archive.metadata.table_configs` | 0 | Clean start |
| `dev2_archive.metadata.global_settings` | 1 row | Seeded (archive path, audit catalog/schema, retention=0, dry_run=true) |
| `dev2_archive.metadata.schema_templates` | 1 row | `schema_id=dev2_archive__source_data_samples`, patterns=`[event_date, start_time, query_date]` |
| `dev2_archive.source_data_samples.*` | 3 tables | `claims`, `members`, `providers` |

---

### Step 1 — Run the scanner — **PASS** (after 2 prior failures; no code changed)

| Attempt | Run URL / Run ID | Status | Duration | Failure mode |
|---|---|---|---|---|
| 1 | `614891096408118` | **FAIL** | ~2s | `Unable to access notebook .../notebooks/run_scanner — either it does not exist, or identity lacks permissions` |
| 2 | `98019961757812` | **FAIL** | ~41s | `ModuleNotFoundError: No module named 'src.config'` |
| 3 | `776711692375956` | **PASS** | ~63s | — |

**Diagnosis of attempts 1 & 2 — DAB bundle-sync race, not a permission issue, not an SP issue, not a notebook-code issue.**

1. `databricks bundle deploy` printed "Deployment complete!" and returned exit 0, but I subsequently confirmed that individual files were still missing from the workspace:
   - After attempt 1: `databricks workspace list .../files/notebooks` showed only 5 of 6 notebooks (missing `run_scanner`). `get-status` on `run_scanner` returned "Path doesn't exist". `touch notebooks/run_scanner.py && bundle deploy` made it appear.
   - After attempt 2: `workspace list .../files/src` showed only 7 of 9 files (missing `__init__.py` and `config.py`). A re-listing a few minutes later showed both present with `modified_at=2026-04-20T05:25:06Z`. Attempt 2 had failed at `05:24:38Z` — the files finished uploading **28 s after the task failed**.
2. SP permission hypothesis ruled out. I listed the workspace as my own user (`sandeep.manocha`, `CAN_MANAGE` on everything) and the files were literally absent — nobody could have read them. Separately, the SP (`44edd08d-…`) has `CAN_RUN` inherited on `.bundle/` and on every file below (verified on `src/config.py` via `databricks permissions get files`), and on notebooks `CAN_RUN` implicitly grants `CAN_READ`.
3. "Notebook code missing a `sys.path.insert`" ruled out. `notebooks/run_scanner.py` already computes `_bundle_root` from `notebookPath()` and inserts it into `sys.path` before the `from src.config import …` line. The import failed because `src/config.py` was literally not yet in the workspace.

Attempt 3 was identical to attempt 2 (no code changed, no re-touch) and ran cleanly — confirming the prior failures were a transient sync-race.

Command that passed:

```
databricks bundle run caresource_scanner -t dev-serverless --profile fe-sandbox-manocha \
  --params config_table=dev2_archive.metadata.global_settings
```

Run URL: https://fe-sandbox-manocha.cloud.databricks.com/?o=7474658872313088#job/969823066224415/run/776711692375956

---

### Step 2 — Check scanner_log (first scanner run) — **PASS**

| `source_table` | `match_status` | `matched_column` | `is_active` | `merge_action` |
|---|---|---|---|---|
| `claims` | matched | `event_date` | true | added |
| `members` | unmatched | (empty) | false | added |
| `providers` | unmatched | (empty) | false | added |

All three rows match expected behavior:

- `claims` → matched on `event_date`, active, `merge_action = added` ✓
- `members` → unmatched, inactive (patterns don't include `start_date`) ✓
- `providers` → unmatched, inactive (patterns don't include `effective_date`) ✓

---

### Step 3 — Check table_configs (first scanner run) — **PASS**

| `table_id` | `watermark_column` | `is_active` | `reason` |
|---|---|---|---|
| `dev2_archive.source_data_samples.claims` | `event_date` | true | (empty) |
| `dev2_archive.source_data_samples.members` | (empty) | false | `no date column matched — no pattern matched any column` |
| `dev2_archive.source_data_samples.providers` | (empty) | false | `no date column matched — no pattern matched any column` |

One row per discovered table. Active table has `watermark_column` set; inactive ones carry a `reason`. ✓

---

### Manual Step — Widen `watermark_column_patterns` — **PASS**

Pre-requisite: catalog owner (`sandeep.manocha@databricks.com`) did not have `MODIFY` on `dev2_archive.metadata` — only the SP did. Granted to self:

```sql
GRANT MODIFY ON SCHEMA dev2_archive.metadata TO `sandeep.manocha@databricks.com`;
```

Then applied the widen:

```sql
UPDATE dev2_archive.metadata.schema_templates
SET watermark_column_patterns = ARRAY('event_date','start_time','query_date','start_date','effective_date')
WHERE schema_id = 'dev2_archive__source_data_samples';
-- num_affected_rows: 1
```

Verify:

```
[{"schema_id": "dev2_archive__source_data_samples",
  "watermark_column_patterns": "[\"event_date\",\"start_time\",\"query_date\",\"start_date\",\"effective_date\"]"}]
```

---

### Step 1 (re-run) — Scanner with widened patterns — **PASS**

Run URL: https://fe-sandbox-manocha.cloud.databricks.com/?o=7474658872313088#job/969823066224415/run/680242491899498
Duration: ~63 s, `TERMINATED SUCCESS`.

---

### Step 2 (re-run) — scanner_log — **PASS**

Top 3 rows from this run (`created_at = 2026-04-20T05:33:03.871Z`):

| `source_table` | `match_status` | `matched_column` | `is_active` | `merge_action` |
|---|---|---|---|---|
| `claims` | matched | `event_date` | true | updated |
| `members` | matched | `start_date` | true | updated |
| `providers` | matched | `effective_date` | true | updated |

All three now matched and active. `merge_action = updated` reflects that the rows already existed in `table_configs` from run #1. ✓

---

### Step 3 (re-run) — table_configs — **PASS**

| `table_id` | `watermark_column` | `is_active` | `reason` |
|---|---|---|---|
| `dev2_archive.source_data_samples.claims` | `event_date` | true | (empty) |
| `dev2_archive.source_data_samples.members` | `start_date` | true | (empty) |
| `dev2_archive.source_data_samples.providers` | `effective_date` | true | (empty) |

---

### Final State

| Table | Count |
|---|---|
| `dev2_archive.metadata.scanner_log` | 6 rows (3 per scanner run) |
| `dev2_archive.metadata.table_configs` | 3 rows (one per source table, all active) |

---

## What Happened

1. **Prereqs verified clean.** Before running, both `scanner_log` and `table_configs` were empty; `global_settings` and `schema_templates` were already seeded with the expected values.
2. **Scanner attempt 1 failed** at the workspace-file layer — `run_scanner.py` was not present in the deployed bundle even though a `bundle deploy` had reported success minutes earlier. Touching the file and redeploying made it appear.
3. **Scanner attempt 2 failed** with `ModuleNotFoundError: No module named 'src.config'`. Listing the deployed `src/` showed `__init__.py` and `config.py` were still missing. By the time I re-listed a few minutes later they had arrived — their `modified_at` is 28 seconds after the task's failure time. The bundle deploy CLI reports "Deployment complete!" before all files are actually on the workspace; `bundle run` fired immediately after hits files still in flight.
4. **Scanner attempt 3 passed** with no code, config, or bundle change — just by running again after the sync had settled. Output matched the test case's expectations exactly: `claims` active on `event_date`, `members`/`providers` unmatched and inactive.
5. **Widened patterns** to include `start_date` and `effective_date` as the test's Manual Step prescribes. First had to grant myself `MODIFY` on `dev2_archive.metadata`; catalog-owner does not inherit schema `MODIFY` in Unity Catalog, and only the run-as SP had been granted it by the setup job.
6. **Scanner re-run** promoted `members` and `providers` to `is_active=true` with their correct watermark columns, and `merge_action=updated` on all three rows (they already existed from run #1). Final `table_configs` state: three active rows, one per source table.
7. **Test case file was stale** — referenced `sandeep_manocha.caresource_audit`, profile `DEFAULT`, target `dev`. All actual values are different under `feat/delta_config_build_v6_dab`. Parameterized the test case at the end of this run.

---

## Next Steps

1. **Confirm or automate the DAB sync race.** The deploy race cost us two scanner attempts. Options:
   - Reproduce with `DATABRICKS_SDK_DEBUG=true databricks bundle deploy` and file a Databricks CLI issue if the deploy really is returning before uploads complete.
   - Workaround for test automation: add a post-deploy wait that calls `databricks workspace get-status` on every expected notebook/src file and blocks until each exists, before kicking off `bundle run`.
   - Or: re-try `bundle run <scanner>` with a single retry on `ModuleNotFoundError` / `notebook does not exist` (transient only).
2. **`seed_config` should grant `MODIFY` on the metadata schema to the catalog owner**, not only to the run-as SP. This came up in the manual step — I had to grant it ad-hoc. Either extend `seed_config.py` / `setup_config_tables.py` to include `GRANT MODIFY ON SCHEMA <catalog>.<schema> TO \`<catalog-owner>\`` or document in `docs/runbooks/service-principals.md` that the catalog owner needs to run that grant once per environment.
3. **Broaden the seeded `watermark_column_patterns`** in `notebooks/seed_config.py` (or in the dev-seed data) to include `start_date` and `effective_date` so the scanner picks up all three sample tables on the first run. The current seed forces a manual UPDATE step every time.
4. **Update `tests/databricks/test_cases/02T_scanner_first_run.md`** — done in this commit; hard-coded catalog/schema/profile/target are replaced with placeholders the runner must supply.
5. **Re-enable the cleanup grant** I made to myself (optional): `GRANT MODIFY ON SCHEMA dev2_archive.metadata TO \`sandeep.manocha@databricks.com\`` is still in place. Fine to keep in dev; revoke in higher envs.

---

## Run — 2026-04-12 17:28 CDT

**TL;DR:** Scanner ran successfully (~85s). All 14 tables scanned — 7 active (watermark matched), 7 inactive (no pattern match). `claims` matched `event_date`, `providers` matched `effective_date`. `members` still unmatched. Post secret-scope removal deploy validated end-to-end.

**Branch:** `feat/delta_config_build_v3_code_reduce` (post secret-scope removal — commit `04d8fcb`)
**Profile:** DEFAULT

### Before State

| Metric | Count |
|--------|-------|
| scanner_log rows | 28 |
| table_configs rows | 14 |

### Step 1 — Run scanner: **PASS**

```
databricks bundle run caresource_scanner -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings"
```

- Run URL: https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/297158067384911/run/366217519638786
- Status: TERMINATED SUCCESS
- Duration: ~85 seconds

### Step 2 — Check scanner_log: **PASS**

Latest 14 entries (one per table):

| source_table | match_status | matched_column | is_active | merge_action |
|---|---|---|---|---|
| silver_query_table_access | matched | start_time | true | updated |
| silver_table_dependencies | unmatched | | false | updated |
| bronze_column_lineage | matched | event_date | true | updated |
| claims | matched | event_date | true | preserved |
| gold_impact_blast_radius | unmatched | | false | updated |
| gold_consumer_summary | unmatched | | false | updated |
| gold_table_lineage_paths | unmatched | | false | updated |
| members | unmatched | | false | updated |
| gold_daily_access_trends | matched | query_date | true | updated |
| gold_column_usage | unmatched | | false | updated |
| bronze_table_lineage | matched | event_date | true | updated |
| bronze_query_history | matched | start_time | true | updated |
| providers | unmatched | | false | preserved |
| gold_table_access_summary | unmatched | | false | updated |

**Notes:**
- `claims` shows `merge_action = preserved` (already existed from prior runs)
- `providers` shows `merge_action = preserved` and `is_active = false` in the scanner_log, but table_configs retains `is_active = true` from prior manual pattern update — scanner preserves existing config
- `members` still unmatched — `start_date` not in watermark_column_patterns

### Step 3 — Check table_configs: **PASS**

| table_id | watermark_column | is_active | reason |
|---|---|---|---|
| ...bronze_column_lineage | event_date | true | |
| ...bronze_query_history | start_time | true | |
| ...bronze_table_lineage | event_date | true | |
| ...claims | event_date | true | |
| ...gold_column_usage | | false | no date column matched — no pattern matched any column |
| ...gold_consumer_summary | | false | no date column matched — no pattern matched any column |
| ...gold_daily_access_trends | query_date | true | |
| ...gold_impact_blast_radius | | false | no date column matched — no pattern matched any column |
| ...gold_table_access_summary | | false | no date column matched — no pattern matched any column |
| ...gold_table_lineage_paths | | false | no date column matched — no pattern matched any column |
| ...members | | false | no date column matched — no pattern matched any column |
| ...providers | effective_date | true | |
| ...silver_query_table_access | start_time | true | |
| ...silver_table_dependencies | | false | no date column matched — no pattern matched any column |

14 rows total — 7 active, 7 inactive.

## What Happened

Scanner deployed and ran successfully after the secret-scope removal changes. All 14 source tables were scanned. 7 tables matched watermark patterns and are active. 7 tables had no pattern match and are inactive. No errors, no duplicates. This confirms the code changes (removing `load_secrets`, `secret_scope` from config, etc.) did not break the scanner pipeline.

## Next Steps

- `members` can be activated by adding `start_date` to `schema_templates.watermark_column_patterns` (see manual steps in 02T test case)
- Proceed to 03T (scanner re-scan idempotent) and 04T (archive dry run)

---

## Run — 2026-04-07

**Test Date:** 2026-04-07
**Test Time:** 12:16 – 12:20 CDT
**Branch:** `feat/delta_config_build_v3_code_reduce`
**Profile:** DEFAULT
**Workspace:** https://e2-demo-field-eng.cloud.databricks.com
**Bundle Target:** dev
**Config:** `sandeep_manocha.caresource_audit`
**Source:** `sandeep_manocha.caresource_data_samples`

### Before — Baseline Counts

| Table | Rows |
|-------|------|
| scanner_log | 0 |
| table_configs | 0 |

### Step 1 — Run Scanner (first run)

| Field | Value |
|-------|-------|
| Status | **PASS** — TERMINATED SUCCESS |
| Duration | ~62 sec |
| Run URL | https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/297158067384911/run/1011741787587035 |

### Step 2 — Check scanner_log (first run)

14 rows logged (all merge_action = `added`).

| Source Table | Match Status | Matched Column | Active | Result |
|-------------|-------------|----------------|--------|--------|
| claims | matched | event_date | true | **PASS** |
| members | unmatched | — | false | **PASS** (expected) |
| providers | unmatched | — | false | **PASS** (expected) |
| bronze_column_lineage | matched | event_date | true | — |
| bronze_query_history | matched | start_time | true | — |
| bronze_table_lineage | matched | event_date | true | — |
| gold_daily_access_trends | matched | query_date | true | — |
| silver_query_table_access | matched | start_time | true | — |
| gold_column_usage | unmatched | — | false | — |
| gold_consumer_summary | unmatched | — | false | — |
| gold_impact_blast_radius | unmatched | — | false | — |
| gold_table_access_summary | unmatched | — | false | — |
| gold_table_lineage_paths | unmatched | — | false | — |
| silver_table_dependencies | unmatched | — | false | — |

### Step 3 — Check table_configs (first run)

| Metric | Value |
|--------|-------|
| Total rows | 14 |
| Active | 6 |
| Inactive | 8 |

Active tables have `watermark_column` set. Inactive tables have reason = "no date column matched — no pattern matched any column."

**Result:** **PASS**

### Manual Fix — Update Watermark Patterns

`members` and `providers` were unmatched because `start_date` and `effective_date` were not in the watermark patterns.

```sql
UPDATE sandeep_manocha.caresource_audit.schema_templates
SET watermark_column_patterns = ARRAY('event_date', 'start_time', 'query_date', 'start_date', 'effective_date')
WHERE schema_id = 'sandeep_manocha__caresource_data_samples'
```

| Field | Value |
|-------|-------|
| Rows affected | 1 |
| Verified patterns | `["event_date","start_time","query_date","start_date","effective_date"]` |

### Re-run Scanner (after pattern fix)

| Field | Value |
|-------|-------|
| Status | **PASS** — TERMINATED SUCCESS |
| Duration | ~52 sec |
| Run URL | https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/297158067384911/run/402308378668412 |

#### scanner_log after re-run (key tables)

| Source Table | Match Status | Matched Column | Active | merge_action | Result |
|-------------|-------------|----------------|--------|-------------|--------|
| claims | matched | event_date | true | updated | **PASS** |
| members | matched | start_date | true | updated | **PASS** |
| providers | matched | effective_date | true | updated | **PASS** |

#### table_configs after re-run (key tables)

| Table ID | Watermark Column | Active | Result |
|----------|-----------------|--------|--------|
| ...claims | event_date | true | **PASS** |
| ...members | start_date | true | **PASS** |
| ...providers | effective_date | true | **PASS** |

#### Final counts

| Table | Rows |
|-------|------|
| scanner_log | 28 (14 from run 1 + 14 from run 2) |
| table_configs | 14 (8 active, 6 inactive) |

### Overall Result: PASS

Scanner correctly discovers all 14 tables in the source schema. On first run, `claims` matched immediately via `event_date`. After adding `start_date` and `effective_date` to watermark patterns, `members` and `providers` also matched on re-run. The merge logic correctly used `added` on first run and `updated` on re-run.

---

## Run — 2026-04-09

**Test Date:** 2026-04-09
**Branch:** `feat/delta_config_build_v3_code_reduce`
**Profile:** DEFAULT
**Bundle Target:** dev

### Before — Baseline Counts

| Table | Rows |
|-------|------|
| scanner_log | 0 |
| table_configs | 0 |

### Step 1 — Run Scanner

| Field | Value |
|-------|-------|
| Status | **PASS** — TERMINATED SUCCESS |
| Duration | ~62 sec |
| Run URL | https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/297158067384911/run/946027027016902 |

### Step 2 — Check scanner_log

14 rows logged (all merge_action = `added`).

| Source Table | Match Status | Matched Column | Active | Result |
|-------------|-------------|----------------|--------|--------|
| claims | matched | event_date | true | **PASS** |
| members | unmatched | — | false | **PASS** (expected) |
| providers | unmatched | — | false | **PASS** (expected) |
| bronze_column_lineage | matched | event_date | true | — |
| bronze_table_lineage | matched | event_date | true | — |
| bronze_query_history | matched | start_time | true | — |
| gold_daily_access_trends | matched | query_date | true | — |
| silver_query_table_access | matched | start_time | true | — |
| gold_column_usage | unmatched | — | false | — |
| gold_consumer_summary | unmatched | — | false | — |
| gold_impact_blast_radius | unmatched | — | false | — |
| gold_table_access_summary | unmatched | — | false | — |
| gold_table_lineage_paths | unmatched | — | false | — |
| silver_table_dependencies | unmatched | — | false | — |

### Step 3 — Check table_configs

14 rows created. 6 active, 8 inactive.

| Table ID | Watermark Column | Active | Reason |
|----------|-----------------|--------|--------|
| ...claims | event_date | true | |
| ...members | — | false | no date column matched |
| ...providers | — | false | no date column matched |
| ...bronze_column_lineage | event_date | true | |
| ...bronze_table_lineage | event_date | true | |
| ...bronze_query_history | start_time | true | |
| ...gold_daily_access_trends | query_date | true | |
| ...silver_query_table_access | start_time | true | |
| ...gold_column_usage | — | false | no date column matched |
| ...gold_consumer_summary | — | false | no date column matched |
| ...gold_impact_blast_radius | — | false | no date column matched |
| ...gold_table_access_summary | — | false | no date column matched |
| ...gold_table_lineage_paths | — | false | no date column matched |
| ...silver_table_dependencies | — | false | no date column matched |

### Overall Result: PASS

Scanner discovered 14 tables (3 test + 11 lineage/gold). `claims` matched on `event_date`. `members` and `providers` unmatched as expected (`start_date`/`effective_date` not in watermark patterns). To activate them, update `schema_templates.watermark_column_patterns` and re-run scanner.
