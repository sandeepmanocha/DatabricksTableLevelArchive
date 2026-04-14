# 08 — Archive with Exclusion Conditions Results

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
