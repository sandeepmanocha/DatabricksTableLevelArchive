# 13 — Stale STARTED Detection Results

## Run — 2026-04-10 16:41 CDT (claims)

> **TL;DR:** Stale STARTED (24h old) did NOT block the archiver — it proceeded normally with SKIPPED. Concurrent STARTED (1min old) correctly blocked year 2020 with SKIPPED_CONCURRENT while other years processed. After cleanup, recovery re-run succeeded. **All phases passed.**

**Branch:** `feat/delta_config_build_v3_code_reduce`
**Profile:** DEFAULT
**Workspace:** https://e2-demo-field-eng.cloud.databricks.com
**Bundle Target:** dev
**Table:** claims (scoped via `table_config_filter`)

---

### Pre-flight Check

| Check | Finding |
|-------|---------|
| Audit log (claims year 2020) | Most recent = SKIPPED (run `a9696122`). No dangling STARTED entries. |
| Archive folder (year_2020) | Present with valid Delta data (parquet + `_delta_log` + `_archive_metadata.json`) |
| Leftover fake entries | None — no `fake-stale-run-00000` or `fake-concurrent-run-00000` rows |
| Other tables | Not touched |

---

### Phase 2 — Stale STARTED (auto-recovery)

| Step | Status | Detail |
|------|--------|--------|
| 2a. Insert fake stale STARTED | **PASS** | Inserted `fake-stale-run-00000` with `created_at = current_timestamp() - 24 HOURS` → `2026-04-09T21:40:59.041Z` |
| 2b. Run archive | **PASS** | TERMINATED SUCCESS. Run URL: `https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/197650125146998/run/51870686157803` |
| 2c. Check audit log | **PASS** | Year 2020: STARTED → SKIPPED (already archived, no new data). Stale entry did NOT block the run. |
| 2d. Clean up stale entry | **PASS** | `DELETE` removed 1 row (`fake-stale-run-00000`) |

**Phase 2 audit log (year 2020, after run):**

| Status | Run ID | Created At |
|--------|--------|------------|
| SKIPPED | `4c25c090-2edf-4550-92d6-2e6d56a091c4` | 2026-04-10T21:42:41Z |
| STARTED | `4c25c090-2edf-4550-92d6-2e6d56a091c4` | 2026-04-10T21:42:37Z |
| STARTED (fake) | `fake-stale-run-00000` | 2026-04-09T21:40:59Z |

**Expectation check:**
- Archive proceeds normally (not blocked by stale entry): **YES**
- Year 2020 outcome = SKIPPED (already archived): **YES**
- WARNING in notebook logs about stale STARTED: **Expected** (not verifiable via CLI — the archiver code at `src/archiver.py:478` logs `LOGGER.warning("Found stale STARTED from run %s (%.1f hours ago)")` but notebook cell output isn't retrievable via API)

---

### Phase 3 — Concurrent STARTED (blocked + manual recovery)

| Step | Status | Detail |
|------|--------|--------|
| 3a. Insert fake concurrent STARTED | **PASS** | Inserted `fake-concurrent-run-00000` with `created_at = current_timestamp() - 1 MINUTE` → `2026-04-10T21:44:51Z` |
| 3b. Run archive | **PASS** | TERMINATED SUCCESS. Run URL: `https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/197650125146998/run/231678975282258` |
| 3c. Check audit log | **PASS** | Year 2020: STARTED → SKIPPED_CONCURRENT. All other years: SKIPPED (normal). |
| 3d. Clean up + re-run | **PASS** | Deleted `fake-concurrent-run-00000` (1 row). Re-run: TERMINATED SUCCESS. Run URL: `https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/197650125146998/run/285100793122542` |
| 3e. Verify recovery | **PASS** | Year 2020: STARTED → SKIPPED (normal). No SKIPPED_CONCURRENT from recovery run. |

**Phase 3 audit log (run `90227760`, all years):**

| Year | Status | Mode | Records |
|------|--------|------|---------|
| 2018 | SKIPPED | SKIP | 0 |
| 2019 | SKIPPED | SKIP | 0 |
| **2020** | **SKIPPED_CONCURRENT** | **-** | **0** |
| 2021 | SKIPPED | SKIP | 0 |
| 2022 | SKIPPED | SKIP | 0 |
| 2023 | SKIPPED | SKIP | 0 |
| 2024 | SKIPPED | SKIP | 0 |
| 2025 | SKIPPED | SKIP | 0 |
| 2026 | SKIPPED | SKIP | 0 |

**Recovery run audit (all years SKIPPED, including year 2020):** Confirmed — year 2020 processed normally after fake concurrent entry was removed.

---

## What Happened

1. **Pre-flight:** Claims year 2020 was in a clean state — most recent entry was SKIPPED from a prior run. No leftover fake entries existed. Archive folder was intact with valid Delta data.

2. **Phase 2 (Stale STARTED):** Inserted a fake STARTED entry timestamped 24 hours ago (`fake-stale-run-00000`). This is well above the 4-hour `stale_started_threshold_hours`. Ran the archiver — it treated the stale entry as abandoned and proceeded normally. Year 2020 resulted in SKIPPED (already archived, no new data). The archiver code confirms a WARNING is logged at `src/archiver.py:478`, but notebook cell output isn't retrievable via the Jobs API. Cleaned up the fake entry afterwards.

3. **Phase 3 (Concurrent STARTED):** Inserted a fake STARTED entry timestamped 1 minute ago (`fake-concurrent-run-00000`). This is well below the 4-hour threshold, so the archiver treated it as an active concurrent run. Year 2020 was blocked with SKIPPED_CONCURRENT while all other years (2018–2026) processed normally as SKIPPED. After deleting the fake entry, the recovery re-run processed year 2020 normally as SKIPPED.

## Next Steps

1. **All test assertions passed.** No code changes required.
2. **Notebook log verification:** The stale WARNING message (`"Found stale STARTED from run fake-stale-run-00000 (24.0 hours ago). Treating as abandoned."`) cannot be verified via CLI — would need to check the notebook run output in the Databricks UI at the Phase 2b run URL.
3. **Proceed to test 14 (Rehydration)** when ready.
