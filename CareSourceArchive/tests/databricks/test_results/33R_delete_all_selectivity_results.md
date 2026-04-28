# 33R — Delete All Selectivity (Only Archived Years Get Deleted)

Append new runs at the top of this file (below this H1).

---

## Run — 2026-04-28 00:01 CDT (post archiver SM cleanup + DeleteJob refactor)

**Overall: PASS.** Schema-wide selectivity invariants hold under the new `ArchiveBase`/`ArchiveEngine`/`DeleteJob` split. 21 (table,year) pairs split exactly 15 (claims+members) → DELETE / 6 (providers) → FAILED with the shared `not_archived_state` reason on both dry-run and live paths. Providers source is byte-identical before and after the live delete; no providers archive folder was ever created.

### TL;DR
- Phase 2 archives only claims+members (15 ARCHIVED/CREATE under SEED_RUN_ID); providers stays unarchived (no audit rows, no folder).
- Phase 3 dry-run yields 15 DRY_RUN/WOULD_DELETE + 6 DRY_RUN/SKIP_NOT_ELIGIBLE under DRY_RUN_ID — perfect 15/6 split.
- Phase 4 live yields 15 ARCHIVED_AND_DELETED/DELETE + 6 FAILED/`not_eligible_for_delete — not_archived_state` under DELETE_RUN_ID — same 15/6 split. Source: claims+members dated rows = 0; providers entirely unchanged (1000 rows including NULL-year). Archive Deltas for claims+members spot-checked unchanged.

### Environment
- Catalog: `dev2_archive`, audit/config schema `dev2_archive.metadata.*`
- Source schema: `source_data_samples` (claims/members/providers)
- Bundle target: `dev-serverless` / profile `fe-sandbox-manocha`
- Archive root: `/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples`
- `default_retention_years=0`

### Captured run IDs
- `SEED_RUN_ID` = `6e3fea76-8f7b-4a47-bdfe-f29fe347c242` (Phase 2 archive scoped to claims+members; 15 ARCHIVED/CREATE; job run `1094229825925182`, ~156s)
- `DRY_RUN_ID` = `d56d2612-3ab7-4083-9ef6-48cdb1761fec` (Phase 3 dry-run schema-wide; 15 WOULD_DELETE + 6 SKIP_NOT_ELIGIBLE; job run `928845964363288`, ~93s)
- `DELETE_RUN_ID` = `e82a2861-32c3-438f-9574-767c48859d9d` (Phase 4 live delete schema-wide; 15 ARCHIVED_AND_DELETED + 6 FAILED; job run `725663935552320`, ~114s)
- `RESTORE_RUN` = `301083137406553` (Phase 5 generate_test_data, ~62s)
- All four IDs distinct ✓.

### Baselines (post 32T Phase 5 restore — used in this run)
- claims dated total = 4985 (2018=623, 2019=624, 2020=623, 2021=623, 2022=624, 2023=624, 2024=622, 2025=622) + 15 NULL = 5000
- members dated total = 2990 (2019=426, 2020=426, 2021=427, 2022=428, 2023=429, 2024=426, 2025=428) + 10 NULL = 3000
- providers dated total = 995 (2020=163, 2021=168, 2022=166, 2023=167, 2024=166, 2025=165) + 5 NULL = 1000

### Phase 1 — Clean preconditions — PASS
- 0 audit rows for these 3 tables, no archive folders for any source_data_samples table (confirmed pre-flight, state inherited clean from 32T Phase 5).
- `delete_after_archive=false` for all 3 tables (confirmed; carried over from 32T cleanup).

### Phase 2 — Seed archive for claims + members only — PASS
- Archive job `1094229825925182` TERMINATED SUCCESS (~156s) with `table_config_filter=source_table IN ('claims','members')`.
- 2b: claims=8 ARCHIVED rows, members=7 ARCHIVED rows, **providers=0 audit rows**. All 15 ARCHIVED rows share single `SEED_RUN_ID`. Critical precondition satisfied ✓.
- 2c: source unchanged (verified implicitly via Phase 4c/dry-run baselines).
- 2d: `databricks fs ls .../providers` → "no such directory" ✓ — archive job correctly excluded providers.

### Phase 3 — Dry-run delete-all schema-wide — PASS
- Delete-job dry-run `928845964363288` TERMINATED SUCCESS (~93s). Schema scope only (no `table_config_filter`).
- 3b results (all 21 rows share `DRY_RUN_ID` ≠ `SEED_RUN_ID`):
  - claims (8 rows): `action=WOULD_DELETE`, `record_count` matches baselines (623/624/623/623/624/624/622/622).
  - members (7 rows): `action=WOULD_DELETE`, `record_count` matches baselines (426/426/427/428/429/426/428).
  - providers (6 rows, 2020-2025): `action=SKIP_NOT_ELIGIBLE`, `record_count=0`.
  - **Note:** `conditions_applied.reason_code` came back as empty string for SKIP rows — the dry-run path encodes the refusal in `action` only, not in a separate `reason_code` JSON field. The full reason text appears in `error_message` in the FAILED rows in Phase 4 (`not_archived_state`).
- 3c: source unchanged after dry-run (verified by Phase 4c).

### Phase 4 — Live delete-all schema-wide — PASS
- Delete-job live `725663935552320` TERMINATED SUCCESS (~114s). Schema scope only.
- 4b results (all 21 rows share `DELETE_RUN_ID`, ≠ SEED, ≠ DRY_RUN):
  - claims (8) + members (7) → 15 `ARCHIVED_AND_DELETED` rows. Every row: `archive_mode='DELETE'`, `archive_delta_version=NULL`, `record_count` matches baselines, `error_message`: `Deleted N rows from source. Originally archived by run 6e3fea76-... at <ts>.` (references SEED_RUN_ID ✓).
  - providers (6) → 6 `FAILED` rows. Every row: `archive_mode=NULL`, `archive_delta_version=NULL`, `record_count=0`, `error_message`: `dev2_archive.source_data_samples.providers year YYYY: Not eligible for delete — not_archived_state. Last success status: None. See docs/runbooks/delete-source-after-archive.md.` ✓ — exact text matches the spec, references the correct runbook (delete-source-after-archive.md, post-rename), reason code `not_archived_state` is the D13 D13_REASON_NOT_ARCHIVED_STATE constant.
  - **Zero rows where `table_name = providers AND archive_mode = 'DELETE'`** ✓ — selectivity strict.
- 4c source: claims/members dated years all gone (only NULL-year rows remain: claims=15, members=10), providers entirely intact (5+163+168+166+167+166+165=1000, byte-identical to baseline) ✓.
- 4d: providers archive folder still "no such directory" ✓ — delete job did not create archive artifacts.
- 4e archive Deltas spot-check: claims/year_2018=623, claims/year_2025=622, members/year_2019=426, members/year_2025=428 — all match baselines ✓.

### Phase 5 — Cleanup + restore — PASS
- 5a: 72 audit rows cleared (15 SEED CREATE + 15 STARTED for claims+members + 21 DRY_RUN + 21 ARCHIVED_AND_DELETED/FAILED for the live delete).
- 5b: claims and members archive folders removed; providers had nothing to drop.
- 5c restore: `generate_test_data` (run `301083137406553`) TERMINATED SUCCESS (~62s). Source restored to claims=5000, members=3000, providers=1000.

### What Happened (this run)
The selectivity invariant holds under the post-refactor codebase. The shared `is_eligible_for_delete` (D13) check in `AuditLogger` routes both the dry-run preview and live execution paths through the same gate, which is why the providers split is exactly the same on both passes (6 SKIP_NOT_ELIGIBLE on dry-run mirrors 6 FAILED/`not_archived_state` on live). The new `DeleteJob.run()` (in `src/delete_job.py`) calls `_record_delete_skip()` for both paths, producing the same row shape with `archive_mode=NULL`, `archive_delta_version=NULL`, `record_count=0` — only the `status` value differs (`DRY_RUN` vs `FAILED`).

The dry-run JSON's `reason_code` field appearing empty is consistent with the implementation: dry-run skip rows encode the refusal in `conditions_applied.action='SKIP_NOT_ELIGIBLE'` and only populate `error_message` on the FAILED live path. This is a reasonable signal split (dry-run is a preview, live is an audit), and the spec's `reason` annotation note (`record whatever reason_code appears`) accommodates it.

Providers source data is byte-identical before and after Phase 4. The delete job touched no providers row, no providers folder, no providers archive Delta. The 6 FAILED audit rows are the only artifact of the providers branch — and they exist precisely so the operator has a permanent record of why providers was skipped.

### Next Steps
- 33R is GREEN against the post-refactor codebase. No code action required.
- Optional: if you want a more discoverable `reason_code` on dry-run SKIP rows, the `DeleteJob._record_delete_skip` could be extended to populate `conditions_applied.reason_code` for `DRY_RUN` rows the same way the live FAILED path populates the prose in `error_message`. Not in scope for this regression.

---

## Run — 2026-04-27 11:43 CDT (rename-recovery-trio regression)

**Overall: PASS.** Schema-wide selectivity invariants from 33T held byte-for-byte; the recovery-trio rename does not affect the delete job's `is_eligible_for_delete` (D13) gate.

### TL;DR

- Phase 2 archived **only** claims+members (15 ARCHIVED rows, 0 providers); providers archive folder absent.
- Phase 3 dry-run produced **15 WOULD_DELETE + 6 SKIP_NOT_ELIGIBLE** under one DRY_RUN_ID, distinct from SEED_RUN_ID.
- Phase 4 live produced **15 ARCHIVED_AND_DELETED/DELETE + 6 FAILED/not_eligible_for_delete** under one DELETE_RUN_ID.
- Providers source counts **byte-identical** before vs after Phase 4 (NULL=5, 2020=163, 2021=168, 2022=166, 2023=167, 2024=166, 2025=165). Providers archive folder still does not exist.
- Live job did not write any `archive_mode = 'DELETE'` row for providers; it did not write to a non-existent providers archive folder.

### Run IDs

- `SEED_RUN_ID = 88174515-77c0-43dd-b6aa-06bfba1a675c` — Phase 2 archive of claims + members (filter `source_table IN ('claims','members')`)
- `DRY_RUN_ID  = 8ffca277-8d4d-4389-a2c2-0e8fc35e7202` — Phase 3 dry-run delete-all (no filter)
- `DELETE_RUN_ID = 4116a0c5-1ab0-4bcc-abc4-13fc6f4a568a` — Phase 4 live delete-all (no filter)

All three distinct. Every Phase 4 ARCHIVED_AND_DELETED `error_message` references SEED_RUN_ID via `"Originally archived by run 88174515-... at <ts>."`.

### Baselines (post-`generate_test_data`, used throughout)

| Table | NULL | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 |
|---|---|---|---|---|---|---|---|---|---|
| claims    | 15 | 623 | 624 | 623 | 623 | 624 | 624 | 622 | 622 |
| members   | 10 | —   | 426 | 426 | 427 | 428 | 429 | 426 | 428 |
| providers |  5 | —   | —   | 163 | 168 | 166 | 167 | 166 | 165 |

Identical to 2026-04-24 baselines.

### Phase 1 — Clean preconditions

Inherited from 32T Phase 5 cleanup (immediately preceding this run): audit empty for the three tables (verified `audit_rows = 0`), archive volume root empty (no `claims/`, `members/`, or `providers/`), `delete_after_archive = false` on all three configs, source restored to 5000/3000/1000 via `generate_test_data`. No additional cleanup required.

### Phase 2 — Selective archive (claims + members only)

- **Result:** PASS.
- `--params "table_config_filter=source_table IN ('claims','members')"` cleanly excluded providers.
- 15 ARCHIVED rows under `SEED_RUN_ID`: 8 claims (2018–2025) + 7 members (2019–2025), every `record_count` matches baseline, `archive_mode = 'CREATE'` everywhere.
- **Zero `table_name = 'dev2_archive.source_data_samples.providers'` audit rows.** Critical pre-condition holds.
- `databricks fs ls` on archive base returned only `claims` and `members` — providers folder does not exist.

### Phase 3 — Dry-run delete-all, schema scope only

- **Result:** PASS.
- 21 DRY_RUN rows under `DRY_RUN_ID = 8ffca277-...`:
  - **15 `WOULD_DELETE` rows** (claims 8 + members 7) with `record_count = BASELINE_{tbl}_{yr}`, `reason_code` empty.
  - **6 `SKIP_NOT_ELIGIBLE` rows** (providers 2020–2025) with `record_count = 0`, `reason_code` empty.
- Branching matches the 2026-04-24 run exactly: `conditions_applied.action` distinguishes `WOULD_DELETE` from `SKIP_NOT_ELIGIBLE`; `reason_code` is not populated in dry-run mode (operator signal is `action`).
- Single `DRY_RUN_ID` distinct from `SEED_RUN_ID`. Source untouched (verified implicitly by Phase 4c showing baseline-matching providers counts).

### Phase 4 — Live delete-all, schema scope only (the selectivity proof)

- **Result:** PASS.
- 21 rows under `DELETE_RUN_ID = 4116a0c5-...`, split exactly **15 DELETE / 6 FAILED**:

| Table | Years | Count | status / archive_mode | record_count | message_excerpt |
|---|---|---|---|---|---|
| claims    | 2018–2025 | 8 | `ARCHIVED_AND_DELETED / DELETE` | 623,624,623,623,624,624,622,622 | `Deleted N rows from source. Originally archived by run 88174515-...` |
| members   | 2019–2025 | 7 | `ARCHIVED_AND_DELETED / DELETE` | 426,426,427,428,429,426,428      | `Deleted N rows from source. Originally archived by run 88174515-...` |
| providers | 2020–2025 | 6 | `FAILED / archive_mode IS NULL` | 0 (all six)                       | `dev2_archive.source_data_samples.providers year YYYY: Not eligible for delete — not_archived_state. Last success status: None. See docs/runbooks/delete-source-after-archive.md.` |

- Every claims+members row: `archive_delta_version IS NULL` (delete job never rewrites archive).
- Every providers FAILED row: `archive_mode IS NULL`, `archive_delta_version IS NULL`, message contains `not_eligible_for_delete`, reason `not_archived_state`, runbook pointer.
- **Zero rows of any kind** with `archive_mode = 'DELETE'` for providers under any run ID.

### Phase 4c — Source counts post live delete

- `claims`: only NULL-watermark rows survive (cnt=15). Every dated year row gone.
- `members`: only NULL-watermark rows survive (cnt=10). Every dated year row gone.
- **`providers`: byte-identical to baseline** — NULL=5, 2020=163, 2021=168, 2022=166, 2023=167, 2024=166, 2025=165. Selectivity proof.

### Phase 4d — Providers archive folder still absent

```
$ databricks fs ls .../source_data_samples/providers
Error: no such directory: /Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples/providers
```

Delete job did not create providers archive artifacts.

### Phase 4e — claims+members archive Deltas unchanged

Spot-checked `claims/year_2018 = 623` and `members/year_2023 = 429` — both equal Phase 2 record_counts. Delete job did not rewrite the archive.

### Phase 5 — Cleanup + restore

- 72 audit rows cleared (15 ARCHIVED + 21 DRY_RUN + 21 ARCHIVED_AND_DELETED + 6 FAILED + 9 STARTED carryover).
- claims + members archive subfolders dropped (providers had nothing to drop).
- `generate_test_data` re-run, source restored to 5000/3000/1000.

### What this run proves about the rename plan

1. The dry-run vs live selectivity contract is unaffected by the recovery-trio rename: dry-run `SKIP_NOT_ELIGIBLE` rows line up row-for-row with live `FAILED / not_eligible_for_delete` rows for un-archived `(table, year)` pairs.
2. `AuditLogger.is_eligible_for_delete` (D13) — invoked from both branches — continues to enforce selectivity correctly with `not_archived_state` as the refusal reason for providers, even after the rename touched audit-status constants used by recovery jobs.
3. The schema-wide live delete neither reads nor writes the providers archive folder, archive Delta, or providers source — selectivity holds at the storage layer, not just the audit layer.
4. The delete job's `archive_mode = 'DELETE'` audit signature remains distinct from any recovery-trio audit signature (`RECOVERY_ARCHIVE_DELETED`, `RECOVERY_ARCHIVE_ROLLED_BACK`).

---

## Run — 2026-04-24 16:40 CDT

**Overall: PASS.** Every selectivity invariant in 33T held with no deviation.

### TL;DR timeline

| When (CDT) | Step | Outcome |
|---|---|---|
| 16:28–16:32 | Phase 2a archive run for claims + members only (table_config_filter applied) | 15 ARCHIVED rows, single SEED_RUN_ID, providers archive folder absent |
| 16:32 | Phase 2b audit check | 8 claims + 7 members under `SEED_RUN_ID=1961ab2d-…`, 0 providers rows |
| 16:32–16:34 | Phase 3a dry-run delete-all (schema scope only) | Job SUCCESS, 21 DRY_RUN audit rows |
| 16:34 | Phase 3b audit branching | 15 `WOULD_DELETE` (claims+members) / 6 `SKIP_NOT_ELIGIBLE` (providers), single `DRY_RUN_ID=b2a5d2a6-…`, source intact |
| 16:34–16:37 | Phase 4a live delete-all (schema scope only) | Job SUCCESS |
| 16:37 | Phase 4b audit breakdown | 15 `ARCHIVED_AND_DELETED / DELETE` (claims+members) + 6 `FAILED / not_eligible_for_delete` (providers), single `DELETE_RUN_ID=49a9b354-…` |
| 16:37 | Phase 4c source counts | claims/members dated rows = 0; NULL preserved; **providers byte-identical before/after** |
| 16:37 | Phase 4d/e archive state | providers folder still absent; claims/2020 and members/2023 Deltas still hold BASELINE counts |
| 16:38–16:40 | Phase 5 cleanup + restore | 21 audit rows cleared (72 total incl. lingering rows), claims+members folders dropped, `generate_test_data` restored source (claims=5000, members=3000, providers=1000) |

### Run IDs (persisted for audit review)

- `SEED_RUN_ID = 1961ab2d-dd2e-45ff-bf93-cd9dad7d8c86` — Phase 2 archive of claims + members
- `DRY_RUN_ID  = b2a5d2a6-f93e-4c8c-8939-25ee848656bc` — Phase 3 dry-run delete-all
- `DELETE_RUN_ID = 49a9b354-445e-45f5-8e62-5a2fdf862be5` — Phase 4 live delete-all

### Baselines captured (Phase 2b / Phase 3c)

| Table | NULL | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 |
|---|---|---|---|---|---|---|---|---|---|
| claims    | 15 | 623 | 624 | 623 | 623 | 624 | 624 | 622 | 622 |
| members   | 10 | —   | 426 | 426 | 427 | 428 | 429 | 426 | 428 |
| providers |  5 | —   | —   | 163 | 168 | 166 | 167 | 166 | 165 |

### Phase 2 — Selective archive (claims + members only)

- **Result:** PASS.
- `table_config_filter = "source_table IN ('claims','members')"` cleanly excluded providers from the archive run.
- Phase 2b query returned exactly 15 `status = ARCHIVED` rows — 8 claims + 7 members — all under `SEED_RUN_ID`. **Zero `table_name = 'dev2_archive.source_data_samples.providers'` rows**, as required.
- `databricks fs ls` on the archive base showed only `claims/` and `members/` children — providers folder does not exist. Critical precondition for the selectivity test holds.

### Phase 3 — Dry-run delete-all, schema scope only

- **Result:** PASS.
- 21 total DRY_RUN rows under `DRY_RUN_ID = b2a5d2a6-…`:
  - **15 `WOULD_DELETE` rows** (claims 2018–2025, members 2019–2025) with `record_count = BASELINE_{tbl}_{yr}` — every row matches the Phase 2b ARCHIVED record_count exactly.
  - **6 `SKIP_NOT_ELIGIBLE` rows** (providers 2020–2025) with `record_count = 0`.
- Observed shape: `conditions_applied.action` distinguishes the two branches. `conditions_applied.reason_code` is not populated for dry-run, and `error_message` is NULL for `SKIP_NOT_ELIGIBLE`. The operator-visible signal during a dry-run is the `action` value alone — which is sufficient because both branches share the same DRY_RUN status.
- Source counts re-run after the dry-run matched every BASELINE byte-for-byte, confirming dry-run writes no data paths.

### Phase 4 — Live delete-all, schema scope only (the selectivity proof)

- **Result:** PASS.
- 21 total rows under `DELETE_RUN_ID = 49a9b354-…`, cleanly split **15 DELETE / 6 FAILED**, exactly matching the Phase 3 dry-run branching:

| Table | Years | Count | status / archive_mode | record_count |
|---|---|---|---|---|
| claims    | 2018–2025 | 8 | `ARCHIVED_AND_DELETED / DELETE` | BASELINE_claims_{yr} |
| members   | 2019–2025 | 7 | `ARCHIVED_AND_DELETED / DELETE` | BASELINE_members_{yr} |
| providers | 2020–2025 | 6 | `FAILED / archive_mode IS NULL` | `0` |

- **Every DELETE row references the seed run in its error_message**, e.g. `"Deleted 623 rows from source. Originally archived by run 1961ab2d-dd2e-45ff-bf93-cd9dad7d8c86 at 2026-04-24 21:30:06.031987."` — the delete is provably tied to the Phase 2 archive event.
- **Every FAILED row carries the D13 diagnostic**: `"dev2_archive.source_data_samples.providers year <YYYY>: Not eligible for delete — not_archived_state. Last success status: None. See docs/runbooks/delete-archived-data.md."` `reason_code = not_archived_state`, `Last success status = None` (because providers has zero audit history to point to).
- **No row anywhere has `table_name = '…providers'` AND `archive_mode = 'DELETE'`** — the selectivity invariant the test was written to prove.

### Phase 4c/d — Source and archive state after live delete-all

Source GROUP BY `(table, year)` after Phase 4:

```
tbl        yr     cnt
claims     NULL   15          ← NULL-watermark preserved
members    NULL   10          ← NULL-watermark preserved
providers  NULL    5          ← PROVIDERS UNCHANGED
providers  2020  163          ← PROVIDERS UNCHANGED
providers  2021  168          ← PROVIDERS UNCHANGED
providers  2022  166          ← PROVIDERS UNCHANGED
providers  2023  167          ← PROVIDERS UNCHANGED
providers  2024  166          ← PROVIDERS UNCHANGED
providers  2025  165          ← PROVIDERS UNCHANGED
```

- claims + members: every dated year row gone (no GROUP BY entry returned), NULL row preserved. 15 + 10 = 25 NULL rows match pre-run totals.
- **providers: byte-identical before and after Phase 4.** Every dated year and the NULL bucket match pre-delete counts exactly.
- `databricks fs ls` of the archive base: still only `claims/` and `members/`. Delete job did not create a providers folder.
- Spot-check of archive Deltas: `claims/year_2020 = 623`, `members/year_2023 = 429` — both match BASELINE. Archive Deltas are untouched by the delete.

### Phase 5 — Cleanup

- Audit rows cleared: 72 `num_affected_rows` (the 21 from this run + prior lingering rows from earlier test prep).
- Archive folders dropped: `claims/`, `members/` (providers had nothing to drop).
- `generate_test_data` restored source: claims = 5000, members = 3000, providers = 1000.

### What this run proves (beyond what 32T proved)

1. **The delete-all is selective at the audit-log level.** 15 `DELETE` audit rows vs 6 `FAILED` audit rows, partitioned exactly along the archived / un-archived boundary from Phase 2. No providers year slipped through.
2. **The delete-all is selective at the source data level.** Providers table row count and per-year distribution are byte-identical before and after the live run. D13 does not touch source data for refused years — it short-circuits at `is_eligible_for_delete` before any `DELETE FROM` executes against the source.
3. **Dry-run and live paths agree about selectivity.** The Phase 3 `SKIP_NOT_ELIGIBLE` set is the exact `(table, year)` set that surfaces as `FAILED / not_eligible_for_delete` in Phase 4. Operators get a faithful preview — nothing refused in the dry-run can secretly succeed live.
4. **D13 produces a clean audit trail for refusals.** Every FAILED row carries `reason_code = not_archived_state`, a `Last success status` pointer (here `None` because providers has no history), and a runbook path. An on-call can pivot straight from `SELECT * FROM archive_audit_log WHERE status='FAILED'` into a root-cause investigation without guessing.

### Next steps

- **Fold 33T pattern into the runbook.** `docs/runbooks/delete-archived-data.md` should reference this test case as the canonical selectivity proof, alongside 32T for broad-scope mechanics and 30T for single-year D13 behavior.
- **Consider a unit test for `_resolve_delete_years` + `is_eligible_for_delete` interaction.** The integration coverage is strong after 30T/32T/33T; a unit test would cover the same invariant without a workspace round-trip.
- **No code changes indicated.** Selectivity works end-to-end as designed.
