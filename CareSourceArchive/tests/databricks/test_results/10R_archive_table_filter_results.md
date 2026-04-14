# 10 — Archive with Table Config Filter Results

## Run — 2026-04-10 15:44 CDT

**Result:** SKIP — already covered by test 09.

Test 09 used `table_config_filter="source_table = 'providers'"` to scope the live archive to providers only. The job ran successfully and only providers appeared in the audit log — no claims or members. This is the same `table_config_filter` mechanism that test 10 validates, just with a different table name.

---

**Test Date:** 2026-04-07
**Test Time:** 14:35 – 14:36 CDT
**Branch:** `feat/delta_config_build_v3_code_reduce`
**Profile:** DEFAULT
**Workspace:** https://e2-demo-field-eng.cloud.databricks.com
**Bundle Target:** dev

---

## Step 1 — Run Archive (dry_run=true, scoped to claims)

| Field | Value |
|-------|-------|
| Status | **PASS** — TERMINATED SUCCESS |
| Duration | ~75 sec |
| Run URL | https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/197650125146998/run/915338541283606 |

---

## Step 2 — Check Audit Log

| Tables in run | Result |
|---------------|--------|
| `sandeep_manocha.caresource_data_samples.claims` | **PASS** — only table present |

No `members` or `providers` entries for this `archive_run_id`.

---

## Overall Result: PASS

The `table_config_filter` parameter correctly scopes the archive job to a single table. Only `claims` was processed — other active tables were excluded by the filter.
