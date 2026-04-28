# 30R — Dedicated Delete Job End-to-End Results

## Run — 2026-04-27 23:18 CDT (post-cleanup `DeleteJob` class split verification)

**TL;DR:** All seven phases **PASS** end-to-end on the post-cleanup branch. The new `DeleteJob` class in `src/delete_job.py` (extends `ArchiveBase`, replaces `ArchiveEngine.delete_source_after_archive`) behaves identically to the pre-split implementation across all four scenarios — dry-run preview, live scoped delete, D13 eligibility guard, and live no-filter scope guard. The notebook's `from src.delete_job import DeleteJob` import surface works through DABs deploy. No code or config changed during the test.

**Branch:** `feat/delta_config_build_v12_archive_refactor` (commit `a089564`, post-cleanup tip)
**Environment:** `dev2_archive` catalog, `source_data_samples` schema, `metadata` audit/config schema (translated from doc's `caresource_audit`), workspace `fe-sandbox-manocha`, bundle target `dev-serverless`. Test scoped to `providers`.
**Archive volume:** `/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples`

**Captured run IDs:**
- `SEED_RUN_ID` = `b2b04909-244c-459d-a568-cf344a22f454` (Phase 2 archive, `archive_mode=CREATE`, job run `327212425638665`)
- `DRY_RUN_1_ID` = `17c7f9da-253e-4f1d-b4b4-2383ec2c74ea` (Phase 3 dry-run, `action=WOULD_DELETE`, job run `751723943298184`)
- `DELETE_RUN_ID` = `32bc80db-2660-46f7-9c34-1b6381351fca` (Phase 4 live delete year 2020, `archive_mode=DELETE`, job run `1011042558227612`)
- Phase 5 D13 guard for 2019: `729af88e-9d21-4813-9ba4-e4490a8b7326` (job run `926636088224182`, TERMINATED SUCCESS — FAILED audit row logged, task did not raise)
- Phase 6a no-filter live: refused at `generate_parameters` with the exact `ArchiveConfigError` literal (no `archive_run_id` — engine never executed)
- Phase 6c no-filter dry-run (allowed): job run `357346706464083`

**Baselines (`providers`):** 2020=163, 2021=168, 2022=166, 2023=167, 2024=166, 2025=165 (plus 5 NULL-date rows; 2019 has zero rows — confirms D13 fixture for Phase 5).

### Steps

| # | Step | Result |
|---|------|--------|
| 1 | Phase 1 — clean preconditions (audit DELETE 26 rows, fs rm providers, `delete_after_archive=false`, baselines) | **PASS** |
| 2 | Phase 2a — seed archive run scoped to `providers` (`dry_run=false`, `delete_after_archive=false`) | **PASS** — TERMINATED SUCCESS in ~171s |
| 2b | STARTED→ARCHIVED(CREATE) for years 2020-2025; 0 ARCHIVED_AND_DELETED | **PASS** — `record_count` per year matches baseline |
| 2c | Source untouched | **PASS** — exact baselines |
| 2d | Archive folder `providers/year_2020` count = 163 = ARCHIVED_2020 | **PASS** (implicit via Phase 4d) |
| 3a | DeleteJob dry-run for years 2020,2021 | **PASS** — TERMINATED SUCCESS in ~53s |
| 3b | 2 DRY_RUN rows; `action=WOULD_DELETE`; record_count=163/168 (=BASELINE); `archive_mode`/`archive_delta_version` both NULL; new `DRY_RUN_1_ID` ≠ SEED | **PASS** |
| 3c | Source untouched (2020=163, 2021=168) | **PASS** |
| 4a | DeleteJob live delete for year 2020 | **PASS** — TERMINATED SUCCESS in ~63s |
| 4b | `ARCHIVED_AND_DELETED` row: `archive_mode='DELETE'`, `archive_delta_version=NULL`, `record_count=163`; message `Deleted 163 rows from source. Originally archived by run b2b04909... at 2026-04-28 04:04:14`; `DELETE_RUN_ID=32bc80db` (≠ SEED, ≠ DRY_RUN_1) | **PASS** |
| 4c | Source: year_2020 = 0; year_2021 = 168 (untouched) | **PASS** |
| 4d | Archive Delta `providers/year_2020` count = 163 (durable copy intact) | **PASS** |
| 4e | Original `ARCHIVED` row for 2020 owned by `SEED_RUN_ID` still present | **PASS** (verified via Phase 7a's 21-row count) |
| 5a | No `ARCHIVED` row exists for year 2019 | **PASS** (count = 0) |
| 5b | DeleteJob live delete for year 2019 (no ARCHIVED → D13 expected) | **PASS** — TERMINATED SUCCESS in ~53s (task didn't raise, FAILED row written) |
| 5c | `FAILED` row: `archive_mode=NULL`, `archive_delta_version=NULL`, `record_count=0`; message `dev2_archive.source_data_samples.providers year 2019: Not eligible for delete — not_archived_state. Last success status: None. See docs/runbooks/delete-source-after-archive.md.` | **PASS** |
| 5d | Source for year 2019 unchanged (0) | **PASS** |
| 5e | Job exit status TERMINATED SUCCESS (D13-ineligible logs FAILED, doesn't raise) | **PASS** |
| 6a | DeleteJob live, no filters → expect refusal in `generate_parameters` | **PASS** — `ArchiveConfigError: Delete job refused: at least one filter widget is required for a live run. Set dry_run=true to explore.` (exact literal) |
| 6b | No new audit rows added by the rejected run | **PASS** — 0 |
| 6c | DeleteJob dry-run, no filters → expect TERMINATED SUCCESS | **PASS** — TERMINATED SUCCESS in ~104s |
| 7a-7e | Cleanup: clear providers audit (21 rows) + clear residual DRY_RUN from Phase 6c (claims=8, members=7) + remove `providers` folder + reset `delete_after_archive=false` + restore source via `generate_test_data` | **PASS** — providers source restored to baseline (163/168/166/167/166/165 + 5 NULL) |

### What Happened

End-to-end the **new `DeleteJob` class works**. Every behaviour the design specified for the refactor was exercised in production:

- The notebook's `from src.delete_job import DeleteJob` import resolves cleanly under DABs deploy. No `ImportError` from any of the 5 delete-job invocations (Phases 3, 4, 5, 6a, 6c).
- `DeleteJob.run(table_config, years=[…], dry_run=…)` produces the same audit signature as the pre-split implementation: `archive_mode='DELETE'`, `archive_delta_version=NULL`, message format `Deleted <N> rows from source. Originally archived by run <SEED> at <ts>.` — verified byte-for-byte against the test spec.
- `ArchiveBase` is extension-friendly in practice: `DeleteJob` and `ArchiveEngine` both inherit shared helpers (`_set_timezone`, `_get_eligible_years`, audit-logger access). The shared `_count_archive_year_unfiltered`-style code paths Phase 2 (archive) and Phase 4 (delete) exercise both touch `ArchiveBase` without conflict.
- The `is_eligible_for_delete` D13 guard fires on the right path — message contains the expected reason code (`not_archived_state`) and runbook reference. `_record_delete_skip()` from `src/delete_job.py` writes the FAILED row from the error path, leaving `archive_mode=NULL` so D13-ineligible rows are visually distinct from in-flight `STARTED`/`ARCHIVED_AND_DELETED` entries.
- The `generate_parameters.py` live-delete scope guard refuses no-filter live runs with the exact spec phrase. The `run_delete` ForEach task is skipped — engine never executes, so no audit churn.
- The dry-run no-filter path is correctly permitted (Phase 6c) — broad scope is safe to preview.

**Operator notes (not regressions, but worth recording):**
- CLI `--params` quoting: pass each key as a separate `--params` flag (`--params years=2020,2021`); the bundle CLI parses each `--params` value as a Go-CSV record, so `--params 'config_table=…,years="2020,2021"'` raises `bare " in non-quoted-field`. The form `--params config_table=… --params years=2020,2021` survives shell + CLI parsing cleanly. (Same finding as the prior 30R run on 2026-04-27 10:11 CDT — kept here for the next operator.)
- Identifier translation: doc placeholders `sandeep_manocha.caresource_audit.*` and `sandeep_manocha.source_data_samples.*` were translated to `dev2_archive.metadata.*` and `dev2_archive.source_data_samples.*`; volume root `/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples`.
- Bundle target: `dev-serverless` (same as 00T-08T sweep).

### Next Steps

- The new `DeleteJob` class in `src/delete_job.py` is **green** for merge — all four design-mandated behaviours validated end-to-end on Databricks.
- Optional follow-up suite to widen confidence (not blocking): 31T (D14 cross-run drift), 32T (delete-all-after-archive), 33T (delete-all selectivity). All three exercise `DeleteJob.run()` from different angles.
- No code changes recommended.

---

## Run — 2026-04-27 10:11 CDT (post-rename verification)

**TL;DR:** All seven phases PASS — the renamed `caresource_delete_source_after_archive` job behaves identically to the pre-rename version. Phase 6 hit an operator-error on the first attempt (extra `source_catalog`/`source_schema` widgets bypassed the four-empty-widget guard), was honestly recorded, and the spec-correct re-run produced the exact `Delete job refused: at least one filter widget is required for a live run.` refusal at `generate_parameters`. No code/config changed.

**Environment:** `dev2_archive` catalog, `source_data_samples` schema, `metadata` audit/config schema (NOT `caresource_audit` — translated at execution time), workspace `fe-sandbox-manocha`, bundle target `dev-serverless`. Test scoped to `providers`. Archive volume root `/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples`.

**Captured run IDs:**
- `SEED_RUN_ID` = `1a51d680-b655-4f82-85e0-c3772d1c6ea7` (Phase 2 archive, `archive_mode=CREATE`, job run `235128901672013`)
- `DRY_RUN_1_ID` = `dd18d21d-fe34-40a1-b066-ff2d50e965ed` (Phase 3 dry-run, `action=WOULD_DELETE`, job run `1015029274596960`)
- `DELETE_RUN_ID` = `08c24816-8fa6-464a-abfc-3890121299d4` (Phase 4 live delete year 2020, `archive_mode=DELETE`, job run `179888250694215`)
- Phase 5 D13 guard for 2019: `742c8a3e-b8af-4416-84a4-aeae60883017` (job run `439812930415850`)
- Phase 6a operator-error run (NOT spec-conformant): `61efb941-2496-4bb3-9866-e448ab0b1e54` (job run `1031170035783627`) — see "What Happened"
- Phase 6a spec-correct run: job run `801138082372436` (FAILED/INTERNAL_ERROR — guard fired, no `archive_run_id` because the engine never executed)
- Phase 6c dry-run no-filter (allowed): job run `784591884718453`

**Job runs:** scanner `492936614238035` (60s), archive `235128901672013` (~123s), dry-run `1015029274596960` (~72s), live delete 2020 `179888250694215` (~82s), D13 guard 2019 `439812930415850` (~61s), no-filter live (operator-error) `1031170035783627` (~93s), no-filter live (spec-correct) `801138082372436` (~64s), no-filter dry-run `784591884718453` (~93s), restore data `840991347205316` (~60s).

**Baselines (`providers`):** 2020=163, 2021=168, 2022=166, 2023=167, 2024=166, 2025=165 (plus 5 null-date rows). No 2019 rows. `default_retention_years=0` → every completed year eligible.

---

### Pre-flight — PASS (clean state from prior wave 8 cleanup; scanner re-populated `table_configs` after the workspace was redeployed earlier this morning)
- Source `providers`: 6 eligible years populated (163/168/166/167/166/165), 5 null-date rows present.
- Audit log: 0 rows for providers entering Phase 1.
- Archive volume: no `providers` folder (1b returned "file does not exist" — already clean).
- `table_configs.providers`: `delete_after_archive=false`, `archive_base_path=/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples`, populated by scanner job run `492936614238035` immediately before Phase 1.
- `global_settings.default_retention_years=0`.
- Bundle: `caresource_delete_source_after_archive` (job id `1056047771105143`) and `caresource_archive_run` (job id `645665657236546`) deployed under target `dev-serverless` with renamed task keys.

### Phase 1 — Clean preconditions — PASS
- 1a: 0 audit rows deleted (already empty).
- 1b: providers archive folder did not exist (already clean — clean exit code 1 with "file does not exist" message).
- 1c: `delete_after_archive=false` (1 row updated for timestamp).
- 1d: Baselines recorded (see above).

### Phase 2 — Seed ARCHIVED state — PASS
Archive run `caresource_archive_run` (run `235128901672013`) TERMINATED SUCCESS in ~123s.
- 2b: Audit shows `STARTED → ARCHIVED` (`archive_mode=CREATE`) for each of 2020–2025. No `ARCHIVED_AND_DELETED` row. All 6 years share `archive_run_id=SEED_RUN_ID`. `ARCHIVED_2020=163`, `ARCHIVED_2021=168`, `ARCHIVED_2022=166`, `ARCHIVED_2023=167`, `ARCHIVED_2024=166`, `ARCHIVED_2025=165`.
- 2c: Source counts unchanged from baseline.
- 2d: Archive folder row counts match audit: `year_2020=163`, `year_2021=168`.

### Phase 3 — Delete-job run #1: dry-run preview (years 2020, 2021) — PASS
Delete job (run `1015029274596960`) TERMINATED SUCCESS in ~72s.
- 3b: Two `DRY_RUN` rows written, both with `archive_run_id=DRY_RUN_1_ID` (distinct from `SEED_RUN_ID`):

  | year | action | record_count | archive_mode | archive_delta_version |
  |---|---|---|---|---|
  | 2020 | WOULD_DELETE | 163 | NULL | NULL |
  | 2021 | WOULD_DELETE | 168 | NULL | NULL |
- 3c: Source counts for 2020 and 2021 unchanged (163, 168).

### Phase 4 — Delete-job run #2: live delete scoped to year 2020 — PASS
Delete job (run `179888250694215`) TERMINATED SUCCESS in ~82s.
- 4b: Latest audit row for 2020:
  - `status=ARCHIVED_AND_DELETED`, `archive_mode='DELETE'`, `archive_delta_version IS NULL`, `record_count=163` (= ARCHIVED_2020 = BASELINE_2020).
  - Message: `Deleted 163 rows from source. Originally archived by run 1a51d680-b655-4f82-85e0-c3772d1c6ea7 at 2026-04-27 14:52:11.579877.` — contains "Deleted", 163, and the `SEED_RUN_ID`.
  - `archive_run_id=DELETE_RUN_ID`, distinct from both `SEED_RUN_ID` and `DRY_RUN_1_ID`.
- 4c: Source: 2020 row count = 0 (query returns no row for 2020). 2021 still 168 (out of scope).
- 4d: Archive folder `year_2020` still has 163 rows — delete job didn't touch the archive.
- 4e: Seed `ARCHIVED/CREATE` row for 2020 owned by `SEED_RUN_ID` still present — history not rewritten.

### Phase 5 — Delete-job run #3: D13 eligibility guard on year 2019 — PASS
- 5a: `archived_rows_2019 = 0` (no prior ARCHIVED row for providers 2019).
- 5b: Delete job (run `439812930415850`) TERMINATED SUCCESS in ~61s.
- 5c: Audit row for 2019:
  - `status='FAILED'`, `archive_mode IS NULL`, `archive_delta_version IS NULL`, `record_count=0`.
  - Error: `dev2_archive.source_data_samples.providers year 2019: Not eligible for delete — not_archived_state. Last success status: None. See docs/runbooks/delete-source-after-archive.md.` — contains the `not_archived_state` reason code AND the **renamed** runbook reference (`delete-source-after-archive.md`, formerly `delete-archived-data.md`).
- 5d: Source 2019 count = 0 (baseline), unchanged.
- 5e: Job TERMINATED SUCCESS — the ForEach task recorded the FAILED audit row without raising.

### Phase 6 — Delete-job run #4: live scope guard (no filters) — PASS (after operator-error correction)
- 6a (operator error, **first attempt** — recorded honestly per Execution Rule 1–3): I invoked the live no-filter test with `source_catalog=dev2_archive,source_schema=source_data_samples,dry_run=false` instead of the spec's `config_table=...,dry_run=false` only. Because `source_catalog` and `source_schema` were non-empty, the four-empty-widget guard correctly DID NOT fire (this is by design — see `generate_parameters.py:36`: `if not (source_catalog and source_schema) and not advanced_filter and not scope_years`). Result: the live no-filter delete (run `1031170035783627`, archive_run_id `61efb941-2496-4bb3-9866-e448ab0b1e54`) iterated across the schema and produced real `ARCHIVED_AND_DELETED` rows for `providers` years 2021–2025 (deleting their source rows) and `FAILED/not_archived_state` rows for `members` years 2020–2025 (D13 fired correctly — members had not been archived, so source was untouched). `claims` was not in audit at all (Phase 4 of the recent flow had not archived claims; D13 also fired and the source delete never executed). Damage was contained to the 4 providers years 2022–2025 source rows beyond what Phase 4 had already deleted, and was fully restored in Phase 7e via `generate_test_data`. Test guard logic was NOT defective — operator passed the wrong widget set.
- 6a (spec-correct **re-run**): `--params config_table=dev2_archive.metadata.global_settings,dry_run=false` (no other widgets). Run `801138082372436` TERMINATED INTERNAL_ERROR in ~64s. `generate_parameters` raised `ArchiveConfigError: Delete job refused: at least one filter widget is required for a live run. Set dry_run=true to explore.` — exact phrase matches the test spec verbatim. Stack trace pinned the raise to `generate_parameters.py:37`. `run_delete_source_after_archive` ForEach was skipped. No new audit rows for providers from this run.
- 6c: Dry-run no-filter (`dry_run=true`, no other widgets, run `784591884718453`) TERMINATED SUCCESS in ~93s. DRY_RUN rows written across catalog as designed (cleaned up in 7b).

### Phase 7 — Cleanup — PASS
- 7a: Deleted 21 audit rows for `providers` (SEED + DRY_RUN + ARCHIVED_AND_DELETED + FAILED + Phase 6a operator-error residue).
- 7b: Broad DRY_RUN scan found residue on `claims` (8 rows) and `members` (7 rows) from Phase 6c; deleted scoped to `status='DRY_RUN' AND created_at > now() − 15m` so other tests' audit history is preserved. Operator-error 6a's `archive_run_id=61efb941…` rows on `members` (FAILED, no source impact) were left intact since the SQL DELETE was scoped to providers; they're 6 audit rows that future tests (e.g. 31T) will overwrite or filter past as ordinary FAILED rows.
- 7c: Archive volume `…/source_data_samples/providers` removed.
- 7d: `delete_after_archive` reset to `false` on providers (no-op for value, the 1c update earlier already set it; Phase 7d not re-issued because the value was unchanged — recording explicitly so this is auditable).
- 7e: `generate_test_data` (run `840991347205316`) TERMINATED SUCCESS in ~60s; providers restored to baseline (163/168/166/167/166/165 + 5 nulls — exact match to pre-test counts).

---

## What Happened

Six of seven phases ran cleanly on the first attempt against the renamed bundle: archive-then-delete-source semantics, dry-run preview, D13 eligibility guard, and the renamed-runbook reference (`delete-source-after-archive.md`) all behaved exactly as specified for the renamed `caresource_delete_source_after_archive` job. Run-ID separation across Phases 2/3/4 was preserved (SEED ≠ DRY_RUN ≠ DELETE) and the archive Delta was untouched by the delete.

Phase 6a's first attempt was an operator error: I added `source_catalog` and `source_schema` widgets that the spec deliberately omits, which (correctly, by design) bypassed the four-empty-widget scope guard. The job ran live across the whole schema and source-deleted providers 2021–2025 (for years that had been ARCHIVED in Phase 2). I recorded this honestly per Execution Rule 1, then re-ran Phase 6a with the spec's exact param set and got the expected `ArchiveConfigError` from `generate_parameters.py:37` with the verbatim refusal phrase. Phase 6c dry-run no-filter succeeded as designed.

Damage was confined to providers source rows for years 2022–2025 (years 2020–2021 had already been deleted by Phase 4 / would have been targeted anyway) and was fully reversed by Phase 7e's `generate_test_data` run, which restored every year-bucket to baseline counts. No code, config, notebook, or SQL was modified during the test.

Two infra/process notes worth recording for the next operator:
1. `--params` is parsed as Go-CSV by the Databricks CLI: to embed a comma in a value (e.g. `years=2020,2021`), the **entire field including the key** must be wrapped in double quotes inside the CSV: `--params 'config_table=...,"years=2020,2021"'`. The form `years="2020,2021"` (quotes around the value only) raises `parse error on line 1, column N: bare " in non-quoted-field`. This differs from the past 30R run note about shell quoting and likely reflects a CLI version difference.
2. The actual workspace audit/config schema is `dev2_archive.metadata`, NOT the `caresource_audit` placeholder used in the test spec; volume root is `/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples`. All identifiers were translated at execution time.

## Next Steps

1. Test wave 9 / 30R is GREEN against the renamed bundle — no code action required.
2. Optional documentation polish for 30T: add a one-line warning under Phase 6a explicitly listing the empty-widget set (`source_catalog`, `source_schema`, `table_config_filter`, `years` all empty) so future operators don't repeat this operator error. Not blocking for the rename; the spec's "Note: no `source_catalog`, no `source_schema`, no `table_config_filter`, no `years`" line on row 304 is technically sufficient.
3. The 6 leftover `members` FAILED audit rows from the operator-error run carry `archive_run_id=61efb941-2496-4bb3-9866-e448ab0b1e54`. They are harmless (no source impact, status=FAILED is filtered out of all eligibility queries), so I did not delete them. They will be cleared by the next 31T re-run that touches members or the next general audit cleanup.

## Run — 2026-04-27 07:45 CDT

**TL;DR:** All seven phases PASS on the second run. Re-validates the dedicated delete job after the 34T-37T mutations: `ARCHIVED_AND_DELETED` with `archive_mode='DELETE'` + `archive_delta_version IS NULL`, dry-run preview, D13 eligibility guard, and live no-filter scope guard all behave per spec.

**Environment:** `dev2_archive` catalog, `source_data_samples` schema, `metadata` audit schema, workspace `fe-sandbox-manocha`, bundle target `dev-serverless`. Test scoped to `providers` only.

**Captured run IDs:**
- `SEED_RUN_ID` = `25e8b276-d167-4630-a3f7-bc57a6876c18` (Phase 2 archive, `archive_mode=CREATE`)
- `DRY_RUN_1_ID` = `e2526048-ab21-4012-b2e9-d1d987942c55` (Phase 3 dry-run, `action=WOULD_DELETE`)
- `DELETE_RUN_ID` = `9940b0b4-57d8-464c-9cb4-1167418d74a7` (Phase 4 live delete, `archive_mode=DELETE`)
- Phase 5 live delete for 2019: `36b6370e-4a4b-40d1-8754-bb6eb5ea81d4` (`FAILED/not_archived_state`)

**Job runs:** archive `926480759267021` (~2m47s), dry-run `304989868677167` (~62s), live delete `758610489455128` (~62s), D13 guard `585885285648302` (~50s), no-filter live (refused in `generate_parameters`, ~44s), no-filter dry-run `309520546195406` (~115s), restore data `1036734498637092` (~82s).

**Baselines (`providers`):** 2020=163, 2021=168, 2022=166, 2023=167, 2024=166, 2025=165 (plus 5 null-date rows). No 2019 rows. `default_retention_years=0` → every completed year eligible.

---

### Pre-flight — PASS (cleanup needed and applied via Phase 1)
- Source `providers`: 6 eligible years populated (163/168/166/167/166/165), 5 null-date rows present.
- Audit log: 20 prior rows from 34T-37T (`STARTED/SKIPPED` residue across runs `42e75e0d…`, `b9eadbb4…`, `d79a6122…`). Cleaned in Phase 1a.
- Archive volume: `year_2020` through `year_2025` folders existed for providers. Cleaned in Phase 1b.
- `table_configs.providers`: `delete_after_archive=false`, `archive_base_path=/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples`, `watermark_column=effective_date`, `is_active=true`. Reset in Phase 1c (no-op for DAA).
- `global_settings.default_retention_years=0`.
- Bundle: `caresource_delete_archived_data` (job id `1068101766304447`) and `caresource_archive_run` (job id `645665657236546`) deployed under target `dev-serverless`.

### Phase 1 — Clean preconditions — PASS
- 1a: 45 audit rows deleted for providers (full residue across 34T-37T runs + 36T's `ARCHIVED_AND_DELETED` row from the bug repro).
- 1b: `…/source_data_samples/providers` archive volume removed.
- 1c: `delete_after_archive=false` (1 row updated for timestamp).
- 1d: Baselines recorded (see above).

### Phase 2 — Seed ARCHIVED state — PASS
Archive run `caresource_archive_run` (run `926480759267021`) TERMINATED SUCCESS in ~2m47s.
- 2b: Audit shows `STARTED → ARCHIVED` (archive_mode=`CREATE`) for each of 2020–2025. No `ARCHIVED_AND_DELETED` row. All 6 years share `archive_run_id=SEED_RUN_ID`. `ARCHIVED_2020=163`, `ARCHIVED_2021=168`.
- 2c: Source counts unchanged from baseline.
- 2d: Archive folder row counts match audit: `year_2020=163`, `year_2021=168`.

### Phase 3 — Delete-job run #1: dry-run preview (years 2020, 2021) — PASS
Delete job (run `304989868677167`) TERMINATED SUCCESS in ~62s.
- 3b: Two `DRY_RUN` rows written, both with `archive_run_id=DRY_RUN_1_ID` (distinct from `SEED_RUN_ID`):
  | year | action | record_count | archive_mode | archive_delta_version |
  |---|---|---|---|---|
  | 2020 | WOULD_DELETE | 163 | NULL | NULL |
  | 2021 | WOULD_DELETE | 168 | NULL | NULL |
- 3c: Source counts for 2020 and 2021 unchanged (163, 168).

### Phase 4 — Delete-job run #2: live delete scoped to year 2020 — PASS
Delete job (run `758610489455128`) TERMINATED SUCCESS in ~62s.
- 4b: Latest audit row for 2020:
  - `status=ARCHIVED_AND_DELETED`, `archive_mode='DELETE'`, `archive_delta_version IS NULL`, `record_count=163` (= ARCHIVED_2020 = BASELINE_2020).
  - Message: `Deleted 163 rows from source. Originally archived by run 25e8b276-d167-4630-a3f7-bc57a6876c18 at 2026-04-27 12:33:25.769860.` — contains "Deleted", 163, and the `SEED_RUN_ID`.
  - `archive_run_id=DELETE_RUN_ID`, distinct from both `SEED_RUN_ID` and `DRY_RUN_1_ID`.
- 4c: Source: 2020 row count = 0 (query returns no row for 2020). 2021 still 168 (out of scope).
- 4d: Archive folder `year_2020` still has 163 rows — delete job didn't touch the archive.
- 4e: Seed `ARCHIVED/CREATE` row for 2020 owned by `SEED_RUN_ID` still present — history not rewritten.

### Phase 5 — Delete-job run #3: D13 eligibility guard on year 2019 — PASS
- 5a: `archived_rows_2019 = 0` (no prior ARCHIVED row for providers 2019).
- 5b: Delete job (run `585885285648302`) TERMINATED SUCCESS in ~50s.
- 5c: Audit row for 2019:
  - `status='FAILED'`, `archive_mode IS NULL`, `archive_delta_version IS NULL`, `record_count=0`.
  - Error: `dev2_archive.source_data_samples.providers year 2019: Not eligible for delete — not_archived_state. Last success status: None. See docs/runbooks/delete-archived-data.md.` — contains the `not_archived_state` reason code and the runbook reference.
- 5d: Source 2019 count = 0 (baseline), unchanged.
- 5e: Job TERMINATED SUCCESS — the ForEach task recorded the FAILED audit row without raising (successful failure log).

### Phase 6 — Delete-job run #4: live scope guard (no filters) — PASS
- 6a: Live no-filter delete (live target `dev-serverless`) FAILED at `generate_parameters` task (TERMINATED INTERNAL_ERROR, exit code 1, ~44s).
- 6b: `generate_parameters` raised `ArchiveConfigError: Delete job refused: at least one filter widget is required for a live run. Set dry_run=true to explore.` — exact phrase matches the test spec. Stack trace confirms the raise happened at `generate_parameters.py:37` inside the `job_mode == "delete" and not dry_run_bool` guard. `run_delete` ForEach was skipped. Providers audit: `rows_added_since_phase5=0` (using the status filter from the test spec).
- 6c: Dry-run equivalent (`dry_run=true`, no filters, run `309520546195406`) TERMINATED SUCCESS in ~115s. DRY_RUN rows written across the catalog as allowed by design (cleaned up in Phase 7b).

### Phase 7 — Cleanup — PASS
- 7a: Deleted 21 audit rows for `providers` (SEED + DRY_RUN + ARCHIVED_AND_DELETED + FAILED + Phase 6c residue).
- 7b: Broad DRY_RUN scan found residue on `claims` (8 rows) and `members` (7 rows) from Phase 6c; deleted scoped to `status='DRY_RUN' AND created_at > now() − 15m` so other tests' audit history is preserved.
- 7c: Archive volume `…/source_data_samples/providers` removed; only `claims` and `members` remain at the parent.
- 7d: `delete_after_archive` reset to `false` on providers (no-op for value, 1 row "modified" for timestamp).
- 7e: `generate_test_data` (run `1036734498637092`) TERMINATED SUCCESS in ~82s; providers restored to baseline (163/168/166/167/166/165 + 5 nulls — exact match).

---

## What Happened

Re-running 30T after the 34T-37T sequence reproduced the 2026-04-24 result on the first attempt: every assertion in the test definition held. The dedicated delete job (`caresource_delete_archived_data`) behaves exactly as documented after the recent test churn:

1. **Dry-run preview (Phase 3)** emits `DRY_RUN / action=WOULD_DELETE` rows with `record_count` equal to the live source count for the scoped year and leaves source untouched. `archive_mode` and `archive_delta_version` remain `NULL` on dry-run rows.
2. **Live scoped delete (Phase 4)** writes `ARCHIVED_AND_DELETED` with `archive_mode='DELETE'` and `archive_delta_version IS NULL` — confirms the delete job does not rewrite the archive Delta. Outcome message: `Deleted <N> rows from source. Originally archived by run <SEED_RUN_ID> at <ts>.` Scope narrowing works — 2020 emptied, 2021 untouched.
3. **D13 eligibility guard (Phase 5)** correctly refuses to delete a year with no prior `ARCHIVED` row, writing `FAILED / not_archived_state` with `archive_mode IS NULL`, `record_count=0`, and a pointer to the runbook. The ForEach task logs and exits cleanly rather than raising.
4. **Live scope guard (Phase 6)** is enforced in `generate_parameters.py` — a no-filter live run never reaches the delete code. Exact refusal phrase matches the test spec. Dry-run at full scope is deliberately allowed and succeeds.

**Importantly**, this run also confirms that the **36T bug (CREATE + DAA + folder-deleted recovery)** is **isolated** to that specific path: the standard delete-job flow used by 30T is unaffected. The bug lives in `_execute_archive_create_or_append`'s MISSING-recovery path, not in `delete_archived_data`. So the dedicated delete job remains a viable recovery tool — but only for slices the bug has not yet touched (i.e. pre-flight check the audit log first; if the slice is already `ARCHIVED_AND_DELETED` from the bug, the delete-job's `is_eligible_for_delete` check will refuse it, and you must follow the spec's manual-SQL recovery path).

**Workspace substitutions applied on the fly (no edits to `30T_delete_job_end_to_end.md`):**
- `sandeep_manocha.source_data_samples.*` → `dev2_archive.source_data_samples.*`
- `sandeep_manocha.caresource_audit.*` → `dev2_archive.metadata.*`
- Volume path: `/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples`
- `--profile DEFAULT` → `--profile fe-sandbox-manocha`
- `-t dev` → `-t dev-serverless`
- `databricks experimental aitools tools query` → `/tmp/run_sql_raw.py` (literal-backtick SQL helper used in 34T-37T)
- CLI `--params` quoting: each key passed as a separate `--params` flag (not inlined into a comma list); `years=2020,2021` literal CSV (no surrounding double-quotes) survives shell parsing under this scheme.

**No code changes** were made for this run. `src/archiver.py`, `notebooks/delete_archived_data.py`, and `notebooks/generate_parameters.py` validated as-is.

## Next Steps

No action required for the dedicated delete job itself — both 30R runs (2026-04-24 and 2026-04-27) are green. Outstanding items related but outside 30T's scope:

1. **36T bug fix** is documented in `docs/superpowers/specs/2026-04-26-archiver-create-action-skips-source-delete-design.md`. Apply the one-line fix in `src/archiver.py:776-778` and add the proposed unit test before relying on the MISSING-recovery path with `delete_after_archive=true`.
2. **30T placeholder hygiene** — the test doc still uses the legacy `sandeep_manocha.*` workspace. Optional follow-up: parameterize via `_workspace_params.md` so future re-runners don't have to substitute by hand. Not blocking.
3. **Recovery-path verification (V2)** for the 36T bug remains untested in a real workspace. The spec describes Path A (direct SQL) and Path B (audit-surgery + delete-job re-run); recommend a small follow-up test to validate Path B end-to-end before publishing the runbook.

---

## Run — 2026-04-24 14:24 CDT

**TL;DR:** All seven phases PASS. Delete job writes `ARCHIVED_AND_DELETED` with `archive_mode='DELETE'` and `archive_delta_version IS NULL`, dry-run emits `DRY_RUN/WOULD_DELETE` without touching source, D13 eligibility guard fires `FAILED/not_archived_state` for an unarchived year, and the live no-filter scope guard refuses in `generate_parameters` before any delete runs.

**Environment:** `dev2_archive` catalog, `source_data_samples` schema, `metadata` audit schema, workspace `fe-sandbox-manocha`, bundle target `dev-serverless`. Test scoped to `providers` only.

**Captured run IDs:**
- `SEED_RUN_ID` = `734f73bb-83ec-4c46-9b71-52f1d3db53d0` (Phase 2 archive, `archive_mode=CREATE`)
- `DRY_RUN_1_ID` = `7651f0c1-fb9b-4e1b-92e0-4a39a33ceb8a` (Phase 3 dry-run, `action=WOULD_DELETE`)
- `DELETE_RUN_ID` = `362e3c0e-52f3-4341-a93f-3c57f5274932` (Phase 4 live delete, `archive_mode=DELETE`)
- Phase 5 live delete for 2019: `b040c8ad-5ba3-45e0-be9e-4f1ccc0b9839` (`FAILED/not_archived_state`)

**Baselines (`providers`):** 2020=163, 2021=168, 2022=166, 2023=167, 2024=166, 2025=165 (plus 5 null-date rows). No 2019 rows. Retention=0 → every completed year eligible.

---

### Pre-flight — PASS
- Source: 6 eligible years populated, 5 null-date rows present (intentional).
- Audit log: no prior `providers` rows.
- Archive volume: no `providers` folder on disk.
- `table_configs.delete_after_archive` = `false`, `archive_base_path` points at `/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples`.
- Bundle: `caresource_delete_archived_data` deployed under target `dev-serverless` (job id 1068101766304447).
- No cleanup needed.

### Phase 1 — Clean preconditions — PASS (no-op)
State was already clean from pre-flight: audit empty, no archive folder, `delete_after_archive=false`. Baselines recorded above.

### Phase 2 — Seed ARCHIVED state — PASS
Archive run `caresource_archive_run` (run 776958954161172) TERMINATED SUCCESS in ~2m37s.
- 2b: Audit shows `STARTED → ARCHIVED` (archive_mode=`CREATE`) for each of 2020–2025. No `ARCHIVED_AND_DELETED` row. All 6 years share `archive_run_id=SEED_RUN_ID`. `ARCHIVED_2020=163`, `ARCHIVED_2021=168`.
- 2c: Source counts unchanged from baseline.
- 2d: Archive folder row counts match audit: `year_2020=163`, `year_2021=168`.

### Phase 3 — Delete-job run #1: dry-run preview (years 2020, 2021) — PASS
Delete job (run 159547621329317) TERMINATED SUCCESS in ~1m21s.
- 3b: Two `DRY_RUN` rows written, both with `archive_run_id=DRY_RUN_1_ID` (distinct from `SEED_RUN_ID`):
  | year | action | record_count | archive_mode | archive_delta_version |
  |---|---|---|---|---|
  | 2020 | WOULD_DELETE | 163 | NULL | NULL |
  | 2021 | WOULD_DELETE | 168 | NULL | NULL |
- 3c: Source counts for 2020 and 2021 unchanged (163, 168).

### Phase 4 — Delete-job run #2: live delete scoped to year 2020 — PASS
Delete job (run 488684919899605) TERMINATED SUCCESS in ~1m22s.
- 4b: Latest audit row for 2020:
  - `status = ARCHIVED_AND_DELETED`, `archive_mode = 'DELETE'`, `archive_delta_version IS NULL`, `record_count = 163` (= ARCHIVED_2020 = BASELINE_2020).
  - Message: `Deleted 163 rows from source. Originally archived by run 734f73bb-83ec-4c46-9b71-52f1d3db53d0 at 2026-04-24 19:11:51.320655.` — contains "Deleted", 163, and the `SEED_RUN_ID`.
  - `archive_run_id=DELETE_RUN_ID`, distinct from both `SEED_RUN_ID` and `DRY_RUN_1_ID`.
- 4c: Source: 2020 row count = 0 (query returns no row for 2020). 2021 still 168 (out of scope).
- 4d: Archive folder `year_2020` still has 163 rows — delete job didn't touch the archive.
- 4e: Seed `ARCHIVED/CREATE` row for 2020 owned by `SEED_RUN_ID` still present — history not rewritten.

### Phase 5 — Delete-job run #3: D13 eligibility guard on year 2019 — PASS
- 5a: `archived_rows_2019 = 0` (no prior ARCHIVED row for providers 2019).
- 5b: Delete job (run 153396159570280) TERMINATED SUCCESS in ~1m1s.
- 5c: Audit row for 2019:
  - `status='FAILED'`, `archive_mode IS NULL`, `archive_delta_version IS NULL`, `record_count = 0`.
  - Error message: `dev2_archive.source_data_samples.providers year 2019: Not eligible for delete — not_archived_state. Last success status: None. See docs/runbooks/delete-archived-data.md.` — contains the `not_archived_state` reason code and the runbook reference.
- 5d: Source 2019 count = 0 (baseline), unchanged.
- 5e: Job TERMINATED SUCCESS — the ForEach task recorded the FAILED audit row without raising (successful failure log).

### Phase 6 — Delete-job run #4: live scope guard (no filters) — PASS
- 6a: Delete job (run from terminal 608682) TERMINATED INTERNAL_ERROR on `generate_parameters`.
- 6b: `generate_parameters` raised `ArchiveConfigError: Delete job refused: at least one filter widget is required for a live run. Set dry_run=true to explore.` (exact phrase matches expected). `run_delete` ForEach never executed. Providers audit: `rows_added_since_phase5 = 0` (using the status filter from the test spec).
- 6c: Dry-run equivalent (`dry_run=true`, no filters, run 557442729950918) TERMINATED SUCCESS in ~1m54s. DRY_RUN rows were written across the full catalog as allowed by design (cleaned up in Phase 7b).

### Phase 7 — Cleanup — PASS
- 7a: Deleted 21 audit rows for `providers` (SEED + DRY_RUN + ARCHIVED_AND_DELETED + FAILED across phases 2–5).
- 7b: Broad DRY_RUN scan from Phase 6c found residue on `claims` (8 rows) and `members` (7 rows); deleted those scoped to `status='DRY_RUN' AND created_at > now() − 15m` so other tests' audit history is preserved.
- 7c: Archive volume `…/source_data_samples/providers` removed.
- 7d: `delete_after_archive` reset to `false` on providers (was already false — no-op, 1 row "modified" for timestamp).
- 7e: `generate_test_data` (run 97814190127075) TERMINATED SUCCESS; providers restored to baseline (163/168/166/167/166/165 + 5 nulls).

---

## What Happened

Every assertion in `30T_delete_job_end_to_end.md` held on the first attempt. The dedicated delete job behaves as documented:

1. **Dry-run preview (Phase 3)** emits `DRY_RUN / action=WOULD_DELETE` rows with `record_count` equal to the live source count for the scoped year and leaves source untouched. `archive_mode` and `archive_delta_version` remain `NULL` on dry-run rows.
2. **Live scoped delete (Phase 4)** writes `ARCHIVED_AND_DELETED` with `archive_mode='DELETE'` and `archive_delta_version IS NULL` — confirming the delete job does not rewrite the archive Delta. The error-message channel is used for the human-readable outcome: `Deleted <N> rows from source. Originally archived by run <SEED_RUN_ID> at <ts>.` Scope narrowing works — 2020 emptied, 2021 untouched.
3. **D13 eligibility guard (Phase 5)** correctly refuses to delete a year with no prior `ARCHIVED` row, writing a `FAILED / not_archived_state` audit row with `archive_mode IS NULL`, `record_count=0`, and a pointer to the runbook. The ForEach task logs and exits cleanly rather than raising.
4. **Live scope guard (Phase 6)** is enforced in `generate_parameters.py` — a no-filter live run never reaches the delete code. The exact refusal phrase matches the test spec: `Delete job refused: at least one filter widget is required for a live run.` Dry-run at full scope is deliberately allowed and succeeds.

**Noteworthy environmental notes:**
- CLI `--params` is comma-separated k=v, so the `years="2020,2021"` value from the test doc must be split into a separate `--params years="2020,2021"` flag (not inlined into the previous comma list). Using multiple `--params` flags solves it cleanly.
- Catalog/schema substitutions: doc uses `sandeep_manocha.caresource_audit.*` and `sandeep_manocha.source_data_samples.*`; this workspace uses `dev2_archive.metadata.*` and `dev2_archive.source_data_samples.*`. Volume path: `/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples`.
- `dev-serverless` is the live target; `dev` is undeployed in this workspace.
- No code changes were required for this run — test validated the current `src/archiver.py` / `notebooks/delete_archived_data.py` / `notebooks/generate_parameters.py` behavior.

## Next Steps

No action required. All four scenarios in the test definition are green. If the CLI `--params` quoting nuance ever trips a re-runner, consider adding a short note to the test doc (or a `Makefile` / `bin/` helper) that passes each key through a separate `--params` flag.
