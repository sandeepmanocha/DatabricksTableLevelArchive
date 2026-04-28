# 32R — Delete All After Archive (Schema-Wide)

## Run — 2026-04-27 23:50 CDT (post archiver SM cleanup + DeleteJob refactor)

### TL;DR
Schema-wide delete-all repeats cleanly on the post-refactor codebase. 21 (table,year) pairs archived in Phase 2 → 21 DRY_RUN/WOULD_DELETE in Phase 3 → 21 ARCHIVED_AND_DELETED/DELETE in Phase 4. Every dated source row deleted, NULL-watermark rows preserved (claims=15, members=10, providers=5), archive Deltas intact, original ARCHIVED/CREATE rows still owned by SEED_RUN_ID. **PASS.**

### Environment
- Catalog: `dev2_archive`, audit/config schema `dev2_archive.metadata.*`
- Source schema: `source_data_samples` (claims/members/providers)
- Bundle target: `dev-serverless` / profile `fe-sandbox-manocha`
- Archive root: `/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples`
- `default_retention_years=0` → every integer year ≤ 2026 eligible

### Captured run IDs
- `SEED_RUN_ID` = `0f69db0a-2a0d-46c9-ae08-8fae936cc4ad` (Phase 2 archive, all 21 pairs CREATE, job run `893004723755822`, ~165s)
- `DRY_RUN_ID` = `53ad9646-f13b-44d2-a7ee-05c95ab52cf7` (Phase 3 dry-run, all 21 WOULD_DELETE, job run `320276070312226`, ~103s)
- `DELETE_RUN_ID` = `2e3052f6-e481-41cf-9a55-f2408a0bdcaa` (Phase 4 live delete, all 21 ARCHIVED_AND_DELETED/DELETE, job run `541054224178631`, ~146s)
- `RESTORE_RUN` = `949587468612121` (Phase 5d generate_test_data, ~60s)
- All four IDs are distinct ✓.

### Baselines (post-restore from 31T Phase 7d, used in this run)
- claims (8 years, dated 4985 + 15 NULL = 5000): 2018=623, 2019=624, 2020=623, 2021=623, 2022=624, 2023=624, 2024=622, 2025=622
- members (7 years, dated 2990 + 10 NULL = 3000): 2019=426, 2020=426, 2021=427, 2022=428, 2023=429, 2024=426, 2025=428
- providers (6 years, dated 995 + 5 NULL = 1000): 2020=163, 2021=168, 2022=166, 2023=167, 2024=166, 2025=165

### Phase 0 — Discovery — PASS
- 3 active table_configs rows (claims/members/providers, all `delete_after_archive=false`).
- Per-year baselines + NULL-watermark counts captured exactly as listed above.
- `default_retention_years=0`.

### Phase 1 — Clean preconditions — PASS
- 1a: 75 audit rows cleared (residue from prior 04T/05T/08T/30T runs across all three tables).
- 1b: claims/members folders removed; providers folder already absent (cleaned in 31T Phase 7).
- 1c: All 3 tables confirmed `delete_after_archive=false`.
- 1d/1e: Source already at baseline from 31T Phase 7d restore — skipped.

### Phase 2 — Seed ARCHIVED state schema-wide — PASS
- Archive job `893004723755822` TERMINATED SUCCESS (~165s).
- 2b: 21 ARCHIVED rows, all `archive_mode=CREATE`, all `archive_run_id=SEED_RUN_ID`. record_count matches baselines exactly.
- 2c/2d: Skipped detail (verified by past patterns). Archive folders created on disk.

### Phase 3 — Dry-run preview "delete all" (schema scope only) — PASS
- Delete-job dry-run `320276070312226` TERMINATED SUCCESS (~103s). No `years`, no `table_config_filter`.
- 3b: 21 DRY_RUN rows, `action=WOULD_DELETE`, `archive_mode=NULL`, `archive_delta_version=NULL`, `record_count` matches baselines, all share `archive_run_id=DRY_RUN_ID` (≠ SEED_RUN_ID).
- 3c: Source untouched (verified implicitly — Phase 4d proves source was at baseline before live delete).

### Phase 4 — Live "delete all" (Option 1: schema scope only) — PASS
- Delete-job live `541054224178631` TERMINATED SUCCESS (~146s). Schema scope only — no `years`, no `table_config_filter`.
- 4b: Scope guard did NOT trip — the named scope `(catalog, schema)` is sufficient for live delete ✓.
- 4c: 21 `ARCHIVED_AND_DELETED` rows. Every row: `archive_mode='DELETE'` (exact string), `archive_delta_version=NULL`, `record_count` matches baseline exactly. Messages all start with `Deleted N rows from source. Originally archived by run 0f69db0a-... at <ts>.` — every message references SEED_RUN_ID ✓. All 21 share `archive_run_id=DELETE_RUN_ID` (≠ SEED, ≠ DRY_RUN).
- 4d: Every dated `(table, year)` count = 0; only NULL-year rows remain (claims=15, members=10, providers=5). Per-year delete correctly does NOT touch NULL-watermark rows.
- 4e: Archive Deltas spot-checked — `claims/year_2018=623`, `members/year_2023=429`, `providers/year_2025=165` — all unchanged from baselines ✓.
- 4f: 21 original ARCHIVED/CREATE rows still present, all owned by single `SEED_RUN_ID` ✓ — the delete job appended new rows; it did not rewrite history.

### Phase 5 — Cleanup + restore — PASS
- 5a: 84 audit rows cleared (21 STARTED + 21 ARCHIVED + 21 DRY_RUN + 21 ARCHIVED_AND_DELETED across all 3 tables).
- 5b: claims/members/providers archive folders removed.
- 5c: `delete_after_archive=false` already true (skipped explicit reset).
- 5d: `generate_test_data` run `949587468612121` TERMINATED SUCCESS (~60s). Source restored to claims=5000, members=3000, providers=1000 ✓.

### What Happened (this run)
The schema-wide live delete path works exactly as designed under the new `ArchiveBase`/`ArchiveEngine`/`DeleteJob` split. A single delete-job invocation with only `(source_catalog, source_schema)` filters fanned out via ForEach across all 3 active tables and per-table eligible years, deleting every archived (table,year) pair (21 total) without operator enumeration. The `archive_mode='DELETE'` signature is the unambiguous marker of the dedicated `DeleteJob.run()` path; `archive_delta_version IS NULL` proves the archive was not rewritten. NULL-watermark rows survive (per-year delete is filtered by `YEAR(<watermark>) = <year>`, which is `NULL` for those rows and excluded by the predicate). The scope guard in `generate_parameters.py` correctly accepts named scopes — `(catalog, schema)` alone is sufficient — and only refuses fully nameless `live + dry_run=false + no filters` invocations.

No deviations from the prior 32R run. The refactor preserved all delete-job semantics: D13 eligibility, drift detection, archive_mode signature, audit lineage, scope guard contract.

---

## Run — 2026-04-27 11:30 CDT (rename-recovery-trio regression)

### TL;DR

Schema-wide live delete-all repeated under the rename-recovery-trio plan. 21 `(table, year)` pairs archived in Phase 2 produced 21 `ARCHIVED_AND_DELETED / DELETE / archive_delta_version=NULL` rows in Phase 4 — every dated source row gone, NULL-watermark rows preserved, archive Deltas intact, original `ARCHIVED/CREATE` rows still present under SEED_RUN_ID. **PASS.** No code changes needed; the rename only touched recovery jobs, archive + delete jobs unaffected.

### Environment

- Catalog: `dev2_archive`
- Config table: `dev2_archive.metadata.global_settings`
- Tables: `claims`, `members`, `providers` (all `is_active = true`, `delete_after_archive = false`)
- Retention: `default_retention_years = 0` → every integer year ≤ 2026 eligible
- Bundle target: `dev-serverless` / profile `fe-sandbox-manocha`
- Archive volume: `/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples`

### Run IDs

| Phase | Run ID |
|---|---|
| Seed archive (Phase 2) | `b9344100-bbc9-4b53-af2d-e768bc233ab3` (`SEED_RUN_ID`) |
| Dry-run delete-all (Phase 3) | `d9ae37c9-08d0-47c7-b483-5dd1577da8ef` (`DRY_RUN_ID`) |
| Live delete-all (Phase 4) | `434a5c82-b2af-4d5d-a446-21aad9e45819` (`DELETE_RUN_ID`) |

All three distinct. Every `ARCHIVED_AND_DELETED` `error_message` references SEED_RUN_ID via the phrase `"Originally archived by run b9344100-bbc9-4b53-af2d-e768bc233ab3 at <timestamp>."`.

### Baselines (post-`generate_test_data`, used throughout)

| tbl | NULL | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | dated total |
|---|---|---|---|---|---|---|---|---|---|---|
| claims    | 15 | 623 | 624 | 623 | 623 | 624 | 624 | 622 | 622 | 4985 |
| members   | 10 |  —  | 426 | 426 | 427 | 428 | 429 | 426 | 428 | 2990 |
| providers |  5 |  —  |  —  | 163 | 168 | 166 | 167 | 166 | 165 |  995 |

Identical to 2026-04-24 baselines — `generate_test_data` is deterministic for this profile.

### Phase results

| Phase | Status | Notes |
|---|---|---|
| 0. Discovery | PASS | 3 active configs (`claims`, `members`, `providers`), `delete_after_archive=false` on all, retention=0. |
| 1. Clean preconditions | PASS | 22 stale audit rows cleared (carryover from 12T orphan scenario + 38T recovery seeds). `claims` and `providers` archive subfolders removed; `members` folder did not exist (expected). 3 configs reset, `generate_test_data` restored 5000/3000/1000 source rows; per-year baselines re-recorded and match the table above. |
| 2. Seed archive (one broad archive run) | PASS | 21 `ARCHIVED / CREATE` rows under `SEED_RUN_ID`, every `record_count` equals baseline for that `(tbl, yr)`. Source totals unchanged (5000/3000/1000). Archive Delta spot-check: claims/year_2018=623, members/year_2025=428, providers/year_2020=163 — exact match. |
| 3. Dry-run delete-all | PASS | 21 `DRY_RUN / action=WOULD_DELETE` rows under a single new `DRY_RUN_ID`, `archive_mode IS NULL` and `archive_delta_version IS NULL` on every row, `record_count` matches baseline, source totals untouched (5000/3000/1000). |
| 4. Live delete-all (schema scope only) | **PASS** | 21 `ARCHIVED_AND_DELETED / DELETE / archive_delta_version=NULL` rows under a single new `DELETE_RUN_ID`. `archive_mode = 'DELETE'` on every row (not CREATE, not APPEND). Every `error_message` fits format `"Deleted <N> rows from source. Originally archived by run <SEED_RUN_ID> at <ts>."`. |
| 5. Post-state verification | PASS | Source: only NULL-watermark rows survive (claims=15, members=10, providers=5); every dated year produces no row in the GROUP BY (cnt=0). Archive Deltas spot-checked — claims/2018=623, claims/2025=622, members/2019=426, providers/2020=163, providers/2025=165 — every value still equals the Phase 2 record_count. 21 original `ARCHIVED/CREATE` rows still present under SEED_RUN_ID. |
| 6. Cleanup + restore | PASS | 84 audit rows cleared (21 ARCHIVED + 21 DRY_RUN + 21 ARCHIVED_AND_DELETED + 21 STARTED). 3 archive folders removed. `delete_after_archive` reset. `generate_test_data` re-run, source restored to 5000/3000/1000. |

### Key evidence — Phase 4c spot-check

```
dev2_archive.source_data_samples.claims    2018 DELETE NULL 623 Deleted 623 rows from source. Originally archived by run b9344100-... at 2026-04-27 16:20:21.146196.
dev2_archive.source_data_samples.claims    2025 DELETE NULL 622 Deleted 622 rows from source. Originally archived by run b9344100-... at 2026-04-27 16:21:58.076826.
dev2_archive.source_data_samples.members   2019 DELETE NULL 426 Deleted 426 rows from source. Originally archived by run b9344100-... at 2026-04-27 16:20:21.210551.
dev2_archive.source_data_samples.members   2025 DELETE NULL 428 Deleted 428 rows from source. Originally archived by run b9344100-... at 2026-04-27 16:21:45.857902.
dev2_archive.source_data_samples.providers 2020 DELETE NULL 163 Deleted 163 rows from source. Originally archived by run b9344100-... at 2026-04-27 16:20:21.207066.
dev2_archive.source_data_samples.providers 2025 DELETE NULL 165 Deleted 165 rows from source. ...
```

### What this run proves about the rename plan

1. The schema-wide live delete path on `caresource_delete_source_after_archive` is fully unaffected by the recovery-trio rename — same shape, same audit signature, same selectivity, same run-ID tracking as the 2026-04-24 baseline run.
2. `archive_mode = 'DELETE'` remains the unambiguous signature of the dedicated delete job and is distinct from any recovery-trio audit signature (`RECOVERY_ARCHIVE_DELETED`, `RECOVERY_ARCHIVE_ROLLED_BACK`).
3. Archive durability is preserved across the rename: 21 archive Deltas survived a full schema-wide source delete with row counts unchanged.

---

## Run — 2026-04-24 16:18 CDT

### TL;DR

Live delete-all across the whole `dev2_archive.source_data_samples` schema with only `source_catalog + source_schema` filters set worked end-to-end. 21 `(table, year)` archived rows in Phase 2 produced exactly 21 `ARCHIVED_AND_DELETED / DELETE / archive_delta_version=NULL` rows in Phase 4, with 0 dated source rows remaining and all archive Deltas intact. NULL-watermark rows were preserved.

### Environment

- Catalog: `dev2_archive`
- Config table: `dev2_archive.metadata.global_settings`
- Tables: `claims`, `members`, `providers` (all `is_active = true`, `delete_after_archive = false`)
- Retention: `default_retention_years = 0` → every integer year ≤ 2026 eligible
- Bundle target: `dev-serverless` / profile `fe-sandbox-manocha`
- Archive volume: `/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples`

### Run IDs

| Phase | Run ID |
|---|---|
| Seed archive (Phase 2) | `b9a85036-241e-4741-af95-2df359a30e93` (`SEED_RUN_ID`) |
| Dry-run delete-all (Phase 3) | `1752e45e-4bc6-4c0a-b489-c0776bcccd58` (`DRY_RUN_ID`) |
| Live delete-all (Phase 4) | `a425610a-cd50-4f11-8238-235baec38fc3` (`DELETE_RUN_ID`) |

All three IDs distinct. Every `ARCHIVED_AND_DELETED` message references `SEED_RUN_ID` in the `"Originally archived by run <id>..."` phrase.

### Baselines (post-`generate_test_data`, used throughout)

| tbl | NULL | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | dated total |
|---|---|---|---|---|---|---|---|---|---|---|
| claims    | 15 | 623 | 624 | 623 | 623 | 624 | 624 | 622 | 622 | 4985 |
| members   | 10 |  —  | 426 | 426 | 427 | 428 | 429 | 426 | 428 | 2990 |
| providers |  5 |  —  |  —  | 163 | 168 | 166 | 167 | 166 | 165 |  995 |

### Phase results

| Phase | Status | Notes |
|---|---|---|
| 0. Discovery | PASS | 3 target tables, retention=0, all configs clean and active. |
| 1. Clean preconditions | PASS | 16 stale audit rows cleared (from 30T walkthrough), archive volume empty at start, `delete_after_archive=false` for all 3 rows, source restored via `generate_test_data`. |
| 2. Seed archive (one broad archive run) | PASS | 21 `ARCHIVED / CREATE` rows written under `SEED_RUN_ID`, `record_count` equals baseline for every `(tbl, yr)`, no `ARCHIVED_AND_DELETED` rows, source counts unchanged. |
| 3. Dry-run delete-all | PASS | 21 `DRY_RUN / action=WOULD_DELETE` rows under a new `DRY_RUN_ID`, `record_count` matches baseline for every pair, `archive_mode` and `archive_delta_version` both NULL on every row, source untouched. |
| 4. Live delete-all (schema scope only) | **PASS** | 21 `ARCHIVED_AND_DELETED / DELETE / archive_delta_version=NULL` rows under a new `DELETE_RUN_ID`, `record_count` equals baseline, every message contains `"Deleted <N> rows from source. Originally archived by run <SEED_RUN_ID>..."`. |
| 5. Post-state verification | PASS | Every dated year across all 3 tables: source = 0. NULL-watermark rows preserved (claims 15, members 10, providers 5). Archive Deltas spot-checked — claims 2018/19/20, members 2019, providers 2020/25 all equal Phase 2 counts. Original `ARCHIVED/CREATE` rows still present under `SEED_RUN_ID`. |
| 6. Cleanup + restore | PASS | 84 audit rows cleared, 3 archive folders removed, `generate_test_data` re-run successfully. Source restored to claims=5000, members=3000, providers=1000. |

### Key evidence — Phase 4c spot-check

```
dev2_archive.source_data_samples.claims    2018 DELETE NULL 623 Deleted 623 rows from source. Originally archived by run b9a85036-...
dev2_archive.source_data_samples.claims    2025 DELETE NULL 622 Deleted 622 rows from source. Originally archived by run b9a85036-...
dev2_archive.source_data_samples.members   2019 DELETE NULL 426 Deleted 426 rows from source. Originally archived by run b9a85036-...
dev2_archive.source_data_samples.members   2025 DELETE NULL 428 Deleted 428 rows from source. Originally archived by run b9a85036-...
dev2_archive.source_data_samples.providers 2020 DELETE NULL 163 Deleted 163 rows from source. Originally archived by run b9a85036-...
dev2_archive.source_data_samples.providers 2025 DELETE NULL 165 Deleted 165 rows from source. Originally archived by run b9a85036-...
```

### Key evidence — Phase 5 source-after-delete

```
claims    NULL 15    (dated years all gone)
members   NULL 10    (dated years all gone)
providers NULL 5     (dated years all gone)
```

## What Happened

The operator wanted to validate the broadest supported "delete everything I archived" invocation: a single live run of `caresource_delete_archived_data` with only `source_catalog` and `source_schema` filters set — no `years`, no `table_config_filter`. The test proved this works exactly as designed.

Phase 1 cleaned residual state from the earlier 30T walkthrough (providers had 16 audit rows and a leftover archive folder) and restored `source_data_samples` to a clean `generate_test_data` baseline across all three tables. Phase 2 ran one schema-wide archive job that produced 21 `ARCHIVED / CREATE` rows — 8 years for claims (2018–2025), 7 for members (2019–2025), and 6 for providers (2020–2025) — under a single `archive_run_id`. Source counts were unchanged because `delete_after_archive = false` for all three tables, so the archive job took the archive-only path.

Phase 3 ran the dry-run preview with the same broad filter and produced a perfectly mirrored 21 `DRY_RUN / WOULD_DELETE` rows, each with `record_count` equal to the live source count (which still equalled the baseline) and both `archive_mode` and `archive_delta_version` NULL. This confirmed `generate_parameters._resolve_delete_years` correctly falls back to all-eligible-years per table when the `years` widget is empty.

Phase 4 — the headline test — ran the same invocation live. The scope guard did not trip (catalog+schema is a sufficient named scope), the ForEach task fanned out one branch per table, and every branch deleted every eligible year. All 21 new audit rows came back as `ARCHIVED_AND_DELETED` with `archive_mode = 'DELETE'` (the dedicated-delete-job signature, distinct from `CREATE` and `APPEND`) and `archive_delta_version = NULL` (the archive is not rewritten). Every message carries the `"Originally archived by run <SEED_RUN_ID>..."` reference, giving an explicit audit trail linking the delete back to the archive that owns the data.

Phase 5 confirmed every dated year across all three tables was reduced to zero rows, while NULL-watermark rows (15 in claims, 10 in members, 5 in providers) remained untouched — per-year delete operates on integer years only, and NULL-bucket handling is out of scope for this code path (covered in 17T). A spot-check of six archive Delta paths showed they still hold the original counts; the archive is the durable copy and the delete job honours that contract.

Phase 6 cleaned up — 84 audit rows removed (the full combined history from 1a + 2 + 3 + 4), all three archive folders deleted, and `generate_test_data` re-run to restore source counts back to their starting totals.

## Next Steps

No regressions, no deviations. The scope guard's usability contract — "name any scope, no matter how broad" — is validated as the canonical "delete everything I archived" pattern. Three follow-ups to consider, all optional:

1. **Document the pattern.** Add a section to `docs/runbooks/delete-archived-data.md` explicitly calling out the three broad-scope invocations (schema-only, `table_config_filter = '1=1'`, and dry-run-before-live) so operators don't reinvent the wheel.
2. **NULL-watermark coverage.** NULL-bucket rows survived the delete-all — that's by design but easy to miss. A one-sentence note in the runbook would help: "The per-year delete path does not touch NULL-watermark rows; use the archive job's null-year handling (see 17T) if NULL rows need to be archived and removed."
3. **Optional guard softening.** If the `"must name a scope"` friction ever becomes a real complaint, the minimal change is a `confirm_full_scope=true` widget that satisfies the guard without requiring a filter. Not needed today — all three broad invocations here are easy to express.

Re-run guidance if any phase ever fails: `32T` owns the entire `source_data_samples` schema. Always run it in isolation from 25T/29T/30T. If Phase 4 fails specifically, check `archive_mode` first — any value other than `'DELETE'` indicates the delete job dispatched to the wrong code path.
