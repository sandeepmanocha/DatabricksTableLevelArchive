# 25 — End-to-End: Archive then Rehydrate — Results

> **Note:** `unified_view_suffix` is now a configurable parameter (default `_unified`). It can be passed via `--params unified_view_suffix=<value>` or set in the notebook widget. When not specified, the unified view uses the `_unified` suffix (e.g. `claims_unified`).

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
