# 36 — Folder Deleted, Then `delete_after_archive` Flipped On Results

## Run — 2026-04-27 15:34 CDT

**Branch:** `feat/delta_config_build_v10_test_cases`
**Profile:** `fe-sandbox-manocha`
**Workspace:** https://fe-sandbox-manocha.cloud.databricks.com
**Bundle Target:** `dev-serverless`
**Fix under test:** `src/archiver.py` per-action `after_watermark` branching in `_execute_archive_create_or_append` (option A from [`docs/superpowers/specs/2026-04-26-archiver-create-action-skips-source-delete-design.md`](../../../docs/superpowers/specs/2026-04-26-archiver-create-action-skips-source-delete-design.md))
**Pre-flight archive_run_id (providers/2020 baseline ARCHIVED):** `7f512d8b-7bea-47bc-bf0c-d00c3b36f79d`
**Phase 2b re-CREATE archive_run_id:** `20b79581-6cbf-4d19-9e27-030236dfdeed`

### TL;DR

**PASS.** Bundle deployed with the fix, baseline archive established, folder deleted, DAA flipped to true, archive re-run produced the `STARTED → ARCHIVED (CREATE) → ARCHIVED_AND_DELETED` triple, and crucially **source rows for providers/2020 are now 0** (vs 163 stranded in the 2026-04-26 run). The CREATE+DAA recovery path now actually deletes source rows.

---

## Pre-flight: PASS

- Audit log: 0 prior rows for providers/2020 (workspace had been reseeded after the failing run); ran a clean baseline archive (DAA=false, profile/source filter `source_table='providers'`) to seed an `ARCHIVED` row at `archive_run_id=7f512d8b-...` with `record_count=163`.
- Archive volume: post-baseline, `providers/year_2020` folder present with delta data.
- Source table: 163 rows for year 2020 matching baseline `record_count`.
- table_configs.providers: `delete_after_archive = false` (flipped to `true` in Phase 1c).
- Other tables: not touched.

---

## Phase 1 — Establish preconditions: **PASS**

### 1a — providers/2020 ARCHIVED row: PASS

```
status=ARCHIVED, archive_mode=CREATE, record_count=163,
archive_run_id=7f512d8b-7bea-47bc-bf0c-d00c3b36f79d,
created_at=2026-04-27T20:30:28.114Z
```

`pre_count = 163`.

### 1b — Folder + counts match: PASS

`archive_rows = 163`, `source_rows = 163`.

### 1c — Flip DAA=true: PASS

`UPDATE dev2_archive.metadata.table_configs SET delete_after_archive = true ...` succeeded.

---

## Phase 2 — Delete folder + Re-run: **PASS** (the bug is fixed)

### 2a — Delete folder: PASS

`databricks fs rm --recursive` succeeded; subsequent `ls` returned `no such directory`.

### 2b — Run archive (providers only): PASS

Bundle run TERMINATED SUCCESS in ~114s.
Run URL: https://fe-sandbox-manocha.cloud.databricks.com/?o=7474658872313088#job/645665657236546/run/791660335802609

### 2c — Audit log (last 5 rows): PASS

| status | archive_mode | record_count | archive_run_id |
|---|---|---|---|
| ARCHIVED_AND_DELETED | CREATE | 163 | `20b79581-...` (newest) |
| ARCHIVED | CREATE | 163 | `20b79581-...` |
| STARTED | (null) | 0 | `20b79581-...` |
| ARCHIVED | CREATE | 163 | `7f512d8b-...` (baseline) |
| STARTED | (null) | 0 | `7f512d8b-...` |

The expected 3-row sequence (STARTED → ARCHIVED CREATE → ARCHIVED_AND_DELETED) all share `archive_run_id=20b79581-...`.

### 2d — Folder back: PASS

`archive_rows = 163`.

### 2e — Source year 2020 should be empty: **PASS** (this is the bug the fix targeted)

```
source_rows = 0
```

Compared with the 2026-04-26 run (`source_rows = 163`, FAIL). With the per-action `after_watermark` mapping in place, CREATE now passes `after_watermark=None` so `_run_watermark_window` covers the full year that was just written, and `_delete_archived` issues a real source `DELETE`.

### 2f — No FAILED rows: PASS

`failed_rows = 0`.

---

## Phase 4 — Cleanup: PASS

### 4a — Reset DAA=false on providers: PASS

`UPDATE table_configs SET delete_after_archive = false ...` confirmed.

### 4b — Source data for 2020 is in archive only

Source rows for year 2020 are now in the archive only (this is the intended steady state after a successful CREATE+DAA recovery). Re-seed via `generate_test_data` if the next test needs source data for 2020.

---

## What this proves

| Behavior | Pass criteria | Result |
|---|---|---|
| Folder deleted while audit = `ARCHIVED` and DAA flipped to `true` | Detected as `MISSING + ARCHIVED + DAA=true` | **PASS** |
| Action chosen | `CREATE` (rule F) | **PASS** |
| Self-heal | Folder rebuilt; new `ARCHIVED` row with `archive_mode=CREATE` | **PASS** |
| Delete-after-archive runs in same run | Source year 2020 = 0 rows | **PASS** (was the bug; now fixed) |
| Final audit row | `ARCHIVED_AND_DELETED` with `record_count = pre_count` | **PASS** |
| No FAILED, no SKIPPED_CONCURRENT | Recovery silent and complete | **PASS** |

---

## What Happened

After deploying the option-A fix to `dev-serverless`, the audit table was empty for providers/2020 (workspace had been reseeded since the 2026-04-26 failure), so a baseline archive was first run to produce a single `ARCHIVED` row (`archive_run_id=7f512d8b-...`, `record_count=163`). DAA was flipped to true, the archive folder was deleted by hand, and the archive job was re-run for providers only. The new run logged the expected `STARTED → ARCHIVED CREATE → ARCHIVED_AND_DELETED` triple all sharing `archive_run_id=20b79581-...`. Folder is rebuilt with 163 rows, source for 2020 is now 0, and no FAILED rows. The earlier silent no-op is gone.

## Phase 3 (skipped)

Plan called for `phases 1-2e`; phase 3 (re-run = SKIP via rule D) was not executed in this re-run since the bug surfaced in phase 2e and that is the one the fix addressed. Phase 3 is unchanged by this fix and is covered by other tests' rule-D coverage.

---

## Run — 2026-04-26 15:01 CDT

**Branch:** `feat/delta_config_build_v10_test_cases`
**Profile:** `fe-sandbox-manocha`
**Workspace:** https://fe-sandbox-manocha.cloud.databricks.com
**Bundle Target:** `dev-serverless`
**Pre-flight archive_run_id (providers/2020 baseline ARCHIVED):** `4d94680d-d2fe-4272-afe7-0a362fc8773f`
**Phase 2b re-CREATE archive_run_id:** `ce67c889-06e7-4376-907d-e4a185f5ada5`
**Phase 3a SKIP archive_run_id:** `d79a6122-cb2a-49f9-9697-ee7a5e88dac6`

### TL;DR

Folder deleted, DAA flipped to true, archive re-run produced the expected `STARTED → ARCHIVED (CREATE) → ARCHIVED_AND_DELETED` audit triple, but the source `DELETE` was a silent no-op — **163 source rows for providers/2020 still remain**. The audit row claims `ARCHIVED_AND_DELETED`. **FAIL** — surfaces a real archiver bug in the `MISSING + ARCHIVED + DAA=true` branch.

---

## Pre-flight: PASS

- Audit log: most recent row for providers/2020 is `ARCHIVED` (run `4d94680d-...`, record_count=163).
- Archive volume: `claims/year_2020` not relevant; `providers/year_2020` folder present with `_delta_log/` and parquet.
- Source table: 163 rows for year 2020 (matches archive `record_count = 163`).
- table_configs.providers: `delete_after_archive = false` (will be flipped in Phase 1c).
- Retention: `default_retention_years = 0`, year 2020 eligible.
- Other tables: not touched.

---

## Phase 1 — Establish preconditions: **PASS**

### 1a — providers/2020 ARCHIVED row: PASS

```
status=ARCHIVED, record_count=163, archive_run_id=4d94680d-...,
created_at=2026-04-26T19:24:21.634Z
```

`pre_count = 163`.

### 1b — Folder + counts match: PASS

`archive_rows = 163`, `source_rows = 163` (both match `pre_count`).

### 1c — Flip DAA=true: PASS

```sql
UPDATE table_configs SET delete_after_archive = true ...
```

`delete_after_archive = true` confirmed.

---

## Phase 2 — Delete folder + Re-run: **PARTIAL — FAIL on 2e**

### 2a — Delete folder: PASS

`databricks fs rm` succeeded; subsequent `ls` returned "no such directory".

### 2b — Run archive (providers only): PASS

Bundle run TERMINATED SUCCESS in ~137s.
Run URL: https://fe-sandbox-manocha.cloud.databricks.com/?o=7474658872313088#job/645665657236546/run/1047861876927143

### 2c — Audit log (last 5 rows): PASS

| status | archive_mode | record_count | archive_run_id |
|---|---|---|---|
| ARCHIVED_AND_DELETED | CREATE | 163 | `ce67c889-...` (newest) |
| ARCHIVED | CREATE | 163 | `ce67c889-...` |
| STARTED | (null) | 0 | `ce67c889-...` |
| ARCHIVED | CREATE | 163 | `4d94680d-...` (clean-slate run) |
| STARTED | (null) | 0 | `4d94680d-...` |

The expected 3-row sequence (STARTED → ARCHIVED (CREATE) → ARCHIVED_AND_DELETED) all share `archive_run_id = ce67c889-...`.

### 2d — Folder back: PASS

Folder lists `_archive_metadata.json`, `_delta_log/`, parquet. `archive_rows = 163`.

### 2e — Source year 2020 should be empty: **FAIL**

Expected `source_rows = 0`. Actual `source_rows = 163` (no source rows were deleted).

**Diagnostics:**
- `effective_date` range in source for 2020: 2020-01-01 to 2020-12-31, 163 rows.
- `effective_date` range in archive Delta for 2020: 2020-01-01 to 2020-12-31, 163 rows.
- `DESCRIBE HISTORY dev2_archive.source_data_samples.providers` shows **no DELETE operation** by today's archive run. The most recent operation is the clean-slate generate_test_data WRITE (version 23). The older DELETE entries (versions 14-19) are from previous test 09 runs on April 24.
- The archive job did not raise an exception — `failed_rows = 0`, no FAILED audit row.
- `ARCHIVED_AND_DELETED` audit row was logged with `record_count = 163`.

**Root cause (suspected):** In `src/archiver.py` (`_execute_archive_create_or_append`), the `MISSING + ARCHIVED → CREATE + delete_after = true` path computes the run watermark window via:

```python
effective_wm = yr_action["last_watermark"]   # = prior ARCHIVED row's high watermark
run_wm_low, run_wm_high = self._run_watermark_window(
    merged, year, exclusion_clause, after_watermark=effective_wm
)
self._delete_archived(merged, year, exclusion_clause, run_wm_low, run_wm_high)
```

`_run_watermark_window` filters the freshly written archive Delta to `effective_date > effective_wm`. For a re-CREATE the previously-archived row's high watermark is `2020-12-31`, so the filter `effective_date > 2020-12-31 AND YEAR = 2020` matches zero rows → returns `(None, None)`. `_delete_archived` then short-circuits at:

```python
if run_wm_low is None or run_wm_high is None:
    return
```

…and silently no-ops. Yet the surrounding code happily logs `ARCHIVED_AND_DELETED` afterward.

**This is a real bug.** Test 34 didn't surface it (DAA=false skips the delete branch entirely). Test 09 doesn't surface it (initial CREATE runs with `last_watermark = None`, so the window is unbounded and the delete works). Only the `MISSING + ARCHIVED + DAA=true` recovery path triggers it.

**Suggested fix (do not apply per test rules):** in the CREATE branch, pass `after_watermark=None` to `_run_watermark_window`, since a CREATE means the entire archive slice was just written this run — the window should cover the full range, not "rows newer than the prior archive's high watermark".

### 2f — No FAILED rows: PASS

`failed_rows = 0`.

---

## Phase 3 — Re-run again: **PASS** (technically) — but compounds the bug

### 3a — Re-run archive: PASS

Bundle run TERMINATED SUCCESS in ~119s.
Run URL: https://fe-sandbox-manocha.cloud.databricks.com/?o=7474658872313088#job/645665657236546/run/729302892562385

### 3b — Audit log (last 3): PASS (per test expectations)

| status | archive_mode | archive_run_id |
|---|---|---|
| SKIPPED | SKIP | `d79a6122-...` (newest) |
| STARTED | (null) | `d79a6122-...` |
| ARCHIVED_AND_DELETED | CREATE | `ce67c889-...` (Phase 2) |

Newest is SKIPPED — rule D (VALID + ARCHIVED_AND_DELETED → SKIP).

**Compounding effect of the bug:** the system now believes providers/2020 is fully archived AND deleted, so subsequent runs will SKIP. The 163 stranded source rows will never get archived or deleted by the normal pipeline. Manual remediation (re-archive after fixing the bug, or manual `DELETE FROM` on source) is required.

---

## Phase 4 — Cleanup: PASS

### 4a — Reset DAA=false on providers: PASS

```sql
UPDATE table_configs SET delete_after_archive = false ... WHERE table_id = 'dev2_archive.source_data_samples.providers'
```

Confirmed `delete_after_archive = false`.

### 4b — Source data restoration: deferred to next step in plan

The plan calls for re-seeding via `generate_test_data` between 36T and 37T. That will idempotently restore the 5 NULL-effective_date rows for providers and re-overwrite all 1000 rows. The 163 stranded year-2020 rows will be overwritten in place since `generate_test_data` runs in `Overwrite` mode.

---

## What this proves

| Behavior | Pass criteria | Result |
|---|---|---|
| Folder deleted while audit = `ARCHIVED` and DAA flipped to `true` | Detected as `MISSING + ARCHIVED + DAA=true` | **PASS** (state machine routed correctly) |
| Action chosen | `CREATE` (rule F wins; not RESUME_DELETE) | **PASS** |
| Self-heal | Folder rebuilt; new `ARCHIVED` audit row with `archive_mode = CREATE` | **PASS** |
| Delete-after-archive runs in same run | Source year 2020 = 0 rows | **FAIL** — source still has all 163 rows |
| Final audit row | `ARCHIVED_AND_DELETED` | **PASS structurally** but **misleading** (claims a delete that did not happen) |
| No FAILED, no SKIPPED_CONCURRENT | Recovery is silent and complete | **PASS** but the silence here is the problem |
| Re-run after recovery | Rule D triggers SKIP | **PASS** |

---

## What Happened

Pre-flight confirmed providers/2020 had a single `ARCHIVED` row (record_count 163) from the clean-slate Phase 0 run, the folder was present with 163 rows, and source had 163 matching rows. We flipped `delete_after_archive = true` and deleted the archive folder by hand. The subsequent archive run logged the expected three audit rows (`STARTED → ARCHIVED CREATE → ARCHIVED_AND_DELETED`) all sharing one archive_run_id, rebuilt the folder with 163 rows, and reported success. However, querying the source revealed all 163 rows still present, and `DESCRIBE HISTORY` on the source showed no DELETE operation from the run. A subsequent re-run produced `SKIPPED` (rule D), which means the bug also blocks any later self-correction.

## Bug Summary

- **Severity:** High. The audit log lies about source state.
- **Path:** `src/archiver.py` `_execute_archive_create_or_append`, CREATE branch with `delete_after = true` and a non-null `last_watermark` from a prior `ARCHIVED` audit row.
- **Mechanism:** `_run_watermark_window(after_watermark=last_watermark)` filters the just-written archive Delta to rows strictly above `last_watermark`. For a re-CREATE those rows do not exist (the entire 2020 range is at-or-below `last_watermark` from the prior run). Window = `(None, None)`. `_delete_archived` returns immediately. `ARCHIVED_AND_DELETED` is logged anyway.
- **Operator visibility:** zero. No FAILED row, no warning. Only manual source-row counting reveals the discrepancy.
- **Scope:** Only the `MISSING + ARCHIVED + DAA=true` recovery path. Other DAA flows (initial CREATE, RESUME_DELETE) compute the window correctly.

## Next Steps

1. **File a bug** against `src.archiver._execute_archive_create_or_append` for the CREATE branch with `delete_after = true`. Recommended fix: pass `after_watermark=None` (or the archive's actual MIN-1) when `action == "CREATE"`, since the entire archive slice was written this run. Add a unit test that constructs `MISSING + ARCHIVED + DAA=true` state and asserts the source `DELETE` actually executes.
2. **Manual remediation in this workspace** (will happen automatically): the next plan step (`reseed-mid`) re-runs `generate_test_data` in Overwrite mode, which will reset providers source to a clean 1000 rows. The 163 stranded rows from this test will be replaced.
3. Continue to 37T after re-seed, scoped to providers retention oscillation. The 36T residue does not affect 37T because 37T verification queries scope by `archive_run_id` and `year`, not by historical state.
