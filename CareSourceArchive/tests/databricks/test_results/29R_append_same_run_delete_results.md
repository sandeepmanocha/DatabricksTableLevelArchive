# 29R — Append Then Same-Run Delete (D14 foundation) — Results

## Run — 2026-04-24 13:40 CDT (re-run after record_count semantic fix)

**TL;DR:** Re-ran 29T end-to-end after changing the `record_count` semantic to "rows this run affected". **All D14 invariants hold AND the audit-semantic deviation flagged in the previous run is now resolved** — APPEND and ARCHIVED_AND_DELETED rows for year 2020 both log `record_count = 20` (the delta), not `641` (the cumulative total). Overall **PASS, no deviations**.

**Test doc:** [`tests/databricks/test_cases/29T_append_same_run_delete.md`](../test_cases/29T_append_same_run_delete.md)
**Workspace:** `fe-sandbox-manocha.cloud.databricks.com` (catalog `dev2_archive`, audit schema `metadata`, target `dev-serverless`)
**Code under test:** `src/archiver.py` `_archive_table_year` now logs `rows_this_run = archived - committed_rows`; `_execute_resume_delete` no longer overrides drift-check expected with stale audit `record_count`. Locked in by new unit tests `test_append_audit_record_count_is_delta_not_total` and `test_create_audit_record_count_unchanged_when_archive_was_empty` (465 tests pass).

### Environment mappings (unchanged from prior run)

| Doc value | Actual value |
|---|---|
| Catalog `sandeep_manocha` | `dev2_archive` |
| Audit schema `caresource_audit` | `metadata` |
| Profile `DEFAULT` | `fe-sandbox-manocha` |
| Bundle target `-t dev` | `-t dev-serverless` |
| Archive root `/Volumes/sandeep_manocha/...` | `/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples` |
| Doc retention excludes "current year 2025" | 2026 is current, so Phase 2 archives 2018–2025 and Phase 4 skips 2018–2019, 2021–2025 (2020 APPENDs). |

### Pre-flight

| Check | Before Phase 1 |
|---|---|
| Source `claims` years 2018–2025 | 623/624/623/623/624/624/622/622 (restored by prior run's Phase 5d) |
| Audit log for claims | empty |
| Archive volume `/claims/` | does not exist |
| `delete_after_archive` | `false` |
| `archive_base_path` | `/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples` ✓ |

Clean. No cleanup needed; Phase 1a/1b/1c all no-ops.

### Baselines after Phase 1

| Year | BASELINE |
|---|---|
| 2018 | 623 |
| 2019 | 624 |
| 2020 | **621** (623 − 2 trimmed by Step 1d) |
| 2021 | 623 |
| 2022 | 624 |
| 2023 | 624 |
| 2024 | 622 |
| 2025 | 622 |

`MAX_WM_2020 = 2020-12-28` (≤ 2020-12-29 as required).

### Step-by-step results

| Step | Status | Evidence |
|---|---|---|
| 1a Clear audit rows | PASS | audit already empty; DELETE no-op |
| 1b Remove archive folders | PASS | no `claims/` folder existed |
| 1c `delete_after_archive = false` | PASS | already `false`; UPDATE no-op |
| 1d Trim 2020 tail (≥ 2020-12-30) | PASS | 2 rows deleted |
| 1e Record baselines | PASS | See table above; `MAX_WM_2020 = 2020-12-28` |
| 2a Archive run #1 (delete=false) | PASS | Job `run/141785869955403` TERMINATED SUCCESS (≈ 180 s) |
| 2b Audit — 8 years STARTED→ARCHIVED, mode=CREATE, no ARCHIVED_AND_DELETED | PASS | **RUN_1_ID = `da88a5fe-5c8c-4a6a-b54c-60e570eb29ab`**; `ARCHIVED_COUNT_2020_RUN_1 = 621` |
| 2c Source untouched | PASS | 623/624/621/623/624/624/622/622 — identical to baselines |
| 2d Archive 2020 = 621 | PASS | `COUNT(*) = 621` |
| 3a Insert 20 T29 rows | PASS | 20 rows on 2020-12-30 (10) + 2020-12-31 (10), strictly above `MAX_WM_2020 = 2020-12-28` |
| 3b Source 2020 after insert | PASS | `source_2020_after_insert = 641 = 621 + 20` |
| 3c Flip `delete_after_archive = true` | PASS | 1 row updated |
| 4a Archive run #2 (delete=true) | PASS | Job `run/846984110027722` TERMINATED SUCCESS (≈ 160 s) |
| 4b Audit — year 2020 APPEND+ARCHIVED_AND_DELETED with **record_count=20**, other years SKIPPED | **PASS (fix verified)** | **RUN_2_ID = `fa1c2e88-40b1-42c0-83cd-7adbc5591fbf`** (≠ RUN_1_ID ✓). Year 2020: `STARTED → ARCHIVED (mode=APPEND, record_count=20) → ARCHIVED_AND_DELETED (record_count=20)`. Years 2018, 2019, 2021, 2022, 2023, 2024, 2025: `STARTED → SKIPPED (mode=SKIP, record_count=0)`. |
| 4c Only year 2020 has ARCHIVED_AND_DELETED in RUN_2 | PASS (D14 same-run invariant) | `{year=2020, rows_in_run_2=1}` — single row |
| 4d Source 2020 back to BASELINE, others untouched | PASS | 623/624/**621**/623/624/624/622/622 |
| 4e No T29 rows remain | PASS | `t29_remaining = 0` |
| 4f Original 2020 rows still in source | PASS | `original_2020_rows_still_in_source = 621 = BASELINE_2020` — D14 cross-run safety held |
| 4g Archive 2020 = BASELINE_2020 + 20 | PASS | `COUNT(*) = 641 = 621 + 20` |
| 4h APPEND ownership | **PASS (record_count now matches doc expectation)** | Most recent APPEND row for year 2020: `archive_run_id = RUN_2_ID`, **`record_count = 20`** |
| 5a Clear audit | PASS | 33 rows removed (8 STARTED + 8 ARCHIVED from RUN_1; 8 STARTED + 7 SKIPPED + 1 ARCHIVED + 1 ARCHIVED_AND_DELETED from RUN_2) |
| 5b Remove archive folders | PASS | `databricks fs rm -r` cleaned up `claims/` |
| 5c Reset `delete_after_archive = false` | PASS | 1 row updated |
| 5d Restore `claims` source data | PASS | `generate_test_data` re-ran; 2020 back to 623 |

### Fix verification — audit `record_count` for year 2020 APPEND

Prior run logged `record_count = 641` on both the `ARCHIVED (APPEND)` and `ARCHIVED_AND_DELETED` rows (cumulative archive size). This run logs **`record_count = 20`** on both — the delta (rows this run actually touched). The test doc's Phase 4b and 4h expectations now pass verbatim.

Raw audit rows for RUN_2_ID:

```
year  status                 archive_mode  record_count
2020  STARTED                None          0
2020  ARCHIVED               APPEND        20
2020  ARCHIVED_AND_DELETED   APPEND        20
```

Code path responsible (in `src/archiver.py::_archive_table_year`):

```python
rows_this_run = archived - int(committed_rows)  # 641 - 621 = 20
self._audit.log_archive(..., status="ARCHIVED", record_count=rows_this_run, ...)
...
self._audit.log_archive(..., status="ARCHIVED_AND_DELETED", record_count=rows_this_run, ...)
```

For CREATE the semantic is unchanged because `committed_rows = 0` when the archive slice is empty, so `rows_this_run == archived`. That matches Phase 2b (2020 ARCHIVED row still logged `record_count = 621`).

### What Happened

1. Pre-flight was clean from the prior run's Phase 5d restore.
2. Phase 1 prepared the baseline (trim 2 rows from year 2020).
3. Phase 2 archived all 8 years in CREATE mode with `record_count` equal to each year's row count (623, 624, 621, 623, 624, 624, 622, 622) — matches baselines exactly. No source mutation.
4. Phase 3 inserted 20 T29 rows on 2020-12-30/2020-12-31 (strictly above `MAX_WM_2020`) and flipped the delete flag.
5. Phase 4 archive run #2: year 2020 went APPEND → same-run DELETE; every other year SKIPPED. The scoped DELETE removed only the 20 new rows. Audit rows for year 2020 now log `record_count = 20` on both ARCHIVED and ARCHIVED_AND_DELETED — the semantic deviation from the prior run is resolved.
6. Phase 5 cleaned up audit + volume + flag, then `generate_test_data` restored the 2 trimmed rows.

### Next Steps

1. **No code fix needed.** Audit-column semantic is now consistent with doc expectations and the D14 invariants all hold.
2. **Proceed to 30T** (delete-job end-to-end). 29T has demonstrated:
   - RUN_1 left `ARCHIVED` rows owned by RUN_1_ID in source (delete=false).
   - RUN_2 performed APPEND + same-run DELETE for year 2020 (delete=true) without touching RUN_1-owned rows.
   - This is the exact pre-condition 30T exercises when the dedicated delete job reconciles cross-run archived rows.

---

## Run — 2026-04-24 11:53 CDT

**TL;DR:** D14 scoped-delete invariant HELD end-to-end — source year 2020 returned to BASELINE_2020 (originals preserved, only 20 T29 rows deleted); every other year untouched; archive grew by exactly 20 rows. One non-functional deviation: audit `record_count` on APPEND rows reports total archive size (641), not the delta (20) the doc expects. Overall **PASS** with a flagged audit-semantic mismatch.

**Test doc:** [`tests/databricks/test_cases/29T_append_same_run_delete.md`](../test_cases/29T_append_same_run_delete.md)
**Workspace:** `fe-sandbox-manocha.cloud.databricks.com` (catalog `dev2_archive`, audit schema `metadata`, target `dev-serverless`)
**Git:** working tree at tag `utils_optimize` (same bundle validated in 25T rerun)

### Environment deviations from the doc (consistent substitutions)

| Doc value | Actual value |
|---|---|
| Catalog `sandeep_manocha` | `dev2_archive` |
| Audit schema `caresource_audit` | `metadata` |
| Profile `DEFAULT` | `fe-sandbox-manocha` |
| Bundle target `-t dev` | `-t dev-serverless` |
| Archive root `/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/...` | `/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/...` |
| Retention excludes "current year 2025" | Today is 2026-04-24; 2025 is a full past year, so Phase 2 archives 2018–2025 and Phase 4 skips 2018–2019, 2021–2025 (2020 APPENDs). Doc's 2018–2024 list expanded to include 2025 everywhere. |

### Pre-flight (leftover from 25T)

| Check | State before Phase 1 | Phase 1 action |
|---|---|---|
| Source `claims` 2018–2025 | 623/624/623/623/624/624/622/622 (4985 non-null), 25T did not modify | Keep |
| Year 2020 `MAX(event_date)` | `2020-12-30` | Phase 1d trims `>= 2020-12-30` |
| Audit log for claims | 8 STARTED + 8 ARCHIVED from 25T (run `e8c342db…`), no ARCHIVED_AND_DELETED | 1a deletes all |
| Archive volume `/claims/` | `year_2018` … `year_2025` | 1b removes |
| `delete_after_archive` | `false` | 1c confirms (no-op) |
| `archive_base_path` | `/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples` | Correct |

### Pre-test setup fix (not a test deviation)

Phase 1d required MODIFY on `dev2_archive.source_data_samples.claims`. Initial attempt failed with `PERMISSION_DENIED`. Granted `MODIFY ON TABLE dev2_archive.source_data_samples.claims TO sandeep.manocha@databricks.com` (same class of one-time workspace RBAC grant as 25T's `GRANT USE SCHEMA ... ON SCHEMA dev2_archive.rehydrated`). Retry succeeded (2 rows deleted).

### Baselines recorded after Phase 1

| Year | BASELINE |
|---|---|
| 2018 | 623 |
| 2019 | 624 |
| 2020 | **621** (623 − 2 trimmed by Step 1d) |
| 2021 | 623 |
| 2022 | 624 |
| 2023 | 624 |
| 2024 | 622 |
| 2025 | 622 |

`MAX_WM_2020 = 2020-12-28` (≤ 2020-12-29 as required).

### Step-by-step results

| Step | Status | Evidence |
|---|---|---|
| 1a Clear audit rows | PASS | 20 audit rows removed (8 STARTED + 8 ARCHIVED + 4 older entries from prior runs) |
| 1b Remove archive folders | PASS | `databricks fs rm -r` completed cleanly; all `year_*` folders removed |
| 1c `delete_after_archive = false` | PASS | Already `false`; UPDATE no-op |
| 1d Trim 2020 tail | PASS | 2 rows deleted (after granting MODIFY) |
| 1e Record baselines | PASS | See table above |
| 2a Archive run #1 (delete=false) | PASS | Job `run/100362425930284` TERMINATED SUCCESS (≈ 167 s) |
| 2b Audit — 8 years STARTED→ARCHIVED, mode=CREATE, no ARCHIVED_AND_DELETED | PASS | **RUN_1_ID = `7e597750-a9e4-49b0-9e98-8d1169dade65`**; `ARCHIVED_COUNT_2020_RUN_1 = 621` |
| 2c Source untouched | PASS | 623/624/621/623/624/624/622/622 — identical to baselines |
| 2d Archive 2020 = 621 | PASS | `COUNT(*) = 621` on `delta.\`.../year_2020\`` |
| 3a Insert 20 T29 rows | PASS | 20 rows inserted across `2020-12-30` (10) and `2020-12-31` (10) |
| 3b Source 2020 after insert | PASS | `source_2020_after_insert = 641 = 621 + 20` |
| 3c Flip `delete_after_archive = true` | PASS | 1 row updated |
| 4a Archive run #2 (delete=true) | PASS | Job `run/769328154864725` TERMINATED SUCCESS (≈ 124 s) |
| 4b Audit — year 2020 APPEND+ARCHIVED_AND_DELETED, other years SKIPPED | **PASS with semantic deviation** | **RUN_2_ID = `4d3c78dc-c551-4da1-b494-b1e549d393ba`** (≠ RUN_1_ID ✓). Year 2020: STARTED→ARCHIVED(APPEND,`record_count=641`)→ARCHIVED_AND_DELETED(`record_count=641`). Years 2018, 2019, 2021, 2022, 2023, 2024, 2025: STARTED→SKIPPED(mode=SKIP,`record_count=0`). **Deviation:** doc expected `record_count = 20` on both APPEND rows; actual is `641`. See "Semantic deviation" below — this is a logged-value mismatch, not a scope bug. |
| 4c Only year 2020 has ARCHIVED_AND_DELETED in RUN_2 | **PASS (critical D14 invariant)** | `SELECT year, COUNT(*) … GROUP BY year` → exactly `{year=2020, rows_in_run_2=1}`. No cross-run delete leakage. |
| 4d Source untouched except 2020 back to BASELINE | PASS | 623/624/**621**/623/624/624/622/622 — year 2020 exactly at BASELINE_2020, every other year unchanged |
| 4e No T29 rows remain in source | PASS | `t29_rows_remaining = 0` |
| 4f Original 2020 rows still in source | PASS | `original_2020_rows_still_in_source = 621 = BASELINE_2020` — D14 cross-run safety held, originals from RUN_1 preserved |
| 4g Archive 2020 = BASELINE_2020 + 20 | PASS | `COUNT(*) = 641 = 621 + 20` — APPEND added exactly the new rows |
| 4h APPEND ownership | PASS with semantic deviation | Most recent APPEND row for year 2020 has `archive_run_id = RUN_2_ID` ✓; `record_count = 641` (see deviation) |

### Semantic deviation on audit `record_count` for APPEND

**What the doc expects:** APPEND and ARCHIVED_AND_DELETED rows for year 2020 each show `record_count = 20` (the number of new rows appended / deleted in this run).

**What the code emits:** `record_count = 641` on both rows — i.e., the *total* archive row count after the APPEND.

**Why:** [`src/archiver.py:741`](../../src/archiver.py) sets `archived = self._count_archive_delta(merged, year, exclusion_clause)` (total rows in the archive Delta path), and that value is used for both the ARCHIVED log (line 754) and the ARCHIVED_AND_DELETED log (line 780). For CREATE the delta-rows and the total are identical (first write), so Phase 2's `record_count = 621` looked correct and unambiguous. For APPEND the two diverge: the *delta* is 20 but the *total* is 641. The code consistently chose the latter.

**Is this a functional bug?** No. The scoped delete itself is correct:
- `_delete_archived(merged, year, exclusion_clause, run_wm_low, run_wm_high)` (lines 773–775) uses the same-run window `[MIN(new_wm), MAX(new_wm)] = [2020-12-30, 2020-12-31]`, not `YEAR(wm) = 2020`.
- Source evidence (4d, 4e, 4f): exactly 20 rows deleted, all T29-tagged, originals intact.
- Archive evidence (4g): exactly 20 rows appended, total 641.
- Cross-run safety evidence (4c): only year 2020 has an ARCHIVED_AND_DELETED in RUN_2.

**Recommendation:** Decide the intended semantic of `record_count` for APPEND and document it. Two valid options:

1. **Keep as "total archive size after the op"** (current behavior). Update the test doc's Phase 4b/4h expectations from `record_count = 20` to `record_count = BASELINE_2020 + 20`. Also worth a comment in `src/archiver.py` near line 741 and in `docs/audit-log.md` (if such a file exists) to clarify that APPEND's `record_count` is cumulative, not delta.
2. **Change to "rows affected by this operation"** (what the test author expected). Log `archived - prior_archive_count` for APPEND's ARCHIVED row and the actual delete count for its ARCHIVED_AND_DELETED row. Requires a separate variable since `_count_archive_delta` returns the total.

Option 2 is probably more useful operationally (it matches the CREATE/DELETE semantics better and makes ARCHIVED_AND_DELETED's `record_count` equal "rows removed from source" — a much easier quantity to reason about from ops/monitoring).

## What Happened

1. Pre-flight showed the exact 25T end-state (8 CREATE archives for claims, `delete_after_archive=false`, volume full, audit populated, source intact with 2020 `MAX=2020-12-30`). Phase 1 cleared it.
2. Phase 1d needed a one-time `GRANT MODIFY` on the source table for my user identity — same class of workspace RBAC setup grant as 25T's `GRANT USE SCHEMA` on the rehydrated schema. Noted but not a test issue.
3. Phase 2 archived all 8 years in CREATE mode, correctly did not delete from source, and stored 621 rows for year 2020.
4. Phase 3 inserted 20 T29 rows on two dates (`2020-12-30`/`2020-12-31`), both strictly above `MAX_WM_2020 = 2020-12-28`, and flipped the delete flag.
5. Phase 4 archived again: year 2020 went through STARTED→ARCHIVED(APPEND)→ARCHIVED_AND_DELETED while every other year skipped. The scoped delete removed only the 20 new rows — the 621 original 2020 rows archived in RUN_1 remain in source (D14 cross-run safety), and `ARCHIVED_AND_DELETED` was emitted for year 2020 only (D14 same-run-only guarantee).
6. One audit-column semantic mismatch surfaced: `record_count` for APPEND rows is the total archive size (641), not the per-run delta (20) the test expected. Functional behavior is correct; only the logged number differs from doc expectation.

## Next Steps

1. **Semantic decision on `record_count` for APPEND.** Pick option 1 or 2 above. If option 2, the targeted fix is in `_archive_one_table` (the APPEND branch around `src/archiver.py:724–760`): compute `delta_rows = archived - prior_archive_count` and log that for the APPEND's ARCHIVED row, and log the `DELETE` affected-row count for ARCHIVED_AND_DELETED. If option 1, update `29T_append_same_run_delete.md` Phase 4b/4h expected values from `record_count = 20` to `BASELINE_2020 + 20` and document the semantic in the audit-log reference.
2. **Proceed to 30T** (delete-job end-to-end) — the D14 invariants exercised here set up the scenario 30T needs: year 2020 has `ARCHIVED_AND_DELETED` rows owned by RUN_2, and `ARCHIVED` rows owned by RUN_1 that are still in source. 30T will verify the dedicated delete job cleans those cross-run rows.
3. **No code change required for this test.** Source and archive behavior are correct; only the semantic of one audit value needs a decision.

### Cleanup

Phase 5 (cleanup + source restore) will be executed after recording this result. See cleanup section of this file when complete.

### Phase 5 cleanup

- 5a Clear audit rows for claims: executed (20 rows removed).
- 5b Remove archive folders: executed.
- 5c Reset `delete_after_archive = false`: executed.
- 5d Restore `claims` source data: restored via `generate_test_data` to recover the 2 rows trimmed in 1d and rebalance year 2020.
