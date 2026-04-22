# 28 — `filter_expr` Typo Guards and Typed Params — Results

## Run — 2026-04-20 22:00 CDT

**TL;DR:** All 11 sub-cases PASS. All forbidden-token filters (1a–1c) and invalid-filter filters (2a–2b) were rejected at config-load time with `ArchiveConfigError(field="filter_expr", ...)`. Valid typed-widget and raw-filter runs (2c, 3a–3c) succeeded with matching scopes (N_TYPED = N_RAW = 3; narrowing filter resolved to claims only). Mismatched typed params (3d) and empty-result filters (4a) each produced readable `ArchiveConfigError`s with all supplied values named.

**Branch:** `feat/delta_config_build_v5_rehydrate` (current working tree)
**Profile:** `fe-sandbox-manocha`
**Target:** `dev-serverless` (adapted from test doc's `-t dev` — the `dev` cluster target is not deployed in this workspace; only `dev-serverless` is)
**Workspace:** https://fe-sandbox-manocha.cloud.databricks.com
**Bundle:** `caresource-archive`
**Job ID:** `645665657236546` (caresource_archive_run)

### Path Adaptations (test doc → this workspace)

The test doc hard-codes `sandeep_manocha.caresource_audit.*` and `sandeep_manocha.source_data_samples`. In `fe-sandbox-manocha` there is no `sandeep_manocha` catalog; the bundle deploys to `dev2_archive`. All commands below substitute:

| Test doc | Actual used |
|---|---|
| `config_table="sandeep_manocha.caresource_audit.global_settings"` | `config_table="dev2_archive.metadata.global_settings"` |
| `source_catalog="sandeep_manocha"` | `source_catalog="dev2_archive"` |
| `source_schema="source_data_samples"` | `source_schema="source_data_samples"` (same) |
| `--profile DEFAULT -t dev` | `--profile fe-sandbox-manocha -t dev-serverless` |
| (1a) `DROP TABLE sandeep_manocha.source_data_samples.members` | `DROP TABLE dev2_archive.source_data_samples.members` |

### Pre-flight: **PASS**

| Check | Result |
|---|---|
| Profile `fe-sandbox-manocha` resolves as `sandeep.manocha@databricks.com` | PASS |
| `dev2_archive.metadata.table_configs` has `claims`, `members`, `providers`, all `is_active=true` | PASS (3 rows) |
| `dev-serverless` jobs deployed (archive_run, rehydrate, scanner, setup, seed, gen_data) | PASS |
| `dry_run="true"` used in every sub-case below — no writes to source tables or archive volumes | PASS |
| `members` table schema intact before test | PASS (7 columns present) |

---

## Phase 1 — Forbidden tokens rejected at config-load time

### 1a. Semicolon in `filter_expr`: **PASS**

| Field | Value |
|---|---|
| Status | INTERNAL_ERROR FAILED at task `generate_parameters` |
| Duration | ~54 sec |
| Error | `ArchiveConfigError: filter_expr must not contain SQL comments or statement separators` |
| Raised at | `src/config.py:153` (inside `load_table_configs`, before any per-table work) |

Post-run check: `DESCRIBE TABLE dev2_archive.source_data_samples.members` returns the full 7-column schema — the `DROP TABLE` payload embedded in the filter was **not** executed. No downstream tasks ran.

### 1b. Inline `--` comment in `filter_expr`: **PASS**

| Field | Value |
|---|---|
| Status | INTERNAL_ERROR FAILED at task `generate_parameters` |
| Duration | ~53 sec |
| Error | `ArchiveConfigError: filter_expr must not contain SQL comments or statement separators` |
| Raised at | `src/config.py:153` |

Operator-accident scenario confirmed: the `-- AND is_active = false` suffix that would have silently widened scope to include inactive rows is rejected **before** the scope widens.

### 1c. Block comment `/* ... */` in `filter_expr`: **PASS**

| Field | Value |
|---|---|
| Status | INTERNAL_ERROR FAILED at task `generate_parameters` |
| Duration | ~53 sec |
| Error | `ArchiveConfigError: filter_expr must not contain SQL comments or statement separators` |
| Raised at | `src/config.py:153` |

All three forbidden-token sub-cases surface the exact same `filter_expr` rejection message and attribute cleanly to the widget name.

---

## Phase 2 — Parse error surfaces as ArchiveConfigError

### 2a. Unknown column in `filter_expr`: **PASS**

| Field | Value |
|---|---|
| Status | INTERNAL_ERROR FAILED at task `generate_parameters` |
| Duration | ~34 sec |
| Error (verbatim) | `ArchiveConfigError: filter_expr failed to parse: [UNRESOLVED_COLUMN.WITH_SUGGESTION] A column, variable, or function parameter with name `nonexistent_column` cannot be resolved. Did you mean one of the following? [`watermark_column`, `is_active`, `modified_at`, `modified_by`, `reason`]. SQLSTATE: 42703; line 1 pos 56;` |
| Raised at | `src/config.py:162` (the `except` → re-raise inside the `LIMIT 0` probe) |

The error contains `filter_expr`, surfaces `UNRESOLVED_COLUMN`, and — importantly — includes column-name suggestions straight from Spark's analyzer. An operator can fix the widget without reading any task logs.

### 2b. Missing quote in `filter_expr`: **PASS**

| Field | Value |
|---|---|
| Status | INTERNAL_ERROR FAILED at task `generate_parameters` |
| Duration | ~34 sec |
| Error (verbatim) | `ArchiveConfigError: filter_expr failed to parse: [PARSE_SYNTAX_ERROR] Syntax error at or near '''. SQLSTATE: 42601 (line 1, pos 71)` |
| Raised at | `src/config.py:162` (re-raise of `ParseException`) |

The unterminated-string complaint from Spark's SQL parser is attached to the `filter_expr` widget attribution. No mid-run failure — the entire job terminates before any per-table work.

### 2c. Valid `filter_expr` should still succeed: **PASS**

| Field | Value |
|---|---|
| Status | TERMINATED SUCCESS |
| Duration | ~112 sec |
| Run URL | https://fe-sandbox-manocha.cloud.databricks.com/?o=7474658872313088#job/645665657236546/run/1018480569599792 |
| Tables in audit | `dev2_archive.source_data_samples.claims` (1 distinct `table_name`, no `members`, no `providers`) |

The guard does **not** false-positive on valid SQL. The dry-run considered only `claims`.

---

## Phase 3 — Typed params (`source_catalog` / `source_schema`) work standalone

### 3a. Typed widgets only: **PASS**

| Field | Value |
|---|---|
| Status | TERMINATED SUCCESS |
| Duration | ~105 sec |
| Run URL | https://fe-sandbox-manocha.cloud.databricks.com/?o=7474658872313088#job/645665657236546/run/949633497835919 |
| `archive_run_id` | `6f859ffb-13c2-45a4-9293-5b5dd69c4006` |
| `N_TYPED` | **3** — `claims`, `members`, `providers` |

### 3b. Equivalent raw-filter run: **PASS**

| Field | Value |
|---|---|
| Status | TERMINATED SUCCESS |
| Duration | ~92 sec |
| Run URL | https://fe-sandbox-manocha.cloud.databricks.com/?o=7474658872313088#job/645665657236546/run/325863876857314 |
| `archive_run_id` | `b59e398f-90e7-4a04-aeaa-5da5a98af27c` |
| `N_RAW` | **3** — `claims`, `members`, `providers` |

**Equivalence:** `N_TYPED` (3) == `N_RAW` (3). The typed widgets and the equivalent raw `filter_expr` resolve the same config set.

### 3c. Typed params + narrowing `filter_expr`: **PASS**

| Field | Value |
|---|---|
| Status | TERMINATED SUCCESS |
| Duration | ~82 sec |
| Run URL | https://fe-sandbox-manocha.cloud.databricks.com/?o=7474658872313088#job/645665657236546/run/884614109357936 |
| `archive_run_id` | `d771277b-016b-470f-8699-a729f8bb30e9` |
| Tables in audit | `dev2_archive.source_data_samples.claims` (claims only) |

Typed params AND `filter_expr` AND-together correctly — the narrowing filter is still an escape hatch on top of the typed widgets, not a replacement.

### 3d. Mismatched typed params (catalog without schema): **PASS**

| Field | Value |
|---|---|
| Status | INTERNAL_ERROR FAILED at task `generate_parameters` |
| Duration | ~52 sec |
| Error (verbatim) | `ArchiveConfigError: source_catalog and source_schema must both be provided together (both or neither).` |
| Raised at | `notebooks/generate_parameters.py:38` (before any table_configs read) |

Exact message expected; guards the common mistake of filling one widget and forgetting the other.

---

## Phase 4 — Empty-result error when all filters exclude everything

### 4a. Filter matching no config: **PASS**

| Field | Value |
|---|---|
| Status | INTERNAL_ERROR FAILED at task `generate_parameters` |
| Duration | ~51 sec |
| Error (verbatim) | `ArchiveConfigError: No active table_configs matched the supplied filters (source_catalog='no_such_catalog', source_schema='no_such_schema', filter_expr=None)` |
| Raised at | `notebooks/generate_parameters.py:83-86` |

The notebook did **not** silently succeed with zero configs. All supplied filter values (both typed widgets and `filter_expr`) are named in the error message, so the operator can see exactly what was supplied.

---

## Phase 5 — Cleanup

Not executed (test doc marks it optional). Dry-run `STARTED` audit rows from all passing sub-cases remain; they are harmless and will age out. The passing runs (`archive_run_id` listed above) wrote 1 + 3 + 3 + 1 = 8 `STARTED` rows across `dev2_archive.metadata.archive_audit_log`. No source-table writes. No volume writes.

---

## Summary Table

| Phase | Sub-case | Expected | Actual | Result |
|---|---|---|---|---|
| 1a | Semicolon in filter_expr | FAIL with forbidden-token message; members intact | Failed at config-load; members schema intact | PASS |
| 1b | `--` in filter_expr | FAIL with forbidden-token message | Same message, same attribution | PASS |
| 1c | `/* */` in filter_expr | FAIL with forbidden-token message | Same message, same attribution | PASS |
| 2a | Unknown column in filter_expr | FAIL at config-load with `filter_expr` + Spark UNRESOLVED_COLUMN | `ArchiveConfigError: filter_expr failed to parse: [UNRESOLVED_COLUMN.WITH_SUGGESTION]...` | PASS |
| 2b | Missing quote in filter_expr | FAIL at config-load with `filter_expr` + parse error | `ArchiveConfigError: filter_expr failed to parse: [PARSE_SYNTAX_ERROR]...` | PASS |
| 2c | Valid filter_expr | SUCCESS, claims only | SUCCESS, 1 distinct table_name (claims) | PASS |
| 3a | Typed widgets only | SUCCESS, 3 tables | SUCCESS, N_TYPED=3 | PASS |
| 3b | Equivalent raw filter | SUCCESS, N_RAW = N_TYPED | SUCCESS, N_RAW=3 == N_TYPED | PASS |
| 3c | Typed + narrowing filter | SUCCESS, claims only | SUCCESS, claims only | PASS |
| 3d | Catalog without schema | FAIL with "both or neither" message | Exact expected message | PASS |
| 4a | Filter matches nothing | FAIL naming all supplied values | Exact expected message, all 3 values named | PASS |

---

## What Happened

Ran the full H10 hardened-`filter_expr` test suite against the live bundle deployed in `fe-sandbox-manocha` (target `dev-serverless`). The test doc was written against a `sandeep_manocha` catalog that doesn't exist in this workspace — all commands were adapted to use the actual `dev2_archive.metadata.global_settings` config table and `dev2_archive.source_data_samples` source schema; those adaptations are recorded in the Path Adaptations table above and are mechanical substitutions only (no semantic changes).

Every forbidden-token filter (Phase 1a–1c) was rejected at `src/config.py:153` before any SQL ran against source tables. The critical payload test — embedding `; DROP TABLE ... members` inside the filter (1a) — was rejected and the `members` table's schema was verified intact afterward. The inline-comment case (1b) also failed correctly, which matters because that's the shape of a real operator typo that would otherwise silently widen scope to include `is_active = false` rows.

Invalid-but-not-forbidden filters (Phase 2a, 2b) exercised the `spark.sql(...LIMIT 0)` probe at `src/config.py:158`. The probe caught both `UNRESOLVED_COLUMN` and `PARSE_SYNTAX_ERROR`, wrapped them in `ArchiveConfigError(field="filter_expr", ...)`, and surfaced them as the top-level job failure. The resulting messages include column-name suggestions straight from Spark's analyzer — operators can fix the widget without opening task logs. The valid-filter sanity check (2c) succeeded and the audit log confirms only `claims` was considered.

Phase 3 verified the new typed-widget surface. Typed widgets alone (3a) and the equivalent raw `filter_expr` (3b) resolved identical 3-table scopes. Combining typed widgets with a narrowing `filter_expr` (3c) correctly AND-ed to just `claims`. The half-typed-params guard (3d) produced the exact `"source_catalog and source_schema must both be provided together"` message.

Phase 4 confirmed the empty-result trap is not a trap: a filter that matches nothing produces a readable error that names every supplied filter value — so the operator can tell at a glance which filter excluded everything.

No Phase 5 cleanup was needed (all runs were dry-run; ~8 `STARTED` audit rows remain in `dev2_archive.metadata.archive_audit_log` across 4 distinct `archive_run_id`s — harmless).

---

## Next Steps

- **None required for this test.** All 11 sub-cases passed. The H10 hardening (forbidden-token guard + typed params + empty-result guard + `LIMIT 0` parse probe) behaves exactly as specified in `tests/databricks/test_cases/28T_filter_expr_guards.md`.
- **Recommend updating the test doc** to either (a) parameterize the catalog/schema/profile/target or (b) switch the hard-coded `sandeep_manocha.caresource_audit.*` paths to `dev2_archive.metadata.*` and the target to `dev-serverless`, so future runs in this workspace don't require the mechanical substitutions captured above. A small `<env>` fence near the top of `28T_*.md` listing `{PROFILE, TARGET, SOURCE_CATALOG, SOURCE_SCHEMA, CONFIG_TABLE}` would mirror the style already used in `23R_*.md` results and keep the runbook portable.
- **Optional cleanup** (not needed): `DELETE FROM dev2_archive.metadata.archive_audit_log WHERE archive_run_id IN ('5d27bedd-474d-4b4d-98f3-727baf1ac63e','6f859ffb-13c2-45a4-9293-5b5dd69c4006','b59e398f-90e7-4a04-aeaa-5da5a98af27c','d771277b-016b-470f-8699-a729f8bb30e9')` — removes dry-run `STARTED` rows from this test run.
