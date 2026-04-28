# 25 — End-to-End: Archive then Rehydrate — Results

> **Note:** `unified_view_suffix` is now a configurable parameter (default `_unified`). It can be passed via `--params unified_view_suffix=<value>` or set in the notebook widget. When not specified, the unified view uses the `_unified` suffix (e.g. `claims_unified`).

## Run — 2026-04-24 11:30 CDT post-utils_optimize (PASS — full round-trip verified)

**TL;DR:** **ALL 8 STEPS PASS.** Archive job archived all 8 years of `claims` (2018–2025, 4985 rows total, 15 NULL event_date rows excluded) in 2m48s on `dev-serverless`. Rehydrate populated `dev2_archive.rehydrated.claims_unified` with 623/623 rows for 2020/2021 — exact match with archive audit `record_count`. The `archive_folder_orphan` misclassification that blocked the prior run (tag-predecessor `d838774`) is gone; post-refactor (commit `6a61448`, tag `utils_optimize`) uses `archive_state_and_count` which probes via `SELECT COUNT(*) FROM delta.\`<path>\`` and correctly surfaces `PATH_NOT_FOUND` as `archive_folder_missing`.

**Branch:** `feat/delta_config_build_v9_del_data_phase2` @ `6a61448` (tag `utils_optimize`)
**Profile:** `fe-sandbox-manocha`
**Target:** `dev-serverless`
**Workspace:** https://fe-sandbox-manocha.cloud.databricks.com
**Archive run URL:** https://fe-sandbox-manocha.cloud.databricks.com/?o=7474658872313088#job/645665657236546/run/779849100111057
**Rehydrate run URL:** https://fe-sandbox-manocha.cloud.databricks.com/?o=7474658872313088#job/646384074228167/run/726803345109696

### Resolved Parameters

| Parameter | Value |
|---|---|
| `PROFILE` | `fe-sandbox-manocha` |
| `TARGET` | `dev-serverless` |
| `SOURCE_CATALOG` | `dev2_archive` |
| `SOURCE_SCHEMA` | `source_data_samples` |
| `CONFIG_TABLE` | `dev2_archive.metadata.global_settings` |
| `ARCHIVE_VOL` | `/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples` |
| `AUDIT_TABLE` | `dev2_archive.metadata.archive_audit_log` |
| `REHYDRATE_TARGET_SCHEMA` | `rehydrated` |
| `REHYDRATION_AUDIT_TABLE` | `dev2_archive.metadata.rehydration_audit_log` |

### Test-case deviations

1. **Runtime scoping to claims** — `table_configs` has `claims`, `members`, `providers` all `is_active=true` (scanner idempotency fix widened watermark detection so all three tables register). Rather than toggle `is_active` on the other two, scoped the archive run at runtime with `--params 'table_config_filter=source_table = '"'"'claims'"'"''`. No state changes to `members` / `providers`.
2. **Audit filter** — 25T Step 2 uses `WHERE table_name = 'claims'` but the audit column stores the full 3-part name (`dev2_archive.source_data_samples.claims`). Re-queried with the FQ filter. Test-case doc could be tightened, not a code issue.
3. **Schema permissions** — rehydrate job runs as SP `44edd08d-b71a-4e29-a01b-4881be31a144`, which owns the created schema. Human-user query in Step 6 needed a one-time `GRANT USE SCHEMA, SELECT ON SCHEMA dev2_archive.rehydrated TO \`sandeep.manocha@databricks.com\`` before reading `claims_unified`. Not a test failure — a workspace RBAC detail.

### Pre-flight — **PASS**

| Check | Result |
|---|---|
| `claims` source counts by year | 623/624/623/623/624/624/622/622 for 2018–2025 + 15 NULL event_date; total 5000. |
| `ARCHIVED` rows for `claims` in `archive_audit_log` | 0 (post prior-run failure, no archives recorded). |
| Archive volume `/Volumes/.../source_data_samples/claims/` | `no such directory` — truly empty. |
| `rehydrated` schema in `dev2_archive` | Does not exist. |
| `table_configs.claims` flags | `watermark_column=event_date`, `is_active=true`, `delete_after_archive=false`. |

### Step 1 — Archive claims (live run, scoped) — **PASS**

- Job `caresource_archive_run` → TERMINATED SUCCESS in ~168 s.
- All 8 years (2018–2025) processed.

### Step 2 — Archive audit log — **PASS**

| Year | Status | record_count | archive_mode | watermark_value |
|---|---|---|---|---|
| 2018 | STARTED → ARCHIVED | 623 | CREATE | 2018-12-31 |
| 2019 | STARTED → ARCHIVED | 624 | CREATE | 2019-12-31 |
| 2020 | STARTED → ARCHIVED | 623 | CREATE | 2020-12-30 |
| 2021 | STARTED → ARCHIVED | 623 | CREATE | 2021-12-30 |
| 2022 | STARTED → ARCHIVED | 624 | CREATE | 2022-12-31 |
| 2023 | STARTED → ARCHIVED | 624 | CREATE | 2023-12-31 |
| 2024 | STARTED → ARCHIVED | 622 | CREATE | 2024-12-31 |
| 2025 | STARTED → ARCHIVED | 622 | CREATE | 2025-12-31 |

Total archived: **4985** (source total 5000 − 15 NULL event_date = 4985). ✓

### Step 3 — Verify archive Delta folders — **PASS**

```
SELECT 2020 AS yr, COUNT(*) FROM delta.`.../claims/year_2020` → 623
SELECT 2021 AS yr, COUNT(*) FROM delta.`.../claims/year_2021` → 623
```
Matches Step 2 record_count. Full volume listing: `year_2018 year_2019 year_2020 year_2021 year_2022 year_2023 year_2024 year_2025`.

### Step 4 — Rehydrate archived claims — **PASS**

- `DROP SCHEMA IF EXISTS dev2_archive.rehydrated CASCADE` → executed.
- Job `caresource_rehydrate` with `years="2020,2021"` → TERMINATED SUCCESS in ~31 s.

### Step 5 — Verify rehydrated views — **PASS**

`SHOW TABLES IN dev2_archive.rehydrated` →
- `claims_unified`
- `claims_year_2020`
- `claims_year_2021`

### Step 6 — Row counts match the archive — **PASS**

After `GRANT USE SCHEMA, SELECT ...` (see deviation #3):

```
SELECT YEAR(event_date) AS yr, COUNT(*) FROM dev2_archive.rehydrated.claims_unified GROUP BY 1 ORDER BY 1
```

| yr | cnt | archive record_count | match |
|---|---|---|---|
| 2020 | 623 | 623 | ✓ |
| 2021 | 623 | 623 | ✓ |

Core round-trip assertion satisfied.

### Step 7 — Rehydration audit log — **PASS**

Latest row:
- `archive_path`: `/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples`
- `source_table`: `dev2_archive.source_data_samples.claims`
- `target_catalog`/`target_schema`: `dev2_archive` / `rehydrated`
- `years`: `[2020, 2021]`
- `tables_created`: `2`
- `status`: **COMPLETED**
- `error_message`: (empty)

### Step 8 — Cleanup — **PASS**

`DROP SCHEMA IF EXISTS dev2_archive.rehydrated CASCADE` → executed.

### What Happened

The previous 25T run on `d838774` (earlier the same day, FAIL logged below) failed at Step 1 because `_describe_history` in `src/utils.py` misclassified a truly non-existent Delta path as `archive_folder_orphan` — Databricks Runtime returns `[DELTA_MISSING_DELTA_TABLE] ... is not a Delta table.` for `DESCRIBE HISTORY delta.\`<nonexistent>\`` instead of PATH_NOT_FOUND, and the matcher only inspected substrings.

Commit `6a61448` (tagged `utils_optimize`) replaced that probing approach with a single `SELECT COUNT(*) FROM delta.\`<path>\`` probe inside `archive_state_and_count`, which:
1. On a truly missing path raises `PATH_NOT_FOUND` → caught by `ArchiveError.is_not_found` → classified `archive_folder_missing` → archiver treats as CREATE.
2. On a non-Delta path raises `DELTA_MISSING_DELTA_TABLE` → matched by `_NOT_A_DELTA_TABLE_FRAGMENTS` → classified `archive_folder_orphan`.
3. On a valid Delta path returns `(state, count)` in one SQL call — half the probe queries vs. the pre-refactor classify+count pair.
4. On unknown exceptions (e.g. PERMISSION_DENIED) returns `None` from the classifier and the caller re-raises with the original diagnostic intact.

This run verifies the fix end-to-end. Every year in `claims` archived successfully on a truly-empty starting volume, round-trips into `claims_unified` via rehydrate, and row counts reconcile against the archive audit log.

### Next Steps

- No code changes required. Tag `utils_optimize` is validated against the primary regression.
- Proceed to **29T** (`tests/databricks/test_cases/29T_append_same_run_delete.md`) — APPEND + same-run DELETE scenario.
- Monitor after 29T for the P2 follow-up noted in the adversarial review (recovery.py duplicate probe pattern) — purely optimization, no correctness gap.

---

## Run — 2026-04-24 post-scanner-fix (FAIL — archiver misclassifies empty volume as orphan)

**TL;DR:** **FAIL at Step 1.** Pre-flight clean (no audit rows, no volume contents, config correct). Archive run (scoped to `claims` via `table_config_filter`) failed on year 2018 with `archive_folder_orphan` because `_describe_history` in `src/utils.py` misclassifies Delta's `[DELTA_MISSING_DELTA_TABLE]` error as "orphan" (non-Delta files present) when the path genuinely does not exist. All downstream steps (2–8) SKIPPED — nothing was archived, so rehydrate has no input. No code changed (per test execution rule). This blocks 25T, 29T, 30T, and any first-run archive on a clean volume.

**Branch:** `feat/delta_config_build_v9_del_data_phase2` (working tree, on top of `d838774` + 4-line watermark-validator fix from earlier today)
**Profile:** `fe-sandbox-manocha`
**Target:** `dev-serverless`
**Workspace:** https://fe-sandbox-manocha.cloud.databricks.com
**Run-as SP:** `44edd08d-b71a-4e29-a01b-4881be31a144`

### Resolved Parameters

| Parameter | Value |
|---|---|
| `PROFILE` | `fe-sandbox-manocha` |
| `TARGET` | `dev-serverless` |
| `SOURCE_CATALOG` | `dev2_archive` |
| `SOURCE_SCHEMA` | `source_data_samples` |
| `CONFIG_TABLE` | `dev2_archive.metadata.global_settings` |
| `ARCHIVE_VOL` | `/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples` |
| `AUDIT_TABLE` | `dev2_archive.metadata.archive_audit_log` |
| `REHYDRATE_TARGET_SCHEMA` | `rehydrated` (not touched this run) |
| `REHYDRATION_AUDIT_TABLE` | `dev2_archive.metadata.rehydration_audit_log` |

### Test-case deviation

25T's Step 1 command does not include `table_config_filter`, but the test explicitly states *"Enable These Tables for Testing: claims"*. Current `table_configs` has `claims`, `members`, and `providers` all `is_active=true` (after today's scanner runs picked up the widened watermark patterns). Rather than toggle `is_active` on the other two rows, I scoped the archive job to claims at runtime with `table_config_filter="source_table = 'claims'"`. No state changes to `members` / `providers`.

---

### Pre-flight — **PASS**

| Check | Result |
|---|---|
| `claims` source counts by year | 623/624/623/623/624/624/622/622 for 2018–2025 + 15 NULL event_date; total 5000. |
| `ARCHIVED` rows for `claims` in `archive_audit_log` | 0. |
| Archive volume `/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/...` | Empty — user confirmed directly (`databricks fs ls` returned "no such directory" for both parent and `year_2018` path; SQL `LIST`, `SELECT FROM parquet`, `SELECT FROM delta` all returned `PATH_NOT_FOUND`). |
| `rehydrated` schema in `dev2_archive` | Does not exist. |
| `table_configs.claims` flags | `watermark_column=event_date`, `is_active=true`, `delete_after_archive=false`. |

---

### Step 1 — Archive claims (live run, scoped) — **FAIL**

Command:

```
databricks bundle run caresource_archive_run -t dev-serverless --profile fe-sandbox-manocha \
  --params config_table=dev2_archive.metadata.global_settings \
  --params dry_run=false \
  --params source_catalog=dev2_archive \
  --params source_schema=source_data_samples \
  --params table_config_filter="source_table = 'claims'"
```

- Job run URL: https://fe-sandbox-manocha.cloud.databricks.com/?o=7474658872313088#job/645665657236546/run/439961599663254
- `generate_parameters` task: SUCCESS.
- `run_archive` For-each: FAILED after 2 attempts on the single iteration (claims).
  - Iteration attempt 0 run: https://fe-sandbox-manocha.cloud.databricks.com/?o=7474658872313088#job/645665657236546/run/4279476421452
  - Iteration attempt 1 run: https://fe-sandbox-manocha.cloud.databricks.com/?o=7474658872313088#job/645665657236546/run/973593267833552
  - Both attempts terminated at year 2018 with identical error.
- `archive_run_id`: `8198ee27-f438-4968-8af4-8ad425a1f563` (wrote 2× `STARTED` + 2× `FAILED` rows for year 2018; nothing written for later years).

**Error (from iteration notebook output and from `archive_audit_log.error_message`):**

```
ArchiveOperationError: dev2_archive.source_data_samples.claims year 2018: Archive path
/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples/claims/year_2018
has data files but no valid Delta transaction log (orphan or corrupted folder).
Use reset_slice to wipe the orphan path, then re-run the archive job.
See docs/runbooks/recovery.md.
```

Stack trace tip:

```
src/archiver.py:1290  ArchiveEngine.run → _archive_table_year
src/archiver.py:740   raises ArchiveOperationError when yr_action["action"] == "ERROR"
```

Path reports for this exact path:

| Probe | Result |
|---|---|
| `databricks fs ls dbfs:/Volumes/.../claims/year_2018` | `Error: no such directory: ...` |
| SQL `LIST '/Volumes/.../claims/year_2018'` | `BAD_REQUEST No such file or directory ...` |
| `SELECT * FROM parquet.\`.../claims/year_2018\`` | `[PATH_NOT_FOUND] Path does not exist ...` |
| `SELECT COUNT(*) FROM delta.\`.../claims/year_2018\`` | `[PATH_NOT_FOUND] Path does not exist ...` |
| `DESCRIBE HISTORY delta.\`.../claims/year_2018\`` | **`[DELTA_MISSING_DELTA_TABLE] ...is not a Delta table.`** |

### Diagnosis

The archiver decides archive state via `src/utils.py::classify_archive_state` → `archive_row_count` → `_describe_history`:

```118:151:src/utils.py
_NOT_A_DELTA_TABLE_FRAGMENTS = (
    "is not a Delta table",
    "is not a delta table",
    "DELTA_MISSING_DELTA_TABLE",
    "not a valid Delta table",
    "not a valid delta table",
)


def _is_not_a_delta_table(exc: Exception) -> bool:
    msg = str(exc)
    return any(f in msg for f in _NOT_A_DELTA_TABLE_FRAGMENTS)


def _describe_history(spark, path: str) -> dict[str, Any]:
    try:
        rows = spark.sql(f"DESCRIBE HISTORY delta.`{path}`").collect()
        return {"rows": rows, "reason": None}
    except _AnalysisException as exc:
        if ArchiveError.is_not_found(exc):
            return {"rows": None, "reason": "archive_folder_missing"}
        if _is_not_a_delta_table(exc):
            return {"rows": None, "reason": "archive_folder_orphan"}
        raise
```

And the not-found matcher:

```154:183:src/exceptions.py
_NOT_FOUND_FRAGMENTS = (
    "java.io.FileNotFoundException",
    "FileNotFoundException",
    "No such file or directory",
    "PATH_NOT_FOUND",
    "does not exist",
    "cannot be found",
)
...
@staticmethod
def is_not_found(exc: Exception) -> bool:
    msg = str(exc)
    if any(f in msg for f in ArchiveError._PERMISSION_FRAGMENTS):
        return False
    return any(f in msg for f in ArchiveError._NOT_FOUND_FRAGMENTS)
```

On Databricks Runtime today, `DESCRIBE HISTORY delta.\`<nonexistent_path>\`` raises `[DELTA_MISSING_DELTA_TABLE] ... is not a Delta table`. The message does **not** contain any `_NOT_FOUND_FRAGMENT` → `is_not_found` returns False → control flows to `_is_not_a_delta_table` which matches `DELTA_MISSING_DELTA_TABLE` / `is not a Delta table` → reason becomes `archive_folder_orphan`. At `src/archiver.py:492–498` the engine treats ORPHAN as a hard ERROR under D14 rule B, with the misleading "has data files" wording.

Net effect: **every first-run archive against a clean archive volume fails** with this diagnostic, even though the volume is genuinely empty. The classification path can never reach `archive_folder_missing` for this scenario on the current runtime.

This also explains why the iteration's audit rows show `archive_mode=""` and `record_count=0` — nothing was read or written; the check bails out before `classify_archive_state` returns.

### Steps 2–8 — **SKIP**

- 2 (audit verification): partial — only the failure rows for year 2018 exist (`STARTED` then `FAILED` for each of 2 attempts). No `ARCHIVED` rows to inspect.
- 3 (spot-check Delta folders): impossible — nothing was written.
- 4 (rehydrate): skipped — no archive to rehydrate from.
- 5–7 (view creation, count match, rehydration audit): skipped — depend on 4.
- 8 (cleanup `rehydrated` schema): no-op, schema never created.

### What Happened

Pre-flight cleanly confirmed the test's assumed baseline: source counts match `generate_test_data` seed, audit log has no `claims` rows, the archive volume is empty, the `rehydrated` schema is absent, and `table_configs.claims` flags are correct. The archive run was kicked off scoped to `claims` only. The for-each iteration attempted year 2018, called `classify_archive_state` on `/Volumes/.../claims/year_2018`, and got Delta's `[DELTA_MISSING_DELTA_TABLE]` back. The classifier matched that against its "not a Delta table" fragments and returned `archive_folder_orphan` instead of `archive_folder_missing`. D14 rule B then raised `ArchiveOperationError` with the "has data files but no valid Delta transaction log" message, which is factually wrong — the path has no data files and no log because it has never existed. The iteration was retried automatically and failed identically. Since D14 rule B fires on year 2018 before any other year, no later years were attempted. Nothing was written anywhere (volume, audit `ARCHIVED` rows), so all downstream verification is meaningless.

### Next Steps

1. **Root-cause fix (library-side):** `_describe_history` (or `ArchiveError.is_not_found`) must treat `[DELTA_MISSING_DELTA_TABLE]` for a path that does not exist as `archive_folder_missing`, not `archive_folder_orphan`. Two common options:
   - Probe existence first with `dbutils.fs.ls(path)` (or a bounded `ls` wrapper). If the path does not exist → `archive_folder_missing`. Only if the path **does** exist but lacks `_delta_log/` → `archive_folder_orphan`.
   - Or, prefer the `archive_row_count` path that already uses `DESCRIBE HISTORY`: extend `_NOT_FOUND_FRAGMENTS` with `DELTA_MISSING_DELTA_TABLE`-plus-a-positive-existence-check, so that a missing path can't be misread as orphan purely from the error text. (Pure string matching is fragile — if the path exists with non-Delta parquet, the message is also `is not a Delta table`. Distinguishing requires an independent existence probe.)
2. **Add a unit/integration test** that calls `classify_archive_state` against a truly non-existent path on DBR and asserts `"MISSING"` (currently it would return `"ORPHAN"` on real Spark — the existing unit test uses a mocked exception so the regression is invisible).
3. **Re-run 25T from Step 1** after the fix. Expect `archive_mode=CREATE` for every eligible year of `claims`, and rehydrate to work as before.
4. **Blocks 29T and 30T** — they both start from a clean archive volume and will hit the same path. Do not run them until the archiver fix is in place, or pre-populate each year with an empty Delta table (nasty; not recommended).
5. Cleanup for the `claims` audit rows from this failed run (4 rows, `archive_run_id = 8198ee27-f438-4968-8af4-8ad425a1f563`) is optional — 29T Phase 1a already plans to `DELETE` all `claims` audit rows before it runs, so leaving them in place is fine.

---

## Run — 2026-04-20 00:57 CDT (Rehydrate-only, reusing 05R archive)

**TL;DR:** Skipped Steps 1–3 because the 05R archive from ~10 min earlier was still intact (21 year-partitions present). Rehydrated `claims` years 2020, 2021 into `dev2_archive.rehydrated` successfully on second attempt — first attempt failed because the SP lacked `CREATE SCHEMA` on the catalog (4th ownership-vs-grant gap). After self-grant, job ran in ~51s, produced 3 views, counts matched archive exactly (2020=623, 2021=623). Two further grant-gaps surfaced (5th on self `USE SCHEMA/SELECT` on SP-owned `rehydrated` schema). Cleanup OK.

**Branch:** `feat/delta_config_build_v6_dab`
**Profile:** `fe-sandbox-manocha`
**Target:** `dev-serverless`
**Workspace:** https://fe-sandbox-manocha.cloud.databricks.com

### Resolved Parameters

| Parameter | Value |
|---|---|
| `PROFILE` | `fe-sandbox-manocha` |
| `TARGET` | `dev-serverless` |
| `SOURCE_CATALOG` | `dev2_archive` |
| `SOURCE_SCHEMA` | `source_data_samples` |
| `CONFIG_TABLE` | `dev2_archive.metadata.global_settings` |
| `ARCHIVE_VOL` | `/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples` |
| `AUDIT_TABLE` | `dev2_archive.metadata.archive_audit_log` |
| `REHYDRATE_TARGET_SCHEMA` | `rehydrated` |
| `REHYDRATION_AUDIT_TABLE` | `dev2_archive.metadata.rehydration_audit_log` |

---

### Pre-flight — **PASS** (with notes)

| Check | Result |
|---|---|
| 05R archive still in place | Yes — 21 year-partitions present across `claims`/`members`/`providers` on the archive volume |
| `rehydration_audit_log` exists | Yes, 0 rows (clean) |
| `dev2_archive.rehydrated` schema | Existed but empty — dropped CASCADE before Step 4 |
| `claims` source count | 5000 (8 years + 15 NULL) — unchanged from 05R |
| `claims` config | `watermark_column=event_date`, `is_active=true`, `delete_after_archive=false` |

Steps 1–3 (archive + audit + delta folder verification) **SKIPPED** — directly reusing the 05R archive. 05R already proved all 21 partitions match audit `record_count` exactly; no need to re-archive.

---

### Step 4 — Rehydrate claims (years 2020, 2021) — **PASS** (on attempt 2)

**Attempt 1 — FAILED:**

- Run URL: https://fe-sandbox-manocha.cloud.databricks.com/?o=7474658872313088#job/646384074228167/run/597590828671204
- Duration: ~63 s
- Result: `FAILED`
- Error (captured in `rehydration_audit_log`):

  ```
  ArchiveOperationError: dev2_archive.source_data_samples.claims year all: rehydrate failed —
  (com.databricks.sql.managedcatalog.acl.UnauthorizedAccessException)
  PERMISSION_DENIED: User does not have CREATE SCHEMA on Catalog 'dev2_archive'.
  ```

**Root cause:** rehydrator calls `CREATE SCHEMA IF NOT EXISTS <catalog>.<target_schema>` (see `src/rehydrator.py:94` → `src/utils.py:28`). `CREATE SCHEMA IF NOT EXISTS` in Unity Catalog requires the `CREATE SCHEMA` privilege on the parent catalog even if the schema already exists. The run-as SP only had `USE_CATALOG` on `dev2_archive`.

**Fix applied (by catalog owner):**

```sql
GRANT CREATE SCHEMA ON CATALOG dev2_archive TO `44edd08d-b71a-4e29-a01b-4881be31a144`;
```

Verified via `SHOW GRANTS ON CATALOG dev2_archive` (note: `databricks grants get catalog` was stale and didn't show it, had to use SQL).

**Attempt 2 — PASS:**

- Run URL: https://fe-sandbox-manocha.cloud.databricks.com/?o=7474658872313088#job/646384074228167/run/525989069346630
- Duration: ~51 s
- Status: `TERMINATED SUCCESS`

Command:

```
databricks bundle run caresource_rehydrate -t dev-serverless --profile fe-sandbox-manocha \
  --params config_table=dev2_archive.metadata.global_settings \
  --params archive_base_path=/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples \
  --params source_table=dev2_archive.source_data_samples.claims \
  --params target_catalog=dev2_archive \
  --params target_schema=rehydrated \
  --params 'years="2020,2021"'
```

---

### Step 5 — Rehydrated views exist — **PASS**

`SHOW TABLES IN dev2_archive.rehydrated` returned:

| name | type |
|---|---|
| `claims_year_2020` | view |
| `claims_year_2021` | view |
| `claims_unified` | view |

Exactly as expected.

---

### Step 6 — Row counts match archive — **PASS** (after self-grant)

Initial query blocked — `USE SCHEMA` missing on the new schema:

```
Error: query failed: BAD_REQUEST [INSUFFICIENT_PERMISSIONS] Insufficient privileges:
User does not have USE SCHEMA on Schema 'dev2_archive.rehydrated'. SQLSTATE: 42501
```

The SP created the `rehydrated` schema, so the SP owns it. Catalog owner does not inherit privileges on SP-owned schemas. **Same ownership-vs-grant pattern for the 5th time this session.**

Self-grant:

```sql
GRANT USE SCHEMA, SELECT ON SCHEMA dev2_archive.rehydrated TO `sandeep.manocha@databricks.com`;
```

After grant:

| View | year | Rehydrated count | Archive `record_count` (05R) | Match |
|---|---|---|---|---|
| `claims_unified` | 2020 | 623 | 623 | ✓ |
| `claims_unified` | 2021 | 623 | 623 | ✓ |
| `claims_year_2020` | — | 623 | 623 | ✓ |
| `claims_year_2021` | — | 623 | 623 | ✓ |

**Core round-trip assertion verified:** source (623 rows in 2020, 623 in 2021) → archived Delta (same) → rehydrated views (same). End-to-end identity preserved.

---

### Step 7 — Rehydration audit log — **PASS**

Two rows in `rehydration_audit_log` (both from this session):

| created_at | status | tables_created | years | error_message |
|---|---|---|---|---|
| 2026-04-20T05:57:26.334Z | **COMPLETED** | 2 | `[2020, 2021]` | (null) |
| 2026-04-20T05:55:56.544Z | FAILED | 0 | `[2020, 2021]` | `PERMISSION_DENIED: User does not have CREATE SCHEMA on Catalog dev2_archive` + JVM stacktrace |

Both entries: `archive_path = /Volumes/.../source_data_samples`, `source_table = dev2_archive.source_data_samples.claims`, `target_catalog = dev2_archive`, `target_schema = rehydrated`. The audit table correctly captured the FAILED attempt with full error detail — good instrumentation. ✓

---

### Step 8 — Cleanup — **PASS**

```sql
DROP SCHEMA IF EXISTS dev2_archive.rehydrated CASCADE;
-- Query executed successfully
SHOW SCHEMAS IN dev2_archive LIKE 'rehydrated';
-- []  (empty)
```

Schema + all 3 views gone. `rehydration_audit_log` entries retained (as they should be — historical record).

---

### Final State

| Item | Value |
|---|---|
| Source `claims` | 5000 rows (unchanged) |
| Archive Delta folders | Intact from 05R (21 year-partitions across claims/members/providers) |
| `rehydrated` schema | Dropped |
| `rehydration_audit_log` | 2 rows (1 FAILED, 1 COMPLETED) — retained as history |
| `archive_audit_log` | Unchanged from 05R end-state (63 rows) |

---

## What Happened

1. **Skipped Steps 1–3** because 05R had just finished archiving all 3 tables (all 21 year-partitions) about 10 minutes prior — verified the archive was still intact before starting.
2. **Pre-flight** found the `rehydrated` schema already existed (empty) from a prior session — dropped CASCADE before running.
3. **First rehydrate attempt failed** within 63 s with `PERMISSION_DENIED: User does not have CREATE SCHEMA on Catalog dev2_archive`. The rehydrator's first SQL after parameter resolution is `CREATE SCHEMA IF NOT EXISTS <catalog>.<schema>` (src/rehydrator.py:94 → src/utils.py:28). UC requires `CREATE SCHEMA` even when the schema already exists.
4. **Granted `CREATE SCHEMA` on the catalog to the run-as SP** and re-ran — second attempt succeeded in ~51 s.
5. **Views all created** — `claims_year_2020`, `claims_year_2021`, `claims_unified`. SP owns the new schema (it created it), so the catalog owner (me) couldn't query the unified view until self-granting `USE SCHEMA, SELECT`.
6. **Round-trip verified** — 623/623 for years 2020/2021 from the unified view and the per-year views, matching the 05R archive's `record_count` and the source exactly.
7. **Audit log captured the failure correctly** — the FAILED row preserves the full error message and JVM stack; the COMPLETED row has `tables_created=2` and null error. Good for forensics.
8. **Cleanup** dropped the schema. Archive volume and archive audit log untouched (as they should be for a non-destructive rehydrate).

---

## Next Steps

1. **Call it a day** — archive (05R) and rehydrate (this run) both verified end-to-end for `claims`. Full round-trip works.
2. **Consolidated grant gaps** hit this session (running count — 05R flagged 3, this run adds 2 more, total = 5 of 6 grant classes):
   | # | Test | Missing grant | Granted to | Fixed in this session |
   | --- | --- | --- | --- | --- |
   | 1 | 02R | `MODIFY ON SCHEMA dev2_archive.metadata` | owner | ✓ |
   | 2 | 04R | `SELECT ON SCHEMA dev2_archive.source_data_samples` | owner | ✓ |
   | 3 | 05R | `READ VOLUME ON VOLUME .sample_data_archive_ext_vol` | owner | ✓ |
   | 4 | 25R (rehydrate attempt 1) | `CREATE SCHEMA ON CATALOG dev2_archive` | SP | ✓ |
   | 5 | 25R (query rehydrated views) | `USE SCHEMA, SELECT ON SCHEMA dev2_archive.rehydrated` | owner | ✓ |
   | 6 | (still open) | `USE SCHEMA` on `dev2_archive.metadata` for owner — unclear whether already present; works because owner granted themselves `MODIFY` in 02R | owner | partial |

   Pattern is clear: **UC catalog ownership does not cascade to child-object privileges, and SP-created schemas aren't visible to the catalog owner.** Fix in `seed_config.py` / `setup_config_tables.py`:

   - Grant the owner: `MODIFY ON SCHEMA <metadata>`, `SELECT ON SCHEMA <source>`, `READ VOLUME ON VOLUME <archive>`.
   - Grant the SP: `CREATE SCHEMA ON CATALOG <catalog>` (needed by rehydrator).
   - After rehydrate job creates the target schema, have the rehydrator itself also `GRANT USE SCHEMA, SELECT ON SCHEMA <target> TO <catalog_owner>` so humans can inspect the views.

3. **Architecturally — consider removing `CREATE SCHEMA ON CATALOG` requirement from the SP:** that's a broad privilege (the SP can create any schema anywhere in the catalog). Cleaner alternatives:
   - Pre-create the target schema in `setup_config_tables.py` / `seed_config.py`; grant SP `CREATE TABLE, USE SCHEMA` on just that schema. Fail fast in the rehydrator if the schema doesn't exist (with a clear message).
   - Or: document the target schema as a required pre-existing resource in the rehydrate test cases + runbook.

4. **Update docs/runbooks/service-principals.md** and/or `docs/runbooks/catalog-setup.md` with the complete list of one-time grants needed after catalog provisioning (this session identified 5 distinct ones).

---



**TL;DR:** Full round-trip passed — archived `claims` (all 8 years, CREATE mode), rehydrated years 2020 and 2021 as views, and confirmed row counts match. All 8 steps PASS, zero failures.

**Branch:** `feat/delta_config_build_v5_rehydrate`
**Profile:** fe-sandbox-manocha
**Target:** dev-serverless
**Workspace:** https://fe-sandbox-manocha.cloud.databricks.com

### Resolved Parameters

| Parameter | Value |
|---|---|
| `PROFILE` | `fe-sandbox-manocha` |
| `TARGET` | `dev-serverless` |
| `SOURCE_CATALOG` | `dev2_archive` |
| `SOURCE_SCHEMA` | `source_data_samples` |
| `CONFIG_TABLE` | `dev2_archive.metadata.global_settings` |
| `ARCHIVE_VOL` | `/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples` |
| `AUDIT_TABLE` | `dev2_archive.metadata.archive_audit_log` |
| `REHYDRATE_TARGET_SCHEMA` | `rehydrated` |
| `REHYDRATION_AUDIT_TABLE` | `dev2_archive.metadata.rehydration_audit_log` |

---

### Pre-flight: **PASS**

User performed manual cleanup before run: dropped all files in the archive Volume and dropped the `rehydrated` schema.

| Check | Result |
|---|---|
| Source `claims` | 5,000 rows (8 years + 15 NULL) |
| Archive audit (ARCHIVED for claims) | Zero matching rows (clean start) |
| Archive volume `claims/` | No folder exists (user dropped) |
| Rehydrate schema `rehydrated` | Does not exist (user dropped) |
| `claims` config | `watermark_column=event_date`, `is_active=true`, `delete_after_archive=false` |
| `bronze_column_lineage` / `bronze_query_history` | Not in `table_configs` — already disabled |

Source row counts by year:

| Year | Count |
|------|-------|
| NULL | 15 |
| 2018 | 623 |
| 2019 | 624 |
| 2020 | 623 |
| 2021 | 623 |
| 2022 | 624 |
| 2023 | 624 |
| 2024 | 622 |
| 2025 | 622 |

---

### Step 1 — Archive claims (live run): **PASS**

| Field | Value |
|-------|-------|
| Run URL | [run/563617677061379](https://fe-sandbox-manocha.cloud.databricks.com/?o=7474652022110066#job/1024729309722184/run/563617677061379) |
| Status | TERMINATED SUCCESS |
| Duration | ~3 min |

---

### Step 2 — Verify archive audit log: **PASS**

Each year has a STARTED → ARCHIVED pair with `archive_mode = CREATE` and `record_count > 0`.

| Year | record_count | archive_mode | watermark_value |
|------|-------------|-------------|-----------------|
| 2018 | 623 | CREATE | 2018-12-31 |
| 2019 | 624 | CREATE | 2019-12-31 |
| 2020 | 623 | CREATE | 2020-12-30 |
| 2021 | 623 | CREATE | 2021-12-30 |
| 2022 | 624 | CREATE | 2022-12-31 |
| 2023 | 624 | CREATE | 2023-12-31 |
| 2024 | 622 | CREATE | 2024-12-31 |
| 2025 | 622 | CREATE | 2025-12-31 |

**Note:** The audit table also contains STARTED/ARCHIVED entries from prior runs (Volume data was cleared but audit log entries were not). The archive correctly used `CREATE` mode for all years, confirming the Volume was clean.

---

### Step 3 — Verify archive Delta folders: **PASS**

| Folder | Count |
|--------|-------|
| `claims/year_2020` | 623 |
| `claims/year_2021` | 623 |

Matches `record_count` from Step 2.

---

### Step 4 — Rehydrate archived claims: **PASS**

| Field | Value |
|-------|-------|
| Run URL | [run/705212801086400](https://fe-sandbox-manocha.cloud.databricks.com/?o=7474652022110066#job/614415962528020/run/705212801086400) |
| Status | TERMINATED SUCCESS |
| Duration | ~50 sec |

---

### Step 5 — Verify rehydrated views exist: **PASS**

| Object | Type |
|--------|------|
| `claims_year_2020` | View |
| `claims_year_2021` | View |
| `claims_unified` | View |

---

### Step 6 — Verify row counts match archive: **PASS**

| Year | Rehydrated count | Archive count (Step 2) | Match? |
|------|-----------------|----------------------|--------|
| 2020 | 623 | 623 | Yes |
| 2021 | 623 | 623 | Yes |

Round-trip assertion confirmed: source rows → archived → rehydrated views → same counts.

---

### Step 7 — Verify rehydration audit log: **PASS**

Latest audit entry:

| Field | Value |
|-------|-------|
| `archive_path` | `/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples` |
| `source_table` | `dev2_archive.source_data_samples.claims` |
| `target_catalog` | `dev2_archive` |
| `target_schema` | `rehydrated` |
| `years` | `[2020, 2021]` |
| `tables_created` | 2 |
| `status` | **COMPLETED** |
| `error_message` | (null) |
| `created_at` | `2026-04-17T21:19:25.971Z` |

---

### Step 8 — Cleanup: **PASS**

`DROP SCHEMA IF EXISTS dev2_archive.rehydrated CASCADE` — succeeded.

---

## What Happened

1. **Pre-flight passed** — user had manually cleaned the archive Volume (dropped all files) and dropped the `rehydrated` schema. Source `claims` had 5,000 rows across 8 years. The `table_configs` entry for `claims` was correctly configured with `watermark_column=event_date`, `is_active=true`, `delete_after_archive=false`.

2. **Archive (Step 1)** — the `caresource_archive_run` job completed in ~3 minutes, archiving all 8 years of `claims` data with `archive_mode=CREATE`. Record counts matched the source exactly (623/624/622 per year).

3. **Delta verification (Step 3)** — spot-checked `year_2020` (623 rows) and `year_2021` (623 rows), both matching the audit log counts.

4. **Rehydrate (Step 4)** — the `caresource_rehydrate` job completed in ~50 seconds, creating views for years 2020 and 2021 plus a `claims_unified` view.

5. **Round-trip assertion (Step 6)** — the unified view returned 623 rows for 2020 and 623 rows for 2021, matching the archived record counts exactly.

6. **Rehydration audit (Step 7)** — latest entry shows `COMPLETED` with `tables_created=2` and no errors.

7. **Cleanup (Step 8)** — `rehydrated` schema dropped.

## Next Steps

- All steps passed — the archive → rehydrate round-trip is verified end-to-end.
- Consider clearing stale audit log entries from prior runs if you want a fully clean audit trail for future tests.
- The archive Volume still contains data from all 8 years — leave in place for subsequent rehydration tests or clean up as needed.
