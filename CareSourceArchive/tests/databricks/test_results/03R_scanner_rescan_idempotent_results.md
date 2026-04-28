# 03 — Scanner Re-scan (Idempotent) Results

## Run — 2026-04-24 post-fix re-verify

**TL;DR:** **PASS.** With the `src/config.py::validate_table_config_dict` fix in place, the re-scan succeeded with a fresh `scan_run_id` and the same final `table_configs` rows as the prior post-fix scanner run. Idempotency confirmed.

**Branch:** `feat/delta_config_build_v9_del_data_phase2` (working tree; applied on top of `d838774`)
**Profile:** `fe-sandbox-manocha`
**Bundle target:** `dev-serverless`
**Config catalog/schema:** `dev2_archive.metadata`
**Source schema:** `dev2_archive.source_data_samples`
**Prior scanner run (for this check):** `58596f37-daab-4d0a-bec2-925eb3d7db1f` (see 02R post-fix Run)

### Step — Re-run the scanner (same params): **PASS**

Command:

```
databricks bundle run caresource_scanner -t dev-serverless --profile fe-sandbox-manocha \
  --params config_table=dev2_archive.metadata.global_settings,schema_id=dev2_archive__source_data_samples
```

- Run URL: https://fe-sandbox-manocha.cloud.databricks.com/?o=7474658872313088#job/969823066224415/run/506329152552738
- Outcome: `TERMINATED SUCCESS`.
- New `scan_run_id`: `d76a7801-f97b-4620-a4c3-c76d4b303bf3` (distinct from prior run `58596f37-…`).

### table_configs after rescan

| `table_id` | `is_active` | `watermark_column` | `scan_run_id` | `reason` |
|---|---|---|---|---|
| `dev2_archive.source_data_samples.claims` | true | `event_date` | `d76a7801-…` | (empty) |
| `dev2_archive.source_data_samples.members` | true | `start_date` | `d76a7801-…` | (empty) |
| `dev2_archive.source_data_samples.providers` | true | `effective_date` | `d76a7801-…` | (empty) |

### scanner_log — last two runs side-by-side

| `scan_run_id` | `table_id` | `match_status` | `matched_column` | `is_active` |
|---|---|---|---|---|
| `58596f37-…` | claims | matched | event_date | true |
| `58596f37-…` | members | matched | start_date | true |
| `58596f37-…` | providers | matched | effective_date | true |
| `d76a7801-…` | claims | matched | event_date | true |
| `d76a7801-…` | members | matched | start_date | true |
| `d76a7801-…` | providers | matched | effective_date | true |

Same three `table_id`s, same `matched_column`, same `is_active=true`, different `scan_run_id` → idempotent.

### What Happened

Fix in `src/config.py` (conditional `watermark_column` validation for `is_active=false` rows) unblocked both the load-existing and the merge-back paths of `run_scanner`. The second post-fix run reprocessed the same tables, produced a new scan UUID, merged updates into the same three rows without schema or semantic drift, and wrote a fresh batch of `scanner_log` entries. This is the idempotency behavior 03T was designed to verify.

### Next Steps

- Commit the fix + unit tests + updated result files on the active feature branch.
- No further action on 02T/03T for this regression.

---

## Run — 2026-04-24 09:23 CDT

**TL;DR:** **FAIL.** Scanner re-run crashes identically to `02T` manual step: `ArchiveConfigError: field='watermark_column', table_id='dev2_archive.source_data_samples.members'` at `config.load_table_configs` (src/config.py:217). Same regression, same stack trace, same pre-write failure. Idempotency cannot be verified until the validator issue is fixed.

**Branch:** `feat/delta_config_build_v9_del_data_phase2` (commit `d838774`)
**Profile:** `fe-sandbox-manocha`
**Workspace:** https://fe-sandbox-manocha.cloud.databricks.com
**Bundle target:** `dev-serverless`
**Config catalog/schema:** `dev2_archive.metadata`
**Source schema:** `dev2_archive.source_data_samples`
**Note on test case:** `03T_scanner_rescan_idempotent.md` hard-codes `sandeep_manocha.caresource_audit` + `--profile DEFAULT` + `-t dev`. Substituted with the actual `dev-serverless` target values per the bundle under test. Same substitution noted in 00R for this session.

---

### Before State (captured just before step 1)

```
SELECT COUNT(*) FROM dev2_archive.metadata.scanner_log           → 3
SELECT COUNT(*) FROM dev2_archive.metadata.table_configs         → 3
SELECT COUNT(DISTINCT scan_run_id) FROM scanner_log              → 1
```

`scan_run_id` for all three rows: `3a647910-08d0-47d0-9f41-f72f9b772cf6` (from scanner run #1 in 02T; 02T's manual-step run #2 failed pre-write and did not change state — see `02R_scanner_first_run_results.md`).

| `table_id` | `modified_by` (via scanner) | `scan_run_id` |
|---|---|---|
| `dev2_archive.source_data_samples.claims` | service-principal | `3a647910-…` |
| `dev2_archive.source_data_samples.members` | service-principal | `3a647910-…` |
| `dev2_archive.source_data_samples.providers` | service-principal | `3a647910-…` |

> ⚠️ 03T strictly depends on 02T's manual step succeeding first so that all three rows have a non-empty `watermark_column`. In this run, 02T's manual step failed (see `02R`), so 03T is effectively running against scanner #1's output — `members` and `providers` still have empty `watermark_column` and `is_active=false`.

---

### Step 1 — Re-run the scanner: **FAIL**

Command:

```
databricks bundle run caresource_scanner -t dev-serverless --profile fe-sandbox-manocha \
  --params config_table=dev2_archive.metadata.global_settings
```

- Duration: ~61s until task crashed.
- Exit status: `INTERNAL_ERROR: Task run_scanner failed`.

**Exact error (identical to 02T manual-step failure):**

```
ArchiveConfigError: Config error: field='watermark_column', table_id='dev2_archive.source_data_samples.members'
```

**Stack trace (summarized):**

```
src/scanner.py:540  run_scanner()           # load existing configs with active_only=False
src/config.py:217   load_table_configs()    # for r in rows: validate_table_config_dict(r)
src/config.py:135   validate_table_config_dict()
src/config.py:77    _require_non_empty_str('watermark_column', …)  → raises
```

### Step 2 — Verify table_configs unchanged: **SKIP (blocked by step 1)**

Since the scanner never reached the write phase, we verified the pre-write state instead:

| `table_id` | `watermark_column` | `is_active` | `scan_run_id` |
|---|---|---|---|
| `dev2_archive.source_data_samples.claims` | `event_date` | true | `3a647910-…` |
| `dev2_archive.source_data_samples.members` | (empty) | false | `3a647910-…` |
| `dev2_archive.source_data_samples.providers` | (empty) | false | `3a647910-…` |

Post-run state is byte-for-byte identical to pre-run state (still 3 rows, still 1 distinct `scan_run_id`). This *happens* to satisfy the "no duplicates, no watermark/is_active drift" clauses of the test, but only because the scanner failed before writing — not because it executed idempotently. The `scan_run_id` was **not** advanced and no `merge_action=updated` rows were produced in `scanner_log`. So the actual idempotency property the test wants to verify is **not demonstrated**.

---

### Verdict

| Test assertion | Expected | Actual | Status |
|---|---|---|---|
| Same number of rows as before | 3 | 3 | MET (by accident) |
| `scan_run_id` updated to new value | New UUID | Unchanged | **NOT MET** |
| `merge_action=updated` in `scanner_log` | 3 rows | 0 new rows | **NOT MET** |
| `watermark_column` and `is_active` unchanged | Unchanged | Unchanged | MET (by accident) |
| No duplicate `table_id` entries | No duplicates | No duplicates | MET (by accident) |

Overall: **FAIL** — the scanner cannot complete a re-run, so idempotency cannot be demonstrated.

---

## What Happened

The `03T` idempotent re-run test was blocked by the same regression that broke `02T`'s manual widen-and-rerun step. `run_scanner` begins by loading all existing rows from `table_configs` with `active_only=False` so it can compute diffs, and `config.load_table_configs` now validates `watermark_column` as non-empty for every loaded row. Scanner-produced inactive rows have empty `watermark_column` by design, so the re-run crashes on the very first row it re-reads (`members`). Nothing was written to `table_configs` or `scanner_log`. Source data and config tables remain consistent and uncorrupted — the failure is purely a read-time validation.

## Next Steps

- **Blocked on the same fix as 02T** — see `02R_scanner_first_run_results.md → Next Steps` for the three fix options.
- Once the fix ships, re-run 02T's manual step to get all three rows to `is_active=true` with non-empty `watermark_column`, and then run 03T against that state. No `00T` + `01T` replay needed; current state is recoverable.
- Separately, update `03T_scanner_rescan_idempotent.md` to use the parameterized placeholder style used in `02T` (it currently hard-codes `sandeep_manocha.caresource_audit` + `--profile DEFAULT` + `-t dev`).
- Consider adding an assertion in this test that explicitly checks `scan_run_id` **advanced** (i.e. new UUID) — the current checks "same number of rows / no duplicates / same watermark+is_active" can all pass trivially if the scanner no-ops or crashes pre-write, which is what happened here.

---

## Run — 2026-04-12 17:31 CDT

**TL;DR:** Re-scan idempotent — PASS. Row count unchanged at 14, scan_run_id updated for 12 tables, 2 manual entries preserved. No duplicates, no watermark/is_active drift.

**Branch:** `feat/delta_config_build_v3_code_reduce` (post secret-scope removal — commit `04d8fcb`)
**Profile:** DEFAULT

### Before State

| Metric | Value |
|--------|-------|
| scanner_log rows | 42 |
| table_configs rows | 14 |
| Previous scan_run_id (12 tables) | `66cc37f7-267d-4e44-846d-8c0f75b785aa` |
| Previous scan_run_id (claims, providers — manual) | `a03b2884-0cb5-4ac8-8bdb-1e1acdaf858b` |

### Step 1 — Re-run scanner: **PASS**

- Run URL: https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/297158067384911/run/77161359918706
- Status: TERMINATED SUCCESS
- Duration: ~60 seconds

### Step 2 — Verify table_configs unchanged: **PASS**

| table_id | watermark_column | is_active | scan_run_id |
|---|---|---|---|
| ...bronze_column_lineage | event_date | true | d5138b61-da92-4021-9f37-405383ca93ce |
| ...bronze_query_history | start_time | true | d5138b61-da92-4021-9f37-405383ca93ce |
| ...bronze_table_lineage | event_date | true | d5138b61-da92-4021-9f37-405383ca93ce |
| ...claims | event_date | true | a03b2884-0cb5-4ac8-8bdb-1e1acdaf858b |
| ...gold_column_usage | | false | d5138b61-da92-4021-9f37-405383ca93ce |
| ...gold_consumer_summary | | false | d5138b61-da92-4021-9f37-405383ca93ce |
| ...gold_daily_access_trends | query_date | true | d5138b61-da92-4021-9f37-405383ca93ce |
| ...gold_impact_blast_radius | | false | d5138b61-da92-4021-9f37-405383ca93ce |
| ...gold_table_access_summary | | false | d5138b61-da92-4021-9f37-405383ca93ce |
| ...gold_table_lineage_paths | | false | d5138b61-da92-4021-9f37-405383ca93ce |
| ...members | | false | d5138b61-da92-4021-9f37-405383ca93ce |
| ...providers | effective_date | true | a03b2884-0cb5-4ac8-8bdb-1e1acdaf858b |
| ...silver_query_table_access | start_time | true | d5138b61-da92-4021-9f37-405383ca93ce |
| ...silver_table_dependencies | | false | d5138b61-da92-4021-9f37-405383ca93ce |

**Verification checklist:**
- Same row count (14 before → 14 after): **PASS**
- scan_run_id updated to `d5138b61...` for 12 scanner-managed tables: **PASS**
- claims & providers retained `a03b2884...` (manual entries, merge_action = preserved): **PASS**
- merge_action in scanner_log = "updated" for 12 tables, "preserved" for 2: **PASS**
- watermark_column and is_active unchanged: **PASS**
- No duplicate table_id entries: **PASS**

## What Happened

Re-running the scanner after a full deploy produced identical table_configs — same 14 rows, same watermark columns, same active status. The scanner correctly used MERGE with "updated" action for scanner-managed tables and "preserved" for manually modified entries. Idempotency confirmed.

## Next Steps

- Proceed to 04T (archive dry run)

---

## Run — 2026-04-10 12:53 CDT

**Branch:** `feat/delta_config_build_v3_code_reduce`
**Profile:** DEFAULT
**Workspace:** https://e2-demo-field-eng.cloud.databricks.com
**Bundle Target:** dev

---

### Before — Baseline

| Metric | Value |
|--------|-------|
| scanner_log rows | 14 |
| table_configs rows | 14 |
| scan_run_id (all rows) | `502ed67e-0253-4f4b-bfbb-2bbacc0d7e7a` |

---

### Step 1 — Re-run Scanner

| Field | Value |
|-------|-------|
| Status | **PASS** — TERMINATED SUCCESS |
| Duration | ~62 sec |
| Run URL | https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/297158067384911/run/286903978633816 |

---

### Step 2 — Verify Idempotency

#### Row count unchanged

| Table | Before | After | Result |
|-------|--------|-------|--------|
| table_configs | 14 | 14 | **PASS** |

#### scan_run_id updated

| Field | Value | Result |
|-------|-------|--------|
| Old scan_run_id | `502ed67e-0253-4f4b-bfbb-2bbacc0d7e7a` | — |
| New scan_run_id | `a03b2884-0cb5-4ac8-8bdb-1e1acdaf858b` | **PASS** — changed |
| All 14 rows have new ID | Yes | **PASS** |

#### merge_action = "updated" (not "added")

All 14 scanner_log entries from this run have `merge_action = "updated"`. **PASS**

#### watermark_column and is_active unchanged

| Table | Watermark | Active | Result |
|-------|-----------|--------|--------|
| bronze_column_lineage | event_date | true | **PASS** |
| bronze_query_history | start_time | true | **PASS** |
| bronze_table_lineage | event_date | true | **PASS** |
| claims | event_date | true | **PASS** |
| gold_column_usage | — | false | **PASS** |
| gold_consumer_summary | — | false | **PASS** |
| gold_daily_access_trends | query_date | true | **PASS** |
| gold_impact_blast_radius | — | false | **PASS** |
| gold_table_access_summary | — | false | **PASS** |
| gold_table_lineage_paths | — | false | **PASS** |
| members | — | false | **PASS** |
| providers | — | false | **PASS** |
| silver_query_table_access | start_time | true | **PASS** |
| silver_table_dependencies | — | false | **PASS** |

> **Note:** `members` and `providers` now show `is_active=false` with no watermark — differs from the 2026-04-07 run where they were active. This reflects code changes between runs; within this run, values are unchanged by rescan.

#### No duplicate table_id entries

Duplicate check query returned empty — **PASS**

#### scanner_log growth

| Metric | Value |
|--------|-------|
| scanner_log before | 14 |
| scanner_log after | 28 (+14 new entries) |

---

### Overall Result: PASS

Re-scanning is fully idempotent. The scanner updated all 14 existing configs with a new `scan_run_id` without creating duplicates or changing `watermark_column` / `is_active` values. All `merge_action` values were "updated" (not "added").

---

## Run — 2026-04-07 12:23 CDT (previous)

**Test Date:** 2026-04-07
**Test Time:** 12:23 – 12:24 CDT
**Branch:** `feat/delta_config_build_v3_code_reduce`
**Profile:** DEFAULT
**Workspace:** https://e2-demo-field-eng.cloud.databricks.com
**Bundle Target:** dev

---

## Before — Baseline

| Metric | Value |
|--------|-------|
| scanner_log rows | 28 |
| table_configs rows | 14 |
| scan_run_id (all rows) | `fb097bd0-a64c-4252-92de-72a2ff4b9746` |

---

## Step 1 — Re-run Scanner

| Field | Value |
|-------|-------|
| Status | **PASS** — TERMINATED SUCCESS |
| Duration | ~52 sec |
| Run URL | https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/297158067384911/run/914988743708788 |

---

## Step 2 — Verify Idempotency

### Row count unchanged

| Table | Before | After | Result |
|-------|--------|-------|--------|
| table_configs | 14 | 14 | **PASS** |

### scan_run_id updated

| Field | Value | Result |
|-------|-------|--------|
| Old scan_run_id | `fb097bd0-a64c-4252-92de-72a2ff4b9746` | — |
| New scan_run_id | `5d56e8b8-85c3-4f51-bd47-65456f81a2c0` | **PASS** — changed |
| All 14 rows have new ID | Yes | **PASS** |

### merge_action = "updated" (not "added")

All 14 scanner_log entries from this run have `merge_action = "updated"`. **PASS**

### watermark_column and is_active unchanged

| Table | Watermark | Active | Changed? | Result |
|-------|-----------|--------|----------|--------|
| claims | event_date | true | No | **PASS** |
| members | start_date | true | No | **PASS** |
| providers | effective_date | true | No | **PASS** |
| bronze_column_lineage | event_date | true | No | **PASS** |
| bronze_query_history | start_time | true | No | **PASS** |
| bronze_table_lineage | event_date | true | No | **PASS** |
| gold_daily_access_trends | query_date | true | No | **PASS** |
| silver_query_table_access | start_time | true | No | **PASS** |
| gold_column_usage | — | false | No | **PASS** |
| gold_consumer_summary | — | false | No | **PASS** |
| gold_impact_blast_radius | — | false | No | **PASS** |
| gold_table_access_summary | — | false | No | **PASS** |
| gold_table_lineage_paths | — | false | No | **PASS** |
| silver_table_dependencies | — | false | No | **PASS** |

### No duplicate table_id entries

Duplicate check query returned empty — **PASS**

### scanner_log growth

| Metric | Value |
|--------|-------|
| scanner_log before | 28 |
| scanner_log after | 42 (+14 new entries) |

---

## Overall Result: PASS

Re-scanning is fully idempotent. The scanner updated all 14 existing configs with a new `scan_run_id` without creating duplicates or changing `watermark_column` / `is_active` values. All `merge_action` values were "updated" (not "added").
