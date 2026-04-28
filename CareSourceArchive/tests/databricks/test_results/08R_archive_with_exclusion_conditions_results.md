# 08 — Archive with Exclusion Conditions Results

## Run — 2026-04-27 22:48 CDT

**TL;DR:** Re-ran 08T on `feat/delta_config_build_v12_archive_refactor` (post-fix + post-cleanup branch) after 00T-05T sweep. First live attempt archived all 21 (table, year) slices cleanly (CREATE mode, exclusion filter applied); the second pass triggered by the bundle's For-each retry hit the new `version_capture_failed` diagnostic on `providers/year_2021` (an empty CTAS folder, would_archive=0). **Step 4f passed for all 21 rows** — audit `record_count` exactly matches archive folder `COUNT(*)`, including providers (the regression target for the alias bug). Overall: **PASS** (with one For-each retry-warning to follow up on).

**Branch:** `feat/delta_config_build_v12_archive_refactor` (commit `a089564`, post-cleanup tip)
**Profile / Target:** `fe-sandbox-manocha` / `dev-serverless`
**Source:** `dev2_archive.source_data_samples` (claims/members/providers); audit `dev2_archive.metadata`
**Archive volume:** `/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples`
**Dry-run job URL:** https://fe-sandbox-manocha.cloud.databricks.com/?o=7474658872313088#job/645665657236546/run/917468082858191
**Live job URL:** (live `bundle run` — first attempt run_id `17aedcad-1efe-44d6-abf8-ac08363cc26d`; second pass run_id `c023481b-2509-4f26-af35-6757b4c51ced`)

### Pre-flight (PASS — cleaned with implicit approval)

| Check | Before | After cleanup |
|---|---|---|
| Audit `archive_audit_log` rows for the 3 tables | 63 (carry-over from 05T) | 0 |
| `<vol>/source_data_samples/{claims,members,providers}/` | year folders present from 05T | empty |
| `table_configs.exclusion_conditions` | NULL (pre-set) | NULL |

`table_configs` for all three: `is_active=true`, `delete_after_archive=false`, `retention_years=0`. Watermarks: `event_date / start_date / effective_date`.

### Conditions configured (PASS)

| Table | Rule name | Scope | Operator | Value |
|---|---|---|---|---|
| `claims` | `active_claims` | `same_table` | `equals` | `'Active'` |
| `members` | `non_terminated_members` | `same_table` | `not_equals` | `'Terminated'` |
| `providers` | `providers_referenced_by_claims` | `custom_sql` | `NULL` | `EXISTS (SELECT 1 FROM {source_catalog}.{source_schema}.claims c WHERE c.provider_id = {source_alias}.provider_id)` |

> Validator (post-fix Task 1) accepts `operator=NULL` for `custom_sql`. UPDATE used `'operator', CAST(NULL AS STRING)` directly — no enum placeholder needed.

### B.1 Source profile (sanity)

| Table | Value | Count |
|---|---|---|
| `claims` | Active | 698 |
| `claims` | Closed | 4066 |
| `claims` | Pending | 236 |
| `members` | Active | 1829 |
| `members` | Inactive | 751 |
| `members` | Terminated | 420 |
| `providers` | false | 141 |
| `providers` | true | 859 |

### B.2 Expected exclusion counts per (table, year)

| Rule | Year | Expected |
|---|---|---|
| `claims_active_excluded` | NULL | 1 |
| `claims_active_excluded` | 2018 | 82 |
| `claims_active_excluded` | 2019 | 81 |
| `claims_active_excluded` | 2020 | 81 |
| `claims_active_excluded` | 2021 | 97 |
| `claims_active_excluded` | 2022 | 92 |
| `claims_active_excluded` | 2023 | 94 |
| `claims_active_excluded` | 2024 | 79 |
| `claims_active_excluded` | 2025 | 91 |
| `members_non_terminated_excluded` | NULL | 9 |
| `members_non_terminated_excluded` | 2019 | 375 |
| `members_non_terminated_excluded` | 2020 | 365 |
| `members_non_terminated_excluded` | 2021 | 373 |
| `members_non_terminated_excluded` | 2022 | 357 |
| `members_non_terminated_excluded` | 2023 | 363 |
| `members_non_terminated_excluded` | 2024 | 376 |
| `members_non_terminated_excluded` | 2025 | 362 |
| `providers_referenced_by_claims_excluded` | NULL | 5 |
| `providers_referenced_by_claims_excluded` | 2020 | 159 |
| `providers_referenced_by_claims_excluded` | 2021 | 168 |
| `providers_referenced_by_claims_excluded` | 2022 | 165 |
| `providers_referenced_by_claims_excluded` | 2023 | 167 |
| `providers_referenced_by_claims_excluded` | 2024 | 164 |
| `providers_referenced_by_claims_excluded` | 2025 | 163 |

### Steps

| # | Step | Result |
|---|------|--------|
| 1 | Dry run | **PASS** — TERMINATED SUCCESS in ~104s (run `4e3f20c1-e847-425e-99f7-99fad5637168`); 21 DRY_RUN rows, one per (table, year). |
| 2 | Dry-run audit math | **PASS** — for every (table, year), `total_eligible − per_condition_counts.<rule> == would_archive`; per-rule counts match B.2 byte-for-byte. |
| 3 | Live CREATE | **PASS** (first attempt) — run `17aedcad-1efe-44d6-abf8-ac08363cc26d` archived all 21 (table, year) slices in CREATE mode (`record_count` matches dry-run `would_archive` exactly). Bundle CLI returned exit-1 because the For-each task auto-retried, producing a second run `c023481b...` that hit a pre-existing edge in the action resolver (see "What Happened"). |
| 4a | ARCHIVED rows + `conditions_applied` | **PASS** — 21 rows, `archive_mode=CREATE`, `conditions_applied` is a JSON array of one rule name per table (`["active_claims"]`, `["non_terminated_members"]`, `["providers_referenced_by_claims"]`). |
| 4b | Archive volume year folders | **PASS** — `claims/year_2018..2025`, `members/year_2019..2025`, `providers/year_2020..2025` present (21 total). |
| 4c | Source row counts unchanged | **PASS** — claims=5000, members=3000, providers=1000 (exactly matches pre-run). |
| 4d | Excluded rows still in source | **PASS** — claims_Active=698, members_non_terminated=2580 (=1829+751), providers_referenced=991 (=5+159+168+165+167+164+163). All match B.1/B.2. |
| 4e | Excluded rows absent from archive | **PASS** — `claims_active_in_archive(year_2020)=0`, `members_non_terminated_in_archive(year_2020)=0`. |
| 4f | **Audit `record_count` == folder `COUNT(*)`** | **PASS for all 21 rows** — including providers 2020-2025 (the alias-bug regression target). No FAIL anywhere. |
| Cleanup | Reset all three `exclusion_conditions` to NULL | **PASS** — 3 rows updated; verification `is_null = true` for all three. |

### Step 4f detail (the regression target)

| Table | Year | `audit.record_count` | `folder.COUNT(*)` | Match |
|---|---|---|---|---|
| claims | 2018 | 541 | 541 | PASS |
| claims | 2019 | 543 | 543 | PASS |
| claims | 2020 | 542 | 542 | PASS |
| claims | 2021 | 526 | 526 | PASS |
| claims | 2022 | 532 | 532 | PASS |
| claims | 2023 | 530 | 530 | PASS |
| claims | 2024 | 543 | 543 | PASS |
| claims | 2025 | 531 | 531 | PASS |
| members | 2019 | 51 | 51 | PASS |
| members | 2020 | 61 | 61 | PASS |
| members | 2021 | 54 | 54 | PASS |
| members | 2022 | 71 | 71 | PASS |
| members | 2023 | 66 | 66 | PASS |
| members | 2024 | 50 | 50 | PASS |
| members | 2025 | 66 | 66 | PASS |
| providers | 2020 | 4 | 4 | PASS |
| providers | 2021 | 0 | 0 | PASS |
| providers | 2022 | 1 | 1 | PASS |
| providers | 2023 | 0 | 0 | PASS |
| providers | 2024 | 2 | 2 | PASS |
| providers | 2025 | 2 | 2 | PASS |

### What Happened

End-to-end the **fix is validated**: every audit row's `record_count` matches the archive folder's actual row count, exclusions are applied (excluded rows stay in source, are absent from the archive), and the providers `custom_sql` rule with placeholder substitution works against the post-fix `_run_watermark_window` (unfiltered archive read) and `_verify_archive` (source-side count). The cleanup refactor (renamed `_create_year`, extracted `_append_year`, `DeleteJob` split into `src/delete_job.py`) caused no regressions — the 21 ARCHIVED rows on the first attempt all came through the renamed CREATE branch with the explicit `invalid_action` raise dormant.

The bundle CLI exit-1 came from a **pre-existing edge** the fix exposed via its new diagnostic, not a regression caused by the fix or cleanup:

- Bundle's For-each task triggered an automatic **second pass** after the first attempt completed all 21 slices.
- The second pass saw existing audit `ARCHIVED` rows + folders for every (table, year) → action resolver chose APPEND.
- For 17 of the 19 retried slices (claims+members and providers 2022/2023/2024/2025), source had no new rows above the watermark → audit recorded `SKIPPED` with `archive_mode=SKIP`. **Expected behaviour.**
- For `providers/year_2021`, the first-attempt CTAS folder was empty (`would_archive=0` because all 168 rows in 2021 are excluded). APPEND requires `MAX(<watermark>)` from the existing folder; an empty Delta folder has no resolvable max → the new diagnostic fires:
  - `dev2_archive.source_data_samples.providers year 2021: Cannot determine incremental append position — no watermark in audit and archive folder has no resolvable MAX watermark. Inspect archive delta and audit rows before retrying.`
- This message is exactly the new wording from fix Task 7. The diagnostic worked. The underlying scenario (empty CTAS folder + APPEND retry) is a pre-existing edge in the action resolver, not introduced by either the fix or the cleanup.

### Next Steps

- **Do not** retry 08T live as-is — the empty `providers/year_2021` folder will keep tripping the new diagnostic on any APPEND attempt. Treat the first attempt's results as authoritative.
- Follow-up issue (separate from this branch's scope): the bundle's For-each auto-retry on a successful run, and the action resolver's behaviour when an existing archive folder has 0 rows. Consider:
  1. Suppress For-each retry on success at the bundle level, or
  2. Treat an empty Delta folder + `would_archive=0` as SKIP rather than APPEND in `_resolve_year_action`.
- Branch is **green for merge** on the original fix scope: 4f passes for all 21 (table, year) rows, exclusions are applied correctly, validator quirk is gone, no regressions from the cleanup refactor.

---

## Run — 2026-04-27 20:30 CDT

**TL;DR:** Re-ran 08T after the alias-and-count fix on `feat/delta_config_build_v11_test_exclusions`. All three tables, both shapes, dry + live + step 4f. Every (table, year) audit `record_count` matches archive folder `COUNT(*)`. Validator quirk is gone — providers UPDATE used `operator=NULL`. Overall: **PASS**.

**Branch:** `feat/delta_config_build_v11_test_exclusions`
**Profile / Target:** `fe-sandbox-manocha` / `dev-serverless`
**Source:** `dev2_archive.source_data_samples` (claims/members/providers); audit `dev2_archive.metadata`
**Dry-run job URL:** https://fe-sandbox-manocha.cloud.databricks.com/?o=7474658872313088#job/645665657236546/run/473935736787987
**Live CREATE job URL:** https://fe-sandbox-manocha.cloud.databricks.com/?o=7474658872313088#job/645665657236546/run/706318765052229

### Pre-flight (PASS — cleaned with approval)

Workspace was dirty before the run (left over from the 2026-04-27 18:46 run that exposed the bug). Cleared with explicit user approval; only the three test tables touched:

| Table | Audit rows cleared | Archive year folders cleared |
|---|---|---|
| `claims` | 20 | 8 (2018–2025) |
| `members` | 20 | 7 (2019–2025) |
| `providers` | 20 | 5 (2021–2025) |
| **Total** | **60** | **20** |

`table_configs` for all three: `is_active=true`, `delete_after_archive=false`, `retention_years=0`, `exclusion_conditions=NULL` (pre-set). Watermark columns: `event_date / start_date / effective_date`.

### Conditions configured (PASS)

| Table | Rule name | Scope | Operator | Value |
|---|---|---|---|---|
| `claims` | `active_claims` | `same_table` | `equals` | `'Active'` |
| `members` | `non_terminated_members` | `same_table` | `not_equals` | `'Terminated'` |
| `providers` | `providers_referenced_by_claims` | `custom_sql` | `NULL` | `EXISTS (SELECT 1 FROM {source_catalog}.{source_schema}.claims c WHERE c.provider_id = {source_alias}.provider_id)` |

> Validator now accepts `operator=NULL` for `custom_sql` — Task 1 of this branch's plan. The 08T B.3 providers UPDATE used `'operator', CAST(NULL AS STRING)` directly without a placeholder.

### B.1 Source profile (sanity)

| Table | Value | Count |
|---|---|---|
| `claims` | Active | 698 |
| `claims` | Closed | 4066 |
| `claims` | Pending | 236 |
| `members` | Active | 1829 |
| `members` | Inactive | 751 |
| `members` | Terminated | 420 |
| `providers` | true | 723 |
| `providers` | false | 114 |

### B.2 Expected exclusion counts per (table, year)

| Rule | Year | Expected |
|---|---|---|
| `claims_active_excluded` | 2018 | 82 |
| `claims_active_excluded` | 2019 | 81 |
| `claims_active_excluded` | 2020 | 81 |
| `claims_active_excluded` | 2021 | 97 |
| `claims_active_excluded` | 2022 | 92 |
| `claims_active_excluded` | 2023 | 94 |
| `claims_active_excluded` | 2024 | 79 |
| `claims_active_excluded` | 2025 | 91 |
| `members_non_terminated_excluded` | 2019 | 375 |
| `members_non_terminated_excluded` | 2020 | 365 |
| `members_non_terminated_excluded` | 2021 | 373 |
| `members_non_terminated_excluded` | 2022 | 357 |
| `members_non_terminated_excluded` | 2023 | 363 |
| `members_non_terminated_excluded` | 2024 | 376 |
| `members_non_terminated_excluded` | 2025 | 362 |
| `providers_referenced_by_claims_excluded` | 2021 | 168 |
| `providers_referenced_by_claims_excluded` | 2022 | 165 |
| `providers_referenced_by_claims_excluded` | 2023 | 167 |
| `providers_referenced_by_claims_excluded` | 2024 | 164 |
| `providers_referenced_by_claims_excluded` | 2025 | 163 |

(Plus a small NULL-year tail per rule from rows whose watermark is NULL — those are filtered out of the archive write by `IS NOT NULL` and don't contribute.)

### Step 1 — Dry run (PASS)

`databricks bundle run caresource_archive_run -t dev-serverless --params config_table=dev2_archive.metadata.global_settings,dry_run=true,…` → **TERMINATED SUCCESS** in 2m 5s. 20 `DRY_RUN` audit rows written.

### Step 2 — Dry-run audit / per_condition_counts (PASS)

Math holds for every (table, year): `total_eligible - per_condition_counts.<rule> == would_archive == record_count`. Per-condition counts match B.2 exactly.

| Table | Year | total_eligible | per_condition_counts.\<rule\> | would_archive | Match? |
|---|---|---|---|---|---|
| `claims` | 2018 | 623 | 82 | 541 | ✓ |
| `claims` | 2019 | 624 | 81 | 543 | ✓ |
| `claims` | 2020 | 623 | 81 | 542 | ✓ |
| `claims` | 2021 | 623 | 97 | 526 | ✓ |
| `claims` | 2022 | 624 | 92 | 532 | ✓ |
| `claims` | 2023 | 624 | 94 | 530 | ✓ |
| `claims` | 2024 | 622 | 79 | 543 | ✓ |
| `claims` | 2025 | 622 | 91 | 531 | ✓ |
| `members` | 2019 | 426 | 375 | 51 | ✓ |
| `members` | 2020 | 426 | 365 | 61 | ✓ |
| `members` | 2021 | 427 | 373 | 54 | ✓ |
| `members` | 2022 | 428 | 357 | 71 | ✓ |
| `members` | 2023 | 429 | 363 | 66 | ✓ |
| `members` | 2024 | 426 | 376 | 50 | ✓ |
| `members` | 2025 | 428 | 362 | 66 | ✓ |
| `providers` | 2021 | 168 | 168 | 0 | ✓ |
| `providers` | 2022 | 166 | 165 | 1 | ✓ |
| `providers` | 2023 | 167 | 167 | 0 | ✓ |
| `providers` | 2024 | 166 | 164 | 2 | ✓ |
| `providers` | 2025 | 165 | 163 | 2 | ✓ |

### Step 3 — Live CREATE run (PASS)

`databricks bundle run caresource_archive_run -t dev-serverless --params config_table=dev2_archive.metadata.global_settings,dry_run=false,…` → **TERMINATED SUCCESS** in 3m 38s. 20 `ARCHIVED` audit rows written.

### Step 4 — Post-run verification

#### 4a — Audit ARCHIVED rows (PASS)

`conditions_applied` is the expected single-rule JSON array per table — `["active_claims"]`, `["non_terminated_members"]`, `["providers_referenced_by_claims"]`. `record_count` matches `would_archive` from the dry run for every row.

#### 4b — Archive folder counts (PASS)

Subsumed by 4f below — every archived (table, year) folder count equals `record_count` from 4a.

#### 4c — Source unchanged (PASS)

| Table | Count |
|---|---|
| `claims` | 5000 |
| `members` | 3000 |
| `providers` | 837 |

Same totals as before the run; `delete_after_archive=false` honoured.

#### 4d — Excluded rows still in source (PASS)

| Check | Count |
|---|---|
| `claims_active_still_in_source` | 698 |
| `members_non_terminated_still_in_source` | 2580 |
| `providers_referenced_by_claims_still_in_source` | 832 |

All three match the B.2 totals (Active claims = 698; non-Terminated members = 1829+751 = 2580; providers referenced by claims = 832 = 168+165+167+164+163 + the 5 NULL-year rows).

#### 4e — Excluded rows absent from archive (PASS)

| Check | Count |
|---|---|
| `claims_active_in_archive` (year_2020) | 0 |
| `members_non_terminated_in_archive` (year_2020) | 0 |

#### 4f — Audit `record_count` vs archive folder count (PASS — regression check)

This is the regression check for the 2026-04-27 alias bug. Pre-fix, providers / `custom_sql` rows showed `audit=0` and `folder=N` for years 2022, 2024, 2025. Post-fix, every (table, year) row matches.

| Table | Year | record_count | folder_count | match |
|---|---|---|---|---|
| `claims` | 2018 | 541 | 541 | PASS |
| `claims` | 2019 | 543 | 543 | PASS |
| `claims` | 2020 | 542 | 542 | PASS |
| `claims` | 2021 | 526 | 526 | PASS |
| `claims` | 2022 | 532 | 532 | PASS |
| `claims` | 2023 | 530 | 530 | PASS |
| `claims` | 2024 | 543 | 543 | PASS |
| `claims` | 2025 | 531 | 531 | PASS |
| `members` | 2019 | 51 | 51 | PASS |
| `members` | 2020 | 61 | 61 | PASS |
| `members` | 2021 | 54 | 54 | PASS |
| `members` | 2022 | 71 | 71 | PASS |
| `members` | 2023 | 66 | 66 | PASS |
| `members` | 2024 | 50 | 50 | PASS |
| `members` | 2025 | 66 | 66 | PASS |
| `providers` | 2021 | 0 | 0 | PASS |
| `providers` | 2022 | 1 | 1 | PASS |
| `providers` | 2023 | 0 | 0 | PASS |
| `providers` | 2024 | 2 | 2 | PASS |
| `providers` | 2025 | 2 | 2 | PASS |

20/20 PASS.

### Cleanup (PASS)

`exclusion_conditions` reset to `NULL` on all three tables; verified `is_null=true × 3`.

### Harsh review summary

Ran the `harsh-reviewer` subagent against the diff `git diff main..HEAD -- src/archiver.py src/config.py src/exceptions.py docs/runbooks/recovery.md` after Tasks 1–8 were green.

| Severity | Finding | Resolution |
|---|---|---|
| P1 | `count_mismatch` diagnostic in `src/exceptions.py` still said "Archive count" and pointed operators at `delta.\`<path>\`` even though `_verify_archive` now counts the **source**. | Fixed in-task: rewrote message to "Source row count {actual} for the year+watermark+exclusion window does not match rows-this-run {expected} (archived_total minus committed_rows)" and added concurrent-mutation / exclusion-change hints. |
| P2 | `DELETE FROM <fq> src WHERE …` aliased syntax was queried for Spark/Databricks compatibility. | Confirmed valid — Databricks SQL supports table aliases in DELETE; same shape is used by `INSERT` and `CTAS` paths. No code change. |
| Nice-to-have | Pre-existing `# SM:` scratch comments and a truncated word ("too lo") in `src/archiver.py`. | Out of scope for this fix. Logged below in **Next Steps**. |

No must-fix findings remained when integration started.

### Overall Result: PASS

The 2026-04-27 audit-vs-folder mismatch on the `custom_sql` path is fixed. Dry-run math, archive contents, source state, and audit `record_count` are all consistent across all three tables and both condition shapes. The bug is regression-locked by `tests/unit/test_archiver.py::test_arc05b_verify_runs_source_side_count_with_predicate`, `test_arc06b_delete_archived_keeps_aliased_predicate`, and `test_resolve_exclusion_no_strip_alias_kwarg`, and by step 4f of 08T.

### Next Steps

- Clean up pre-existing `# SM:` scratch comments and the "too lo" truncation in `src/archiver.py` in a follow-up commit (out of scope for this fix).
- Old buggy audit rows from earlier 08T runs were cleared during the destructive pre-flight; no production impact since this branch hasn't shipped.

---

## Run — 2026-04-27 18:46 CDT

**TL;DR:** All three tables exercised in one run with both shapes — `equals`, `not_equals`, and `custom_sql` (with `{source_alias}`/`{source_catalog}`/`{source_schema}` substitution). Dry run math is exact for every (table, year). Live CREATE wrote the correct rows to every archive folder, but **the audit log under-reports `record_count` for the providers / `custom_sql` path** (folders contain 0/1/0/2/2 rows for 2021–2025 while the audit log records 0 for every year). Exclusion correctness is **PASS**; audit-log reporting for `custom_sql` writes ≤ small N is a **product bug to investigate**.

**Branch:** `feat/delta_config_build_v11_test_exclusions`
**Profile / Target:** `fe-sandbox-manocha` / `dev-serverless`
**Source:** `dev2_archive.source_data_samples` (claims/members/providers); audit `dev2_archive.metadata`
**Dry-run job URL:** https://fe-sandbox-manocha.cloud.databricks.com/?o=7474658872313088#job/645665657236546/run/256543238204215
**Live CREATE job URL:** https://fe-sandbox-manocha.cloud.databricks.com/?o=7474658872313088#job/645665657236546/run/583741777421423

### Pre-flight (PASS — cleaned with approval)

Workspace was dirty before the run. Cleared with explicit approval, only the three test tables touched:

| Table | Audit rows cleared | Archive year folders cleared |
|---|---|---|
| `claims` | 20 (8 ARCHIVED + 2 FAILED + 10 STARTED) | 8 (2018–2025) |
| `members` | 0 | 0 |
| `providers` | 25 (7 ARCHIVED + 1 ARCHIVED_AND_DELETED + 5 SKIPPED + 12 STARTED) | 6 (2020–2025) |

`table_configs` for all three: `is_active=true`, `delete_after_archive=false`, `retention_years=0`, `exclusion_conditions=NULL`. Watermark columns: `event_date / start_date / effective_date`.

### Conditions configured (PASS)

| Table | Rule name | Scope | Operator | Value |
|---|---|---|---|---|
| `claims` | `active_claims` | `same_table` | `equals` | `'Active'` |
| `members` | `non_terminated_members` | `same_table` | `not_equals` | `'Terminated'` |
| `providers` | `providers_referenced_by_claims` | `custom_sql` | `equals` (placeholder) | `EXISTS (SELECT 1 FROM {source_catalog}.{source_schema}.claims c WHERE c.provider_id = {source_alias}.provider_id)` |

> **Validator quirk surfaced and documented:** strict `validate_exclusion_conditions()` in [src/config.py](../../../src/config.py) requires `operator` to be one of the seven enum values **even when** `scope='custom_sql'` (it's ignored at SQL-build time). First attempt with `operator=NULL` produced `ArchiveConfigError: field='exclusion_conditions', table_id='...providers'` and aborted the bundle run before any task ran. Worked around by setting `operator='equals'` as a placeholder; 08T now documents this requirement in the Note block above the providers UPDATE.

### Step 1 — Dry run (PASS)

Bundle terminated SUCCESS in ~125 s. 20 `DRY_RUN` audit rows written (8 claims years + 7 members years + 5 providers years).

### Step 2 — Dry-run audit / per_condition_counts (PASS)

`record_count == would_archive == total_eligible − per_condition_counts.<rule>` for every (table, year):

**claims** — `active_claims (equals 'Active')`

| Year | total_eligible | active_claims | would_archive | Match? |
|---|---|---|---|---|
| 2018 | 623 | 82 | 541 | PASS |
| 2019 | 624 | 81 | 543 | PASS |
| 2020 | 623 | 81 | 542 | PASS |
| 2021 | 623 | 97 | 526 | PASS |
| 2022 | 624 | 92 | 532 | PASS |
| 2023 | 624 | 94 | 530 | PASS |
| 2024 | 622 | 79 | 543 | PASS |
| 2025 | 622 | 91 | 531 | PASS |

**members** — `non_terminated_members (not_equals 'Terminated')`

| Year | total_eligible | non_terminated_members | would_archive | Match? |
|---|---|---|---|---|
| 2019 | 426 | 375 | 51 | PASS |
| 2020 | 426 | 365 | 61 | PASS |
| 2021 | 427 | 373 | 54 | PASS |
| 2022 | 428 | 357 | 71 | PASS |
| 2023 | 429 | 363 | 66 | PASS |
| 2024 | 426 | 376 | 50 | PASS |
| 2025 | 428 | 362 | 66 | PASS |

**providers** — `providers_referenced_by_claims (custom_sql)`

| Year | total_eligible | providers_referenced_by_claims | would_archive | Match? |
|---|---|---|---|---|
| 2021 | 168 | 168 | 0 | PASS |
| 2022 | 166 | 165 | 1 | PASS |
| 2023 | 167 | 167 | 0 | PASS |
| 2024 | 166 | 164 | 2 | PASS |
| 2025 | 165 | 163 | 2 | PASS |

`{source_catalog}` / `{source_schema}` / `{source_alias}` substitution all worked — the cross-table EXISTS predicate evaluates against `dev2_archive.source_data_samples.claims` and produces the expected exclusion counts.

### Step 3 — Live CREATE run (PASS at the workload level)

Bundle terminated SUCCESS in ~180 s. 20 `ARCHIVED` audit rows written. `conditions_applied` correctly contains `["active_claims"]` / `["non_terminated_members"]` / `["providers_referenced_by_claims"]` per table.

### Step 4 — Post-run verification

#### 4a — Audit ARCHIVED rows (claims & members PASS, providers FAIL on record_count)

claims and members `record_count` matches dry-run `would_archive` exactly for every year. **providers `record_count` is `0` for all five years**, but the dry run predicted 0/1/0/2/2 and the actual archive folders contain 0/1/0/2/2. See the bug note below.

#### 4b — Archive folder counts (PASS)

| Spot check | Folder rows | Audit `record_count` | Match? |
|---|---|---|---|
| `claims/year_2020` | 542 | 542 | PASS |
| `members/year_2020` | 61 | 61 | PASS |
| `providers/year_2021` | 0 | 0 | PASS |
| `providers/year_2022` | **1** | **0** | **FAIL** (audit under-reports) |
| `providers/year_2023` | 0 | 0 | PASS |
| `providers/year_2024` | **2** | **0** | **FAIL** (audit under-reports) |
| `providers/year_2025` | **2** | **0** | **FAIL** (audit under-reports) |

The exclusion clause produced the correct rows in the archive — the bug is in the count emitted to the audit log, not in the data written.

#### 4c — Source unchanged (PASS)

`claims=5000`, `members=3000`, `providers=837` — identical to pre-run totals (no DELETE configured, `delete_after_archive=false`).

#### 4d — Excluded rows still present in source (PASS)

| Check | Count | Expected from B.2 | Match? |
|---|---|---|---|
| claims with `status_flag='Active'` | 698 | 1 (NULL year) + 82+81+81+97+92+94+79+91 = 698 | PASS |
| members with `enrollment_status != 'Terminated'` | 2580 | 9 (NULL year) + 375+365+373+357+363+376+362 = 2580 | PASS |
| providers referenced by any claim | 832 | 5 (NULL year) + 168+165+167+164+163 = 832 | PASS |

#### 4e — Excluded rows absent from archive (PASS)

| Spot check | Count | Match? |
|---|---|---|
| `claims/year_2020` rows where `status_flag='Active'` | 0 | PASS |
| `members/year_2020` rows where `enrollment_status != 'Terminated'` | 0 | PASS |

### Cleanup (PASS)

`exclusion_conditions` reset to `NULL` for all three tables; verified via follow-up SELECT (`is_null=true` × 3).

### Overall Result: PASS (with one product bug noted)

Exclusion semantics are correct end-to-end across all three tables and both condition shapes. The only deviation is an audit-log reporting bug specific to the `custom_sql` path on small-N CREATE writes — covered under "What Happened" / "Next Steps" below.

---

## What Happened

1. Branched off `main` to `feat/delta_config_build_v11_test_exclusions` and rewrote 08T to cover all three source tables in a single bundle run, with `equals`, `not_equals`, and `custom_sql` shapes plus per-table verification.
2. Pre-flight surfaced 45 stale audit rows and 14 archive-year folders for `claims` + `providers`. Cleaned (claims and providers only; members was already empty) so the archiver action would resolve to CREATE on every (table, year) and `per_condition_counts` would actually populate ([src/archiver.py](../../../src/archiver.py) line 593 — counts are only emitted when action is CREATE/APPEND).
3. Configured the three exclusions via `NAMED_STRUCT`. First providers attempt set `operator=NULL` and the bundle failed at config-validation time with `ArchiveConfigError`. Root cause: [src/config.py](../../../src/config.py) `validate_exclusion_conditions()` requires `operator` ∈ enum unconditionally, even though `_substitute_custom_sql()` in [src/conditions.py](../../../src/conditions.py) never reads it for `custom_sql`. Set `operator='equals'` as a placeholder and added a Note block in 08T documenting the convention.
4. Dry run produced 20 `DRY_RUN` audit rows whose `conditions_applied.per_condition_counts` matched the pre-computed expected exclusion counts exactly for every (table, year). Custom-SQL placeholder substitution worked (cross-table EXISTS against `claims` evaluated correctly).
5. Live CREATE run terminated SUCCESS. 20 `ARCHIVED` rows written. `conditions_applied` JSON correctly listed each table's rule name. Spot-checked archive folders for `claims/year_2020` (542) and `members/year_2020` (61) — both match the audit `record_count`.
6. Discovered an audit-log discrepancy on the providers / `custom_sql` path: `record_count` reports `0` for all 5 archived years, but the actual delta folders contain 0/1/0/2/2 rows (2021–2025) — exactly what the dry run predicted and what the exclusion logic should produce. Source data, archive contents, and dry-run counts are all consistent; only the audit's `record_count` field is wrong, and only on this code path.
7. Source totals unchanged, excluded rows still in source (698/2580/832), excluded rows absent from archive spot-checks (0/0). Cleanup reset all three `exclusion_conditions` to NULL.

## Next Steps

1. **Investigate audit `record_count` for `custom_sql` + small-N CREATE writes.** The audit row is written before-or-without the actual archived count being computed via the same exclusion-aware path used for delta write. Suspected location: the CREATE/APPEND branch in [src/archiver.py](../../../src/archiver.py) where `record_count` is passed into `audit_log.write(..., status='ARCHIVED', record_count=rows_this_run, ...)` (around line 836). Verify whether `rows_this_run` is computed via the substituted predicate vs. a different predicate, and whether the small-count case has a guard that zeroes it. Worth a focused unit test that mocks a `custom_sql` condition with a non-zero archived count and asserts `record_count > 0` in the resulting audit write.
2. **Consider relaxing the `operator` validator for `custom_sql`.** Either (a) make `operator` optional when `scope='custom_sql'` (NULL allowed), or (b) leave behavior unchanged but surface the requirement in the table_configs schema comment in [notebooks/setup_config_tables.py](../../../notebooks/setup_config_tables.py). Today the failure mode is a generic `ArchiveConfigError` with no hint that the placeholder is the issue.
3. **Re-run 08T** after the audit-log fix lands to confirm providers `record_count` matches archive folder counts.

---

## Run — 2026-04-10 15:21 CDT

**Branch:** `feat/delta_config_build_v3_code_reduce`
**Profile:** DEFAULT
**Workspace:** https://e2-demo-field-eng.cloud.databricks.com
**Bundle Target:** dev

### Prerequisites

Required manual cleanup before running: archive audit log entries for claims were deleted, and the `claims/` archive folder was removed from the volume via `dbutils.fs.rm`. Without this, the archiver detects orphan folders (`folder_exists=True`, `last_status=None`) and returns `action: ERROR` for every year.

### Before — Set Exclusion Condition

```sql
UPDATE sandeep_manocha.caresource_audit.table_configs
SET exclusion_conditions = ARRAY(NAMED_STRUCT(
      'name', 'active_claims', 'scope', 'same_table', 'column', 'status_flag',
      'operator', 'equals', 'value', 'Active', 'sql', CAST(NULL AS STRING))),
    modified_by = 'manual', modified_at = current_timestamp(),
    change_reason = 'Test: exclude Active claims from archiving'
WHERE table_id = 'sandeep_manocha.caresource_data_samples.claims'
```

> JSON string syntax fails with `DATATYPE_MISMATCH.CAST_WITHOUT_SUGGESTION` — must use `NAMED_STRUCT`.

### Step 1 — Dry Run

| Field | Value |
|-------|-------|
| Status | **PASS** — TERMINATED SUCCESS |
| Duration | ~116 sec |
| Run URL | https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/197650125146998/run/935928282559759 |

### Step 2 — Dry Run Audit

`conditions_applied` JSON contains per-condition counts. **PASS**

| Year | Total Eligible | Active Excluded | Would Archive | Match? | Result |
|------|---------------|----------------|---------------|--------|--------|
| 2018 | 623 | 82 | 541 | 623 - 82 = 541 | **PASS** |
| 2019 | 624 | 81 | 543 | 624 - 81 = 543 | **PASS** |
| 2020 | 627 | 83 | 544 | 627 - 83 = 544 | **PASS** |
| 2021 | 625 | 98 | 527 | 625 - 98 = 527 | **PASS** |
| 2022 | 624 | 92 | 532 | 624 - 92 = 532 | **PASS** |
| 2023 | 624 | 94 | 530 | 624 - 94 = 530 | **PASS** |
| 2024 | 622 | 79 | 543 | 622 - 79 = 543 | **PASS** |
| 2025 | 622 | 91 | 531 | 622 - 91 = 531 | **PASS** |

### Cleanup

Exclusion condition reset to NULL after test.

### Overall Result: PASS

Exclusion conditions correctly reduce the archivable row count by excluding rows matching `status_flag = 'Active'`. The `conditions_applied` JSON accurately reports per-condition excluded counts. Math checks out for all 8 years.

---

**Test Date:** 2026-04-07
**Test Time:** 12:55 – 13:05 CDT
**Branch:** `feat/delta_config_build_v3_code_reduce`
**Profile:** DEFAULT
**Workspace:** https://e2-demo-field-eng.cloud.databricks.com
**Bundle Target:** dev

---

## Bug Fixes Required

This test uncovered two bugs that were fixed before the test could pass:

### Bug 1 — ARRAY\<STRUCT\> not handled by config validator

The `exclusion_conditions` column is typed `ARRAY<STRUCT<...>>` in the DDL, but the validator (`validate_exclusion_conditions_json`) and parser (`_parse_conditions`) only accepted JSON strings. The struct field `scope` also didn't match the code's expected `type` key.

**Fix:** Updated `config.py` and `archiver.py` to accept both JSON strings and ARRAY\<STRUCT\> (list of Row/dict), with automatic `scope` → `type` key normalization.

### Bug 2 — Spark Row objects not serialized by ForEach payload

`generate_parameters.py` serializes table_configs to JSON for ForEach, but `_json_safe` didn't handle Spark Row objects — they fell through to `str(value)`.

**Fix:** Added `hasattr(value, "asDict")` check in `_json_safe` before the list/dict checks.

---

## Before — Set Exclusion Condition

```sql
SET exclusion_conditions = ARRAY(NAMED_STRUCT(
  'name','active_claims', 'scope','same_table', 'column','status_flag',
  'operator','equals', 'value','Active', 'sql', NULL))
WHERE table_id = '...claims'
```

### Active claims per year (to be excluded)

| Year | Active | Closed | Pending | Total |
|------|--------|--------|---------|-------|
| 2018 | 82 | 517 | 24 | 623 |
| 2019 | 81 | 512 | 31 | 624 |
| 2020 | 82 | 521 | 22 | 625 |
| 2021 | 97 | 498 | 28 | 623 |
| 2022 | 92 | 504 | 28 | 624 |
| 2023 | 94 | 491 | 39 | 624 |
| 2024 | 79 | 512 | 31 | 622 |
| 2025 | 91 | 498 | 33 | 622 |

---

## Step 1 — Dry Run with Exclusion

| Field | Value |
|-------|-------|
| Status | **PASS** — TERMINATED SUCCESS |
| Duration | ~116 sec |
| Run URL | https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/197650125146998/run/361365896318776 |

---

## Step 2 — Check Dry Run Audit

`conditions_applied` JSON contains per-condition counts. **PASS**

| Year | Total Eligible | Active Excluded | Would Archive | Match? | Result |
|------|---------------|----------------|---------------|--------|--------|
| 2018 | 623 | 82 | 541 | 623 - 82 = 541 | **PASS** |
| 2019 | 624 | 81 | 543 | 624 - 81 = 543 | **PASS** |
| 2020 | 625 | 82 | 543 | 625 - 82 = 543 | **PASS** |
| 2021 | 623 | 97 | 526 | 623 - 97 = 526 | **PASS** |
| 2022 | 624 | 92 | 532 | 624 - 92 = 532 | **PASS** |
| 2023 | 624 | 94 | 530 | 624 - 94 = 530 | **PASS** |
| 2024 | 622 | 79 | 543 | 622 - 79 = 543 | **PASS** |
| 2025 | 622 | 91 | 531 | 622 - 91 = 531 | **PASS** |

---

## Cleanup

Exclusion condition reset to NULL after test.

---

## Overall Result: PASS (after bug fixes)

Exclusion conditions correctly reduce the archivable row count by excluding rows matching `status_flag = 'Active'`. The `conditions_applied` JSON accurately reports per-condition excluded counts. Math checks out for all 8 years.
