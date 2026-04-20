# 04 — Archive Dry Run Results

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
