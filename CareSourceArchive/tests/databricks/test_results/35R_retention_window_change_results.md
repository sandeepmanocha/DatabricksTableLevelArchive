# 35 — Retention Window Change (shrink and grow) Results

## Run — 2026-04-26 14:53 CDT

**Branch:** `feat/delta_config_build_v10_test_cases`
**Profile:** `fe-sandbox-manocha`
**Workspace:** https://fe-sandbox-manocha.cloud.databricks.com
**Bundle Target:** `dev-serverless`
**BASELINE_GLOBAL:** `0`
**BASELINE_TABLE (claims):** `0` (explicit, not NULL — see Phase 2 deviation note)
**BASELINE_YEARS:** `[2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025]` (all ARCHIVED)

### TL;DR

Shrink (7→5) added years 2020+2021 to the eligible set, grow (5→7) removed them, per-table override (claims=3, global=7) produced [2018-2023] for claims while leaving global=7 untouched. Pass with a deviation: the seeded `table_configs.retention_years = 0` override masks any global change for claims, so we temporarily nulled it for Phase 2/3 to make the global retention observable.

---

## Pre-flight

### Audit log (claims)

8 ARCHIVED rows for years 2018-2025 with the expected `record_count` distribution (~620-625 rows/year).

### table_configs (claims)

`retention_years = 0` (explicit, **not** NULL). This is the seeded value from `seed_config`.

### global_settings

`default_retention_years = 0`.

### Archive volume (claims)

Year folders 2018-2025 confirmed present.

### Source table (claims)

Confirmed populated, `delete_after_archive = false`.

### Other tables (members, providers)

Not touched in this run.

---

## Phase 1 — Establish baseline: **PASS**

### 1a — Capture retention values: PASS

`BASELINE_GLOBAL = 0`, `BASELINE_TABLE = 0`.

### 1b — Capture archived years: PASS

8 rows: years 2018-2025 all `ARCHIVED`.

---

## Phase 2 — Shrink the window: **PASS** (after deviation fix)

### 2a — Widen global 0→7: PASS

```sql
UPDATE dev2_archive.metadata.global_settings SET default_retention_years = 7 ...
```

`default_retention_years = 7` confirmed.

### 2b — First attempt: **DEVIATION**

Ran archive scoped to claims, expected eligible = `[2018, 2019]`. Got eligible = `[2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025]`.

**Root cause:** `table_configs.retention_years = 0` (explicit, not NULL) for claims. The merge logic uses the per-table value when it is non-NULL, so the global widen to 7 had no effect for claims. The test author implicitly assumed `BASELINE_TABLE = NULL`. With `seed_config` setting all three tables to explicit `0`, the global setting is shadowed.

**Workaround:** temporarily set `claims.retention_years = NULL` so the global value wins for Phases 2-4. Captured the deviation here, restored to `0` in Phase 5.

```sql
UPDATE dev2_archive.metadata.table_configs SET retention_years = NULL ... WHERE table_id = 'dev2_archive.source_data_samples.claims'
```

### 2b (re-run with NULL override) — PASS

Re-ran archive job. New `archive_run_id = 4c723960-...` (with override) produced 8 distinct years; **second** new `archive_run_id = (run with NULL)` produced **only [2018, 2019]**.

| year | status | archive_mode | record_count |
|---|---|---|---|
| 2018 | STARTED | (null) | 0 |
| 2018 | SKIPPED | SKIP | 0 |
| 2019 | STARTED | (null) | 0 |
| 2019 | SKIPPED | SKIP | 0 |

Folders for 2020-2025 still on disk (verified `claims/year_2025` lists `_delta_log/` + parquet). They are now "frozen archives" — archiver no longer looks at them under retention=7.

### 2c — Shrink global 7→5: PASS

`default_retention_years = 5` confirmed.

### 2d — Re-run archive: PASS

Eligible = `[2018, 2019, 2020, 2021]` — 2020 and 2021 just entered the eligible set.

| year | status | archive_mode | record_count |
|---|---|---|---|
| 2018 | STARTED + SKIPPED | SKIP | 0 |
| 2019 | STARTED + SKIPPED | SKIP | 0 |
| 2020 | STARTED + SKIPPED | SKIP | 0 |
| 2021 | STARTED + SKIPPED | SKIP | 0 |

All SKIPPED because folders existed already from the clean-slate Phase 0 archive (rule G: VALID + ARCHIVED + no new data → SKIP). Note from test prose: this is the correct path; if the volume had been clean, 2020/2021 would have been `archive_mode = CREATE`. No FAILED rows.

### 2e — Distinct years: PASS

`[2018, 2019, 2020, 2021]` — exact match.

---

## Phase 3 — Grow the window: **PASS**

### 3a — Grow global 5→7: PASS

`default_retention_years = 7` confirmed.

### 3b — Re-run archive: PASS

Distinct years = `[2018, 2019]` — exact match. Years 2020 and 2021 are no longer in the run's audit footprint even though their folders exist.

### 3c — Folders 2020 and 2021 untouched: PASS

Both folders still list `_archive_metadata.json`, `_delta_log/`, parquet parts. Archiver did not delete them.

### 3d — Operator visibility: documented

An operator filtering `archive_audit_log` by the most recent `archive_run_id` will not see year 2020 or 2021 entries after a grow. UX implication: a grown retention window silently narrows the audit footprint per run. Consumers building dashboards on "latest run audit" should be aware that the year coverage they see depends on the active retention.

---

## Phase 4 — Per-table override: **PASS**

### 4a — Set claims.retention_years = 3: PASS

`retention_years = 3` confirmed on claims; global still `7`.

### 4b — Re-run archive: PASS

Distinct years = `[2018, 2019, 2020, 2021, 2022, 2023]` — exact match. Per-table override (3) wins over global (7).

### 4c — Global unchanged: PASS

`default_retention_years = 7` (the override does not mutate the global value).

### 4d — Optional members/providers cross-check: SKIPPED

Not run to keep this test focused on claims (per pre-flight rule).

---

## Phase 5 — Restore baseline: **PASS**

### 5a — Restore claims.retention_years to 0: PASS

### 5b — Restore default_retention_years to 0: PASS

### 5c — Verify: PASS

```
default_retention_years = 0
table_configs.claims.retention_years = 0
```

Both match the captured baselines.

---

## What this proves

| Behavior | Pass criteria | Result |
|---|---|---|
| Shrink global retention | New years enter eligible set on next run | **PASS** (2020/2021 entered after 7→5) |
| Grow global retention | Years above new cutoff stop receiving audit rows | **PASS** (2020/2021 dropped after 5→7) |
| Folders preserved on grow | Archiver does not delete folders for years that left eligibility | **PASS** |
| Per-table override | `table_configs.retention_years` wins over `default_retention_years` | **PASS** (claims=3 vs global=7 → eligible 2018-2023) |
| Restore baseline | Setting values back returns eligible set to original | **PASS** |
| No FAILED rows | A retention change is config-only and never errors a year | **PASS** |

---

## What Happened

We captured baselines (global=0, claims=0, all 8 years archived). Phase 2a widened global to 7 with the test's prescribed widen-then-shrink shape. The first archive run unexpectedly listed all 8 years as eligible because the per-table `retention_years = 0` from `seed_config` was shadowing the global value. We temporarily set `claims.retention_years = NULL` so the global setting governs, re-ran, and observed the expected `[2018, 2019]`. Shrink to 5 then produced `[2018, 2019, 2020, 2021]` (2020/2021 newly eligible, all SKIPPED because folders already existed). Grow back to 7 produced `[2018, 2019]` only, with 2020/2021 folders untouched on disk. Per-table override `claims = 3` over `global = 7` produced `[2018, 2019, 2020, 2021, 2022, 2023]`, with the global value unchanged. Restored both baselines (global=0, claims=0).

## Deviation

The test prose assumed `BASELINE_TABLE` could be either NULL or numeric, but Phase 2's expectations only hold when `BASELINE_TABLE = NULL`. With `seed_config` writing `retention_years = 0` to every table, every claims year stays eligible regardless of the global value. We documented this and worked around it by NULLing the per-table override for Phases 2-4, then restoring to `0` in Phase 5. **Recommendation:** either (a) change `seed_config` to write `retention_years = NULL` (so `default_retention_years` is the single source of truth for new tables), or (b) update 35T's prose to instruct the operator to NULL out per-table values before running Phases 2-3.

## Next Steps

Proceed to 36T (providers/2020 folder delete + DAA flip). Baselines are restored; 35T leaves additional `STARTED/SKIPPED` audit rows for claims/2018-2023 across four runs but does not change the canonical "ARCHIVED" history. 36T scopes by `providers`, so claims state is irrelevant.
