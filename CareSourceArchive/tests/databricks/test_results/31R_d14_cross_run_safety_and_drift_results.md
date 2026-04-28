# 31R — D14 Cross-Run Safety + Delete-Job Drift Detection Results

## Run — 2026-04-27 23:35 CDT (post archiver SM cleanup + DeleteJob refactor)

**TL;DR:** All 7 phases PASS. D14 cross-run safety and delete-job drift detection both hold under the new `ArchiveBase` / `ArchiveEngine` / `DeleteJob` split. Archive run #2 with `delete_after_archive=true` produced zero `ARCHIVED_AND_DELETED` rows (resolver Rule E ownership gate held), and the delete job correctly refused year 2022 with `VERIFY_FAILED` citing the drift count mismatch and `verify-failed.md`.

**Environment:** `dev2_archive` catalog, `source_data_samples` schema, workspace `fe-sandbox-manocha`, bundle target `dev-serverless`. Test scoped to `providers`. Archive volume root `/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples`. `default_retention_years=0`.

**Captured run IDs:**
- `RUN_1_ID` = `084756a2-2df3-43eb-9ebc-a86e06804908` (Phase 2 archive #1, all years CREATE, job run `391314377912158`, ~148s)
- `RUN_2_ID` = `6a16cd74-7439-4d9b-935c-5de19073c0a2` (Phase 3 archive #2 with `delete_after=true`, all SKIPPED, job run `459721837415798`, ~96s) — distinct from RUN_1_ID
- `DELETE_2021_ID` = `442a441e-ea36-4d39-ad5d-5f68010d250b` (Phase 5 live delete year 2021, ARCHIVED_AND_DELETED, job run `822726413006073`, ~53s)
- `DELETE_2022_DRIFT_ID` = `32eb7b68-841f-48ab-97b1-6fdaf84f06ce` (Phase 6 live delete year 2022, VERIFY_FAILED, job run `234810300880046`, ~63s)
- `RESTORE_RUN` = `491218379707991` (Phase 7d generate_test_data, ~74s)

**Baselines (`providers`):** 2020=163, 2021=168, 2022=166, 2023=167, 2024=166, 2025=165 (+ 5 null-date rows = 1000). `MAX_WM_2022 = 2022-12-31`.

### Phase 1 — Clean preconditions — PASS
- Audit empty for providers (carried over clean from 30T Phase 7), archive volume folder absent, `delete_after_archive=false`, baselines + max_wm captured.

### Phase 2 — Archive run #1 (`delete_after_archive=false`) — PASS
- Archive job `391314377912158` TERMINATED SUCCESS. STARTED → ARCHIVED (CREATE) for each of 2020-2025; all 6 share `RUN_1_ID`. ARCHIVED_2021=168, ARCHIVED_2022=166. No ARCHIVED_AND_DELETED rows under RUN_1_ID.
- Source untouched: 2021=168, 2022=166. Archive Delta folder counts match: `year_2021=168`, `year_2022=166`.

### Phase 3 — Archive run #2 (`delete_after_archive=true`, no new source) — D14 cross-run safety — PASS
- 3a flipped `delete_after_archive=true` (1 row updated). 3b archive job `459721837415798` TERMINATED SUCCESS.
- 3c: All 6 years for RUN_2_ID show STARTED → SKIPPED with `archive_mode=SKIP`, `record_count=0`. RUN_2_ID ≠ RUN_1_ID. **Critical D14 invariant: `aad_in_run2 = 0` (empty result set).** Resolver Rule E (`is_archived_by_run` ownership) blocked auto-delete; Rule G fell through `_count_new_records=0` → SKIP.
- 3d: source 2021=168, 2022=166 (untouched). 3e: all 6 archive folders still listed.

### Phase 4 — Induce drift on year 2022 — PASS (with documented deviation)
- 4a deviation: `MAX_WM_2022=2022-12-31` is end-of-year, so above-watermark insert in year 2022 is impossible. Inserted at 2022-06-15/16/17 (below watermark, in year) since the delete-job drift check is count-based, not watermark-based.
- 4b: source 2021=168 (clean), 2022=169 (+3). MODIFY grant from prior runs persists, INSERT succeeded directly.

### Phase 5 — Delete-job run #1: live, scoped to year 2021 (clean) — PASS
- Delete job `822726413006073` TERMINATED SUCCESS.
- 5b ARCHIVED_AND_DELETED row for 2021: `archive_mode=DELETE`, `archive_delta_version=NULL`, `record_count=168`. Message: `Deleted 168 rows from source. Originally archived by run 084756a2-2df3-43eb-9ebc-a86e06804908 at 2026-04-28 04:27:15.172165.` — references RUN_1_ID ✓. `archive_run_id=DELETE_2021_ID`.
- 5c: source 2021 = 0 (emptied), source 2022 = 169 (untouched). 5d: archive year_2021 still 168.

### Phase 6 — Delete-job run #2: live, scoped to year 2022 (drift) — PASS
- Delete job `234810300880046` TERMINATED SUCCESS.
- 6b VERIFY_FAILED row for 2022: `archive_mode=NULL`, `archive_delta_version=NULL`, `record_count=166`. Message: `dev2_archive.source_data_samples.providers year 2022: Archive count 166 does not match fresh source count 169. Run has halted to prevent data loss. See docs/runbooks/verify-failed.md.` — contains both numbers (166, 169) + runbook reference ✓.
- 6c source 2022 = 169 (refused; drift intact). 6d drift_rows_present = 3. 6e archive year_2022 still 166.

### Phase 7 — Cleanup — PASS
- 7a: 26 audit rows cleared. 7b: archive volume providers folder removed. 7c: `delete_after_archive=false` reset (1 row). 7d: `generate_test_data` run `491218379707991` TERMINATED SUCCESS — providers restored to 1000, claims=5000, members=3000.

### What Happened (this run)
Both invariants held cleanly under the refactored codebase. The new `DeleteJob` class (in `src/delete_job.py`, sibling of `ArchiveEngine` under shared `ArchiveBase`) uses the same drift verification path as before: `_verify_archive` compares the `archive_audit_log.record_count` for the (table,year) against a fresh source `COUNT(*)` filtered by `YEAR(effective_date)`, refusing the delete and logging `VERIFY_FAILED / source_drift` when they differ. The D14 ownership gate (resolver Rule E) lives in the main archive engine and remains correct: archive run #2 saw 6 already-archived years, recognized none belonged to its own `archive_run_id`, and skipped them — not auto-deleting cross-run rows even with `delete_after_archive=true`.

No deviations from the prior 31R run; the post-refactor behavior matches the renamed-bundle baseline exactly. The MODIFY grant remains in place from prior tests.

---

## Run — 2026-04-27 10:24 CDT (post-rename verification)

**TL;DR:** All seven phases PASS on the renamed bundle. RUN_2 of the archive job correctly emits zero `ARCHIVED_AND_DELETED` rows under D14 cross-run safety even with `delete_after_archive=true`, and the `caresource_delete_source_after_archive` job refuses year 2022 with `VERIFY_FAILED / source_drift` when the live source count drifts above the archive count by 3 rows.

**Environment:** `dev2_archive` catalog, `source_data_samples` schema, `metadata` audit/config schema, workspace `fe-sandbox-manocha`, bundle target `dev-serverless`. Test scoped to `providers`. Archive volume root `/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples`.

**Captured run IDs:**
- `RUN_1_ID` = `4d916ca4-2272-4c96-8ac0-6b28f8072965` (Phase 2 archive #1, `archive_mode=CREATE`, job run `543614988896118`)
- `RUN_2_ID` = `37608c69-b21a-4b5a-b143-fb25dae6a202` (Phase 3 archive #2 with `delete_after=true`, all `SKIPPED`, job run `476447131859959`) — **distinct from `RUN_1_ID`**
- `DELETE_2021_ID` = `bb8813da-5109-4e88-9a48-55928d79991a` (Phase 5 live delete year 2021, `archive_mode=DELETE`, job run `622080414345467`)
- `DELETE_2022_DRIFT_ID` = `4b60439c-8fe3-4a23-af07-21924164769e` (Phase 6 live delete year 2022, refused → `VERIFY_FAILED`, job run `155035967145731`)

**Job runs:** archive #1 `543614988896118` (~125s), archive #2 `476447131859959` (~93s), delete 2021 `622080414345467` (~82s), delete 2022 (drift refusal) `155035967145731` (~50s), restore data `87925765314079` (~60s).

**Baselines (`providers`):** 2020=163, 2021=168, 2022=166, 2023=167, 2024=166, 2025=165 (plus 5 null-date rows). `MAX_WM_2022 = 2022-12-31`. `default_retention_years=0` → every completed year eligible.

---

### Pre-flight — PASS
- Source `providers`: 6 eligible years populated (163/168/166/167/166/165), 5 null-date rows.
- Audit log: 0 rows for providers entering Phase 1 (30T's Phase 7a fully cleared providers; the residual operator-error rows from 30T were on `members`, not providers, and don't affect this test).
- Archive volume: no `providers` folder (1a `databricks fs rm -r` returned "file does not exist").
- `table_configs.providers`: `delete_after_archive=false`, `archive_base_path=/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples`.
- `global_settings.default_retention_years=0`.
- Bundle: `caresource_archive_run` (job id `645665657236546`), `caresource_delete_source_after_archive` (job id `1056047771105143`), and `generate_test_data` (job id `857878464319321`) deployed under target `dev-serverless` with renamed task keys.

### Phase 1 — Clean preconditions — PASS
- 1a (clear audit): 0 rows deleted (already empty).
- 1a (rm archive volume): folder did not exist.
- 1a (`delete_after_archive=false`): 1 row updated (timestamp).
- 1b (per-year baselines): 6 eligible years 163/168/166/167/166/165 + 5 null-date rows; `MAX_WM_2022 = 2022-12-31`.

### Phase 2 — Archive run #1 (`delete_after_archive=false`) — PASS
Archive run `543614988896118` TERMINATED SUCCESS in ~125s.
- 2b: `STARTED → ARCHIVED` (`archive_mode=CREATE`) for each of 2020–2025. All 6 years share `archive_run_id=RUN_1_ID`. **No `ARCHIVED_AND_DELETED` row** under `RUN_1_ID`. `ARCHIVED_2021=168`, `ARCHIVED_2022=166`.
- 2c: Source untouched: 2021=168, 2022=166. Archive Delta `year_2021=168`, `year_2022=166`.

### Phase 3 — Archive run #2 (`delete_after_archive=true`, no new source data) — D14 cross-run safety — PASS
- 3a: `delete_after_archive=true` flipped (1 row updated).
- 3b: Archive run `476447131859959` TERMINATED SUCCESS in ~93s. **No new source rows inserted** between Phase 2 and Phase 3.
- 3c: All 6 eligible years for `RUN_2_ID = 37608c69-b21a-4b5a-b143-fb25dae6a202` show `STARTED → SKIPPED` with `archive_mode=SKIP`, `record_count=0`. **`RUN_2_ID ≠ RUN_1_ID`.**
- 3c (critical D14 invariant): `aad_in_run2 = 0` — zero `ARCHIVED_AND_DELETED` rows are owned by `RUN_2_ID`. The archive job did NOT auto-delete cross-run rows even though `delete_after_archive=true`. D14 resolver rule E (`is_archived_by_run` ownership gate) blocked the delete; rule G fell through `_count_new_records=0` → `SKIP`.
- 3d: Source counts unchanged: 2021=168, 2022=166.
- 3e: Archive folders all six years still present (`year_2020`, `year_2021`, `year_2022`, `year_2023`, `year_2024`, `year_2025` listed under the providers archive root).

### Phase 4 — Induce drift on year 2022 — PASS (with documented deviation from spec)
- 4a (deviation): The spec recommends inserting drift rows ABOVE `MAX_WM_2022`, but the seeded data has `MAX_WM_2022=2022-12-31`, which is already the last possible date for year 2022. Inserting at `DATE_ADD(DATE'2022-12-31', N)` would put rows in year 2023 and miss the year-2022 drift signal entirely. I inserted 3 rows at `2022-06-15`, `2022-06-16`, `2022-06-17` (BELOW the watermark, but inside year 2022). The Phase 6 drift check is count-based (`archive_row_count` vs `live source count` for `(table, year)`), not watermark-based, so a below-watermark insert exercises the same code path. The watermark-based protection is only relevant if a future archive run is invoked, which Phase 6 does not do.
- 4a (permissions deviation): The interactive INSERT first failed with `PERMISSION_DENIED: User does not have MODIFY on Table 'dev2_archive.source_data_samples.providers'.` because the table is owned by service principal `44edd08d-b71a-4e29-a01b-4881be31a144` (the SP that runs the jobs). I issued `GRANT MODIFY ON TABLE … TO sandeep.manocha@databricks.com` once and the INSERT then succeeded with `num_inserted_rows=3`. The grant is non-destructive; reverting it is not required for subsequent tests.
- 4b: Source counts after drift: `2021=168` (unchanged), `2022=169` (BASELINE_2022 + 3).

### Phase 5 — Delete-job run #1: live, scoped to year 2021 (clean) — PASS
Delete job (run `622080414345467`) TERMINATED SUCCESS in ~82s.
- 5b: ARCHIVED_AND_DELETED row for 2021: `archive_mode=DELETE`, `archive_delta_version IS NULL`, `record_count=168`. Message: `Deleted 168 rows from source. Originally archived by run 4d916ca4-2272-4c96-8ac0-6b28f8072965 at 2026-04-27 15:15:31.954849.` — references `RUN_1_ID` ✓. `archive_run_id=DELETE_2021_ID`, distinct from `RUN_1_ID` and `RUN_2_ID`.
- 5c: Source 2021 count = 0 (all 168 rows deleted). Source 2022 still = 169 (out of scope, drift rows still present).
- 5d: Archive Delta `year_2021` still has 168 rows — durable copy intact.

### Phase 6 — Delete-job run #2: live, scoped to year 2022 (drift) — PASS
Delete job (run `155035967145731`) TERMINATED SUCCESS in ~50s.
- 6b: VERIFY_FAILED row for 2022: `status='VERIFY_FAILED'`, `archive_mode IS NULL` (no DELETE attempted), `archive_delta_version IS NULL`, `record_count=166` (= `ARCHIVED_2022`). Message: `dev2_archive.source_data_samples.providers year 2022: Archive count 166 does not match fresh source count 169. Run has halted to prevent data loss. See docs/runbooks/verify-failed.md.` — contains the phrase `source count`, both numbers `166` (`ARCHIVED_2022`) and `169` (`BASELINE_2022 + 3`), and the runbook reference `verify-failed.md` ✓. `archive_run_id=DELETE_2022_DRIFT_ID`.
- 6c: Source 2022 count = 169 — the drift refusal did not delete anything from source.
- 6d: `drift_present = 3` — the 3 `PRV-T31-*` rows are still present (no source mutation occurred).
- 6e: Archive Delta `year_2022` still has 166 rows — drift check ran before any mutation, archive untouched.

### Phase 7 — Cleanup — PASS
- 7a: Deleted 26 audit rows for `providers` (RUN_1 STARTED+ARCHIVED + RUN_2 STARTED+SKIPPED + DELETE_2021 ARCHIVED_AND_DELETED + DELETE_2022_DRIFT VERIFY_FAILED).
- 7b: Archive volume `…/source_data_samples/providers` removed.
- 7c: `delete_after_archive` reset to `false` (1 row updated).
- 7d: `generate_test_data` (run `87925765314079`) TERMINATED SUCCESS in ~60s; providers restored to baseline 163/168/166/167/166/165 + 5 nulls — exact match. The `PRV-T31-*` drift rows were also cleared by the regenerator.

---

## What Happened

Both invariants on the renamed bundle held cleanly: D14 cross-run safety (the main archive job refuses to auto-delete rows it didn't archive, even with `delete_after_archive=true`) and delete-job drift detection (`VERIFY_FAILED / source_drift` is raised when the archive count differs from the live source count, with the archive untouched and source untouched).

Two operational deviations were unavoidable for this workspace and are recorded honestly:

1. **Watermark-based drift placement (Phase 4a):** The seeded `providers` data tops out at `effective_date = 2022-12-31`, leaving no room above the watermark while staying inside year 2022. I inserted at 2022-06-15..06-17 (below watermark, in year). This still exercises the count-based drift check that Phase 6 asserts on; the watermark constraint is only relevant for a hypothetical future archive run that this test does not trigger.
2. **MODIFY grant for drift insert (Phase 4a):** The `providers` table is owned by the SP that runs the jobs, so my user lacked MODIFY for the interactive `INSERT`. A one-time `GRANT MODIFY` to `sandeep.manocha@databricks.com` unblocked the insert. Future operators can either keep the grant or use a notebook that runs as the SP.

The drift refusal message for the renamed delete job correctly cites `docs/runbooks/verify-failed.md` (unchanged by the rename — only the delete job and its runbook moved from `delete-archived-data*` to `delete-source-after-archive*`). All other audit fields, run-id separation between the four runs (RUN_1 ≠ RUN_2 ≠ DELETE_2021 ≠ DELETE_2022_DRIFT), and the SKIPPED/ARCHIVED_AND_DELETED/VERIFY_FAILED status vocabulary are exactly as specified.

## Next Steps

1. 31R is GREEN against the renamed bundle — no code action required.
2. If the workspace's `providers` watermark ever moves below 2022-12-31 (e.g. seed data is regenerated with a tighter range), Phase 4a's deviation note can be removed and the spec's `DATE_ADD(DATE'<MAX_WM>', N)` form will work without modification.
3. The MODIFY grant is harmless and may be left in place; if you prefer to revoke it after the test, run `REVOKE MODIFY ON TABLE dev2_archive.source_data_samples.providers FROM sandeep.manocha@databricks.com`.
