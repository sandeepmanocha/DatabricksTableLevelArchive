# 01 — Setup & Deploy Results

**Test Date:** 2026-04-07
**Test Time:** 17:09 – 17:14 UTC (12:09 – 12:14 CDT)
**Branch:** `feat/delta_config_build_v3_code_reduce`
**Profile:** DEFAULT
**Workspace:** https://e2-demo-field-eng.cloud.databricks.com
**Bundle Target:** dev
**Config:** `sandeep_manocha.caresource_audit`
**Source:** `sandeep_manocha.caresource_data_samples`

---

## Step 1 — Validate & Deploy Bundle

| Step | Result |
|------|--------|
| `bundle validate -t dev` | **PASS** — "Validation OK!" |
| `bundle deploy -t dev` | **PASS** — files uploaded, deployment complete |

Deployed to: `/Workspace/Users/sandeep.manocha@databricks.com/.bundle/caresource-archive/dev`

---

## Step 2 — Run Setup Job (create config tables)

| Field | Value |
|-------|-------|
| Status | **PASS** — TERMINATED SUCCESS |
| Duration | ~34 sec |
| Run URL | https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/469387292954169/run/208210053085527 |

Tables created in `sandeep_manocha.caresource_audit`:

| Table | Exists | Initial Rows |
|-------|--------|-------------|
| global_settings | Yes | 0 |
| schema_templates | Yes | 0 |
| table_configs | Yes | 0 |
| table_configs_staging | Yes | 0 |
| archive_audit_log | Yes | 0 |
| scanner_log | Yes | 0 |

---

## Step 3 — Seed Dev Config

| Field | Value |
|-------|-------|
| Status | **PASS** — TERMINATED SUCCESS |
| Duration | ~47 sec |
| Run URL | https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/478479478935865/run/274316501926284 |

| Verification | Expected | Actual | Result |
|-------------|----------|--------|--------|
| global_settings rows | 1 | 1 | **PASS** |
| schema_templates rows | 1 | 1 | **PASS** |
| watermark_column_patterns | `['event_date', 'start_time', 'query_date']` | `["event_date","start_time","query_date"]` | **PASS** |

---

## Step 4 — Generate Test Data

| Field | Value |
|-------|-------|
| Status | **PASS** — TERMINATED SUCCESS |
| Duration | ~82 sec |
| Run URL | https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/537766256105108/run/791890457613117 |

---

## Step 5 — Verify Tables Exist

| Table | Expected Rows | Actual Rows | Watermark Column | Expected Years | Actual Years | Result |
|-------|--------------|-------------|-----------------|---------------|-------------|--------|
| claims | ~5,000 | 5,000 | event_date | 2018–2025 | 2018–2025 | **PASS** |
| members | ~3,000 | 3,000 | start_date | 2019–2025 | 2019–2025 | **PASS** |
| providers | ~1,000 | 1,000 | effective_date | 2020–2025 | 2020–2025 | **PASS** |

---

## Overall Result: PASS

All 5 steps completed successfully. The bundle deploys cleanly, config tables are created and seeded correctly, and test data matches all expected row counts and year ranges.
