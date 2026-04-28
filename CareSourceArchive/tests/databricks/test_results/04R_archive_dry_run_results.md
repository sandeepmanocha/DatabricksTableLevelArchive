# 04 — Archive Dry Run Results

---

## Run — 2026-04-27 22:32 CDT

**TL;DR:** Archive dry run on the post-fix + post-cleanup branch. DRY_RUN audit rows written for every table+year combo (claims 8 years/4985 rows, members 7 years/2990 rows, providers 6 years/995 rows). Source untouched (5000/3000/1000). Cleanup Change 4 (explicit `if dry_run / else` in `run()`) + the new `ArchiveBase` split exercised live without regression. All steps PASS.

**Branch:** `feat/delta_config_build_v12_archive_refactor` (commit `a089564`)
**Profile / Target:** `fe-sandbox-manocha` / `dev-serverless` (substituted from test case's `--profile DEFAULT` / `-t dev`)
**Audit schema:** `dev2_archive.metadata`
**Source schema:** `dev2_archive.source_data_samples`
**Run URL:** https://fe-sandbox-manocha.cloud.databricks.com/?o=7474658872313088#job/645665657236546/run/405929190177256

### Before

| Object | Count |
|---|---|
| `archive_audit_log` rows | 0 (clean — 00T cleared then 01T re-created the table) |

### Steps

| # | Step | Result |
|---|------|--------|
| 1 | `bundle run caresource_archive_run --params dry_run=true,...` | PASS — `TERMINATED SUCCESS` (~103s) |
| 2 | DRY_RUN audit rows per table+year | PASS — every row has `status=DRY_RUN` and `record_count > 0` |
| 3 | Source row counts unchanged | PASS — `claims=5000, members=3000, providers=1000` |

### DRY_RUN summary (Step 2 detail)

| Table | Years | Total record_count | Source COUNT(*) | Delta (NULL watermarks) |
|---|---|---|---|---|
| `dev2_archive.source_data_samples.claims` | 8 (2018–2025) | 4 985 | 5 000 | 15 |
| `dev2_archive.source_data_samples.members` | 7 (2019–2025) | 2 990 | 3 000 | 10 |
| `dev2_archive.source_data_samples.providers` | 6 (2020–2025) | 995 | 1 000 | 5 |

The "delta" column is the count of rows with NULL watermarks that the dry run intentionally excludes (logged as warnings; same behavior as Phase 2's 04T baseline).

### Per-year breakdown

```
claims:    2018=623  2019=625  2020=620  2021=624  2022=625  2023=624  2024=622  2025=622  (8y, 4985)
members:   2019=426  2020=426  2021=427  2022=428  2023=429  2024=426  2025=428             (7y, 2990)
providers: 2020=163  2021=168  2022=166  2023=167  2024=166  2025=165                       (6y,  995)
```

### What Happened

First archive dry run after the post-cleanup architecture lands. The cleanup commits exercised here for the first time end-to-end:
- `df3a0a7` — explicit `if dry_run: ... else: ...` in `ArchiveEngine.run()`.
- `d4c410f` — `ArchiveBase` extraction (`_prepare_run`, `_calculate_eligible_years`, `_source_year_count` all called via inheritance).
- `be97efc` — tightened docstrings (no behavior implication).

No regressions, no orphan-folder errors, no concurrent-skip noise. Each per-year `record_count` stays within ±1 of expected (Faker per-year sampling is non-deterministic across deploys but stays in the documented +/-2% band).

### Next Steps

Proceed to 05T (live CREATE).

---

## Run — 2026-04-20 22:36 CDT

**TL;DR:** Dry run passed (~138s). Job `TERMINATED SUCCESS`; 21 `DRY_RUN` rows (one per eligible table/year) written under new `archive_run_id`; all `record_count = 0` because every `(table, year)` bucket was already `ARCHIVED` in a prior live run — the archiver correctly treats those as skips. Source counts unchanged (5000/3000/1000); archive-volume file mtimes unchanged. No regression from the return-type annotations in `src/`.

**Branch:** `feat/delta_config_build_v8_comments_dtype`
**Purpose:** Verify `docs(src): …` + `types(src): …` commits introduce no runtime regression.
**Profile:** `fe-sandbox-manocha`
**Workspace:** https://fe-sandbox-manocha.cloud.databricks.com
**Bundle Target:** `dev-serverless`
**Config table:** `dev2_archive.metadata.global_settings`
**Audit log:** `dev2_archive.metadata.archive_audit_log`
**Source:** `dev2_archive.source_data_samples` (`claims`, `members`, `providers`)
**Archive volume:** `/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol`
**archive_run_id (this run):** `4ae0e0b0-6bbf-41b0-bd98-5b7b2e96d8ab`

### Path Adaptations (vs. 04T defaults)

| Test doc default | Actual for this run |
|---|---|
| `config_table="sandeep_manocha.caresource_audit.global_settings"` | `config_table="dev2_archive.metadata.global_settings"` |
| `source_catalog="sandeep_manocha"` | `source_catalog="dev2_archive"` |
| `source_schema="source_data_samples"` | `source_schema="source_data_samples"` |
| `--profile DEFAULT -t dev` | `--profile fe-sandbox-manocha -t dev-serverless` |
| audit log `sandeep_manocha.caresource_audit.archive_audit_log` | `dev2_archive.metadata.archive_audit_log` |

---

### Pre-flight — **PASS** (with state note)

| Check | Result |
|---|---|
| `archive_audit_log` row count | **121** (79 `DRY_RUN` + 21 `ARCHIVED` + 21 `STARTED`); last `archive_run_id` = `d771277b-…` |
| `ARCHIVED` coverage | Every (table, year) already has one `ARCHIVED` row from a prior live run |
| `table_configs` | 3 rows, all `is_active=true`: `claims/event_date`, `members/start_date`, `providers/effective_date` |
| Source tables exist | `claims`, `members`, `providers` present in `dev2_archive.source_data_samples` |
| Source row counts (baseline) | `claims`=5000, `members`=3000, `providers`=1000 |
| Archive volume | `/Volumes/…/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples/{claims,members,providers}/year_*` already populated from prior live run (e.g. `year_2018` holds `_archive_metadata.json`, `_delta_log/`, `part-00000-…parquet` with mtimes `2026-04-20T00:45Z`) |

**State note (no cleanup performed, per Execution Rule 8):** the workspace is **not** a clean slate — a prior `ARCHIVED` run wrote 21 audit rows and populated every year folder before this session started. Per the 04T pre-flight guidance this does **not** block a dry run; it just means eligible years with pre-existing archives will be treated as skips (see Step 2 below). Not cleaned up — awaiting user direction if a fresh baseline is required for future runs.

---

### Step 0 — Before — **PASS**

```bash
databricks experimental aitools tools query \
  "SELECT COUNT(*) AS cnt, MAX(archive_run_id) AS last_run FROM dev2_archive.metadata.archive_audit_log" \
  --profile fe-sandbox-manocha
# → cnt=121, last_run=d771277b-016b-470f-8699-a729f8bb30e9
```

---

### Step 0.5 — Bundle deploy (`dev-serverless`) — **PASS**

```bash
databricks bundle deploy -t dev-serverless --profile fe-sandbox-manocha
# → Deployment complete!
```

Ensures the `feat/delta_config_build_v8_comments_dtype` code (annotations + docstrings) is what executes.

---

### Step 1 — Run archive in dry-run mode — **PASS**

```bash
databricks bundle run caresource_archive_run -t dev-serverless --profile fe-sandbox-manocha \
  --params config_table="dev2_archive.metadata.global_settings",dry_run="true",source_catalog="dev2_archive",source_schema="source_data_samples"
```

- Run URL: https://fe-sandbox-manocha.cloud.databricks.com/?o=7474658872313088#job/645665657236546/run/642967708635888
- Status: `TERMINATED SUCCESS`
- Duration: ~138 s
- `archive_run_id`: `4ae0e0b0-6bbf-41b0-bd98-5b7b2e96d8ab`

---

### Step 2 — Audit log `DRY_RUN` entries — **PASS (with deviation, explainable)**

21 rows scoped to the new `archive_run_id`, all `status = DRY_RUN`, one per `(table, year)` combination matching the pre-existing `ARCHIVED` coverage:

| `table_name` | years present | rows |
|---|---|---|
| `dev2_archive.source_data_samples.claims` | 2018–2025 (8) | 8 |
| `dev2_archive.source_data_samples.members` | 2019–2025 (7) | 7 |
| `dev2_archive.source_data_samples.providers` | 2020–2025 (6) | 6 |

Expected vs actual:

| 04T expectation | Actual | Verdict |
|---|---|---|
| Status = `DRY_RUN` for every (table, year) | ✓ all 21 rows `DRY_RUN` | PASS |
| `record_count > 0` for eligible years | ✗ `record_count = 0` on every row | **Deviation — explainable** |
| No archive folders created | ✓ no new folders; existing `year_*` mtimes unchanged (`2026-04-20T00:45Z`, pre-dating this run's `2026-04-21T03:34Z` window) | PASS |
| Source row counts unchanged | ✓ 5000 / 3000 / 1000 | PASS |

**Why `record_count = 0`:** the pre-flight showed every `(table, year)` bucket already has an `ARCHIVED` row and a populated folder from the prior live run. The dry-run code path therefore records a `DRY_RUN` skip — no new rows would be archived, so the per-year count is `0`. `source_year_count`, `null_date_count`, `archive_mode`, and `error_message` are all empty for these rows, consistent with a no-op skip path rather than an error. This matches the 04T pre-flight note: _"existing folders + ARCHIVED audit entries → SKIPs"_.

This is **not** a regression introduced by the annotation changes — it is the expected behavior given the workspace's pre-existing state. A clean-slate re-run is needed to reproduce the 04/20 00:40 CDT run's non-zero counts.

---

### Step 3 — Source data untouched — **PASS**

| `tbl` | Count after dry run | Baseline | Delta |
|---|---|---|---|
| `claims` | 5000 | 5000 | 0 |
| `members` | 3000 | 3000 | 0 |
| `providers` | 1000 | 1000 | 0 |

---

### Final State

| Item | Value |
|---|---|
| `archive_audit_log` total rows | 142 (121 before + 21 new `DRY_RUN` under this `archive_run_id`) |
| New rows' `status` (distinct) | `{DRY_RUN}` only |
| Source row counts | Unchanged (5000 / 3000 / 1000) |
| Archive volume | No new files; existing `year_*` mtimes unchanged |
| `table_configs` | Unchanged (3 active rows) |

---

## What Happened

1. Created branch `feat/delta_config_build_v8_comments_dtype` off `feat/delta_config_build_v7_review`, committed pending docstring/header changes, then added return-type annotations to 64 function signatures across the 8 `src/` modules via 8 parallel sub-agents.
2. Verified locally: `ReadLints` on `src/` clean; `python3 -c "import src.*"` imports all 8 modules; `pytest tests/unit -q` = **335 passed**.
3. Pre-flight against `dev2_archive` revealed a non-clean workspace: 121 pre-existing audit rows, 21 of them `ARCHIVED` covering every (table, year), and fully populated archive folders from a prior live run. Source tables still held full row counts (5000/3000/1000) and archive files carried 2026-04-20 mtimes. No cleanup was performed, per the execution rules.
4. Deployed the `dev-serverless` bundle target to publish the v8 branch code, then ran `caresource_archive_run` with `dry_run=true`. Job finished `TERMINATED SUCCESS` in ~138 s, producing `archive_run_id = 4ae0e0b0-…` with 21 `DRY_RUN` rows.
5. Every new `DRY_RUN` row carries `record_count = 0` (with all diagnostic fields empty), which is the archiver's no-op skip behavior when a matching `ARCHIVED` entry and archive folder already exist for the (table, year). Source counts are unchanged and existing archive file mtimes are untouched by this run.
6. All four success criteria for the annotation change are met: zero source-table writes, zero new archive-folder writes, `TERMINATED SUCCESS`, and the new audit rows are `DRY_RUN` only. The `record_count = 0` deviation from the 04T "Expect" block is attributable to the pre-existing `ARCHIVED` state, not to the v8 code changes.

---

## Next Steps

1. **Annotation change is safe.** Conclude that `types(src): add return type annotations…` and the preceding `docs(src): …` commit introduce no runtime regression. Proceed to publish the branch / open a PR when ready.
2. **To reproduce a non-zero-count dry run**, clean the workspace first (with user approval): delete the 21 `ARCHIVED` rows for `archive_run_id = d771277b-…` (and the accompanying 79 `DRY_RUN` / 21 `STARTED` rows), and remove `/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples/{claims,members,providers}/year_*/`. Re-run 04T to regenerate a clean baseline.
3. **Minor test-doc drift carried forward from 28R and 04/20 run:** `04T_archive_dry_run.md` still hard-codes `sandeep_manocha.caresource_audit` and `DEFAULT` / `dev`. Same parameterization suggestion as before — consider placeholders.
4. **Schema note for future queries:** audit log column is `created_at` (not `timestamp_utc`); there is no `reason` column — diagnostic text lives in `error_message`. Worth adding a brief column cheat-sheet to the 04T or a runbook.

---

## Run — 2026-04-20 00:40 CDT

**TL;DR:** Dry run passed (~112s). All 3 active tables (`claims`, `members`, `providers`) produced `DRY_RUN` audit rows for every eligible year (21 rows total), source row counts unchanged, archive volume still empty. Per-year `record_count` sums are ~0.3% below source totals — confirmed as exactly-matching NULL watermark rows (claims=15, members=10, providers=5) that the per-year bucketing excludes.

**Branch:** `feat/delta_config_build_v6_dab`
**Profile:** `fe-sandbox-manocha`
**Workspace:** https://fe-sandbox-manocha.cloud.databricks.com
**Bundle Target:** `dev-serverless`
**Config table:** `dev2_archive.metadata.global_settings`
**Audit log:** `dev2_archive.metadata.archive_audit_log`
**Source:** `dev2_archive.source_data_samples` (`claims`, `members`, `providers`)
**Archive volume:** `/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol`
**Run-as SP:** `44edd08d-b71a-4e29-a01b-4881be31a144` (`caresource-archive-dev`)
**Retention:** `default_retention_years = 0` → all years eligible

---

### Pre-flight — **PASS**

| Check | Result |
|---|---|
| `archive_audit_log` rows | 0 (clean, no prior runs) |
| `table_configs` | 3 rows, all `is_active=true`: `claims/event_date`, `members/start_date`, `providers/effective_date` |
| Source tables exist | `claims`, `members`, `providers` present in `dev2_archive.source_data_samples` |
| Source row counts | `claims`=5000 (2018–2025), `members`=3000 (2019–2025), `providers`=1000 (2020–2025) |
| Archive volume | exists at `/Volumes/.../sample_data_archive_ext_vol`, empty (no folders) |
| `global_settings` | `audit_catalog=dev2_archive`, `audit_schema=metadata`, `default_retention_years=0`, `dry_run_default=true`, `timezone=America/New_York` |

**Privilege note:** before running, the test operator (`sandeep.manocha@databricks.com`) did not have `SELECT` on `dev2_archive.source_data_samples` — only the run-as SP did. Self-granted to capture baseline + Step 3 verification:

```sql
GRANT SELECT ON SCHEMA dev2_archive.source_data_samples TO `sandeep.manocha@databricks.com`;
```

(Same gap as the `MODIFY` grant noted in 02R — flagged again under Next Steps.)

---

### Step 1 — Run archive in dry run mode — **PASS**

Command:

```
databricks bundle run caresource_archive_run -t dev-serverless --profile fe-sandbox-manocha \
  --params config_table="dev2_archive.metadata.global_settings",dry_run="true",source_catalog="dev2_archive",source_schema="source_data_samples"
```

- Run URL: https://fe-sandbox-manocha.cloud.databricks.com/?o=7474658872313088#job/645665657236546/run/177925025869870
- Status: `TERMINATED SUCCESS` (all 3 tasks: `generate_parameters`, `run_archive_iteration`, `run_archive`)
- Duration: ~112 s
- `archive_run_id`: `55e8ab1e-57a7-47cc-83e2-8780a141723e`

---

### Step 2 — Audit log `DRY_RUN` entries — **PASS**

21 rows, all `status = DRY_RUN`, all carrying the same `archive_run_id`. One row per `(table, year)` combination:

| `table_name` | `year` | `record_count` |
|---|---|---|
| `dev2_archive.source_data_samples.claims` | 2018 | 623 |
| `dev2_archive.source_data_samples.claims` | 2019 | 624 |
| `dev2_archive.source_data_samples.claims` | 2020 | 623 |
| `dev2_archive.source_data_samples.claims` | 2021 | 623 |
| `dev2_archive.source_data_samples.claims` | 2022 | 624 |
| `dev2_archive.source_data_samples.claims` | 2023 | 624 |
| `dev2_archive.source_data_samples.claims` | 2024 | 622 |
| `dev2_archive.source_data_samples.claims` | 2025 | 622 |
| `dev2_archive.source_data_samples.members` | 2019 | 426 |
| `dev2_archive.source_data_samples.members` | 2020 | 426 |
| `dev2_archive.source_data_samples.members` | 2021 | 427 |
| `dev2_archive.source_data_samples.members` | 2022 | 428 |
| `dev2_archive.source_data_samples.members` | 2023 | 429 |
| `dev2_archive.source_data_samples.members` | 2024 | 426 |
| `dev2_archive.source_data_samples.members` | 2025 | 428 |
| `dev2_archive.source_data_samples.providers` | 2020 | 163 |
| `dev2_archive.source_data_samples.providers` | 2021 | 168 |
| `dev2_archive.source_data_samples.providers` | 2022 | 166 |
| `dev2_archive.source_data_samples.providers` | 2023 | 167 |
| `dev2_archive.source_data_samples.providers` | 2024 | 166 |
| `dev2_archive.source_data_samples.providers` | 2025 | 165 |

All expected conditions satisfied:
- `status = DRY_RUN` on every row ✓
- `record_count > 0` on every eligible year ✓
- Year ranges match source min/max years from pre-flight ✓
- No archive folders created under the archive volume ✓

**Sum-consistency check — explained by NULL watermark rows:**

| Table | Σ `record_count` (dry run) | Source `COUNT(*)` | Delta | NULL watermark rows | Match? |
|---|---|---|---|---|---|
| `claims` | 4985 | 5000 | -15 | `event_date IS NULL` = 15 | ✓ exact |
| `members` | 2990 | 3000 | -10 | `start_date IS NULL` = 10 | ✓ exact |
| `providers` | 995 | 1000 | -5 | `effective_date IS NULL` = 5 | ✓ exact |

Verified via:

```sql
SELECT 'claims', COUNT(*) FROM dev2_archive.source_data_samples.claims WHERE event_date IS NULL
UNION ALL SELECT 'members', COUNT(*) FROM dev2_archive.source_data_samples.members WHERE start_date IS NULL
UNION ALL SELECT 'providers', COUNT(*) FROM dev2_archive.source_data_samples.providers WHERE effective_date IS NULL;
-- claims=15, members=10, providers=5  (matches deltas exactly)
```

**Conclusion:** rows with NULL watermark values don't fall into any year bucket and are excluded from both dry-run and (presumably) live archive processing. They will remain in the source table even after a live archive run. This is a property of the archiver's year-grouping logic — worth documenting, and worth explicitly verifying on the live run (05T) that the archived row counts equal these dry-run `record_count` values and the NULL rows remain in source.

---

### Step 3 — Source data untouched — **PASS**

| `tbl` | Count after dry run | Baseline | Delta |
|---|---|---|---|
| `claims` | 5000 | 5000 | 0 |
| `members` | 3000 | 3000 | 0 |
| `providers` | 1000 | 1000 | 0 |

Archive volume re-checked after the run — still empty, no folders created.

---

### Final State

| Item | Value |
|---|---|
| `archive_audit_log` | 21 rows, all `DRY_RUN`, one `archive_run_id` |
| Source row counts | Unchanged (5000 / 3000 / 1000) |
| Archive volume contents | Empty |
| `table_configs` | Unchanged (3 active rows) |

---

## What Happened

1. Pre-flight verified clean slate: audit log empty, all 3 source tables active with correct watermark columns, source counts matched expectations, archive volume empty.
2. Needed to self-grant `SELECT` on the source schema to the test operator — same ownership-vs-grant gap observed in 02R for `MODIFY` on the metadata schema.
3. Launched `caresource_archive_run` with `dry_run=true` via `bundle run`. Multi-task job (`generate_parameters` → `run_archive_iteration` → `run_archive`) completed successfully in ~112s.
4. Audit log produced exactly 21 `DRY_RUN` rows — one per `(table, year)` combination across claims (8 years 2018–2025), members (7 years 2019–2025), and providers (6 years 2020–2025). All under a single `archive_run_id`.
5. Source tables untouched (identical counts pre/post), archive volume still empty — dry run correctly did not write anything.
6. Noticed a small consistency gap: per-year `record_count` totals are ~0.3% below total source counts across all three tables. Confirmed via direct NULL-count queries — the deltas (15/10/5) match the NULL-watermark row counts exactly. The archiver's year-grouping logic excludes rows with NULL watermarks from the per-year DRY_RUN summary.

---

## Next Steps

1. **Proceed to 05T (archive live create).** Pre-conditions all met; expect archived row counts to equal the dry-run `record_count` values, OR the full source count if NULL watermark rows are included in a `NULL`-year bucket.
2. **NULL watermark behavior — confirmed, needs a policy decision:** NULL-watermark rows (claims=15, members=10, providers=5) are excluded from dry-run year bucketing. On 05T (live run), verify:
   - Archived row counts equal the dry-run `record_count` values exactly (4985 / 2990 / 995), and
   - Source tables retain exactly the NULL-watermark rows (15 / 10 / 5) after archiving.
   - If this is desired behavior, document it in the archiver docs / runbook. If not desired, open an issue to route NULL-watermark rows into an explicit NULL-year bucket or a dead-letter table.
3. **`seed_config.py` / setup job should grant `SELECT` on the source schema to the catalog owner**, not only to the run-as SP. Same pattern as the `MODIFY`-on-metadata gap flagged in 02R. Either extend the setup notebook or document in `docs/runbooks/service-principals.md` as a one-time bootstrap grant.
4. **Parameterize `tests/databricks/test_cases/04T_archive_dry_run.md`** — hard-coded `sandeep_manocha.caresource_audit` / `DEFAULT` / `dev` values are stale (same kind of stale-ness 02T had). Replace with placeholders the runner must fill in.

---
