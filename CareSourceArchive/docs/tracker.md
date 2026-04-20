# CareSource Archive — Feature Tracker

## Status Legend

| Status | Meaning |
|--------|---------|
| Not Started | Feature not yet begun |
| In Progress | Currently being developed |
| Code Complete | Code written, not yet tested |
| Tested | Unit/integration tests passing |
| Deployed | Running in Databricks workspace |

---

### Documentation

| Document | Status | Path |
|----------|--------|------|
| Requirements | Complete | docs/requirements.md |
| Requirements Summary | Complete | docs/requirements-summary.md |
| Design | Complete | docs/design.md |
| Feature List | Complete | docs/features.md |
| Tracker | Complete | docs/tracker.md |
| Development Rules | Complete | docs/development-rules.md |
| Prerequisites | Complete | docs/prerequisites.md |
| 12-Week Plan | Complete | docs/12-week-plan.md |
| Code Dependency Graph | Complete | docs/code-dependency-graph.md |
| Decisions Log | Complete | docs/decisions.md |

### Configuration (Delta Tables)

| Component | Feature | Status | Notes |
|-----------|---------|--------|-------|
| global_settings Delta table | F3, F15 | Code Complete | DDL in notebooks/setup_config_tables.py. Single-row bootstrap table per environment |
| schema_templates Delta table | F11, F15 | Code Complete | DDL in notebooks/setup_config_tables.py. One row per schema — `schema_id` (PK), `(source_catalog, source_schema)` unique in code. Scanner input with optional targeted scan |
| table_configs Delta table | F3, F11, F15 | Code Complete | DDL in notebooks/setup_config_tables.py. One row per table — archive job input |
| table_configs_staging Delta table | F11, F15 | Code Complete | DDL in notebooks/setup_config_tables.py. Scanner output — MERGE reconciles with table_configs |
| Seed data | F15 | Code Complete | Seed INSERT logic in notebooks/setup_config_tables.py. Migrated from previous JSON samples |

### Package & Infrastructure

| Component | Feature | Status | Notes |
|-----------|---------|--------|-------|
| pyproject.toml | F14 | Tested | setuptools backend, `caresource-archive` package, pytest config. Managed by `uv` |
| .python-version | F14 | Tested | Pin to `3.10` (matches Databricks runtime) |
| uv.lock | F14 | Not Started | Deferred — PyPI unreachable from current network. Will generate when network available |
| .gitignore | F14 | Tested | `.venv/`, `__pycache__/`, `.pytest_cache/`, `*.egg-info/`, `dist/`, `build/` |

### Core Modules

| Module | Feature | Status | Tests | Notes |
|--------|---------|--------|-------|-------|
| src/exceptions.py | F13 | Tested | 14/14 passing | ArchiveConfigError, ArchiveOperationError, ArchiveVerificationError |
| src/utils.py | F1 | Tested | 16/16 passing | RunContext dataclass + helpers + configure_logging() |
| src/audit.py | F2 | Tested | 30/30 passing | AuditLogger class + concurrency check + watermark tracking |
| src/config.py | F3 | Tested | 19/19 passing | Pure functions: read Delta config tables, validate + CFG-10 (`min_table_size_gb` validation) |
| src/conditions.py | F4 | Tested | 17/17 passing | Pure functions: SQL generation |
| src/scanner.py | F11 | Tested | 23/23 passing | Pure functions: UC scan + size check + staging table + MERGE to table_configs |
| src/archiver.py | F5, F6 | Tested | 33/33 passing | ArchiveEngine class + watermark-driven CREATE/APPEND/SKIP + concurrency + NULL handling |
| src/rehydrator.py | F7 | Tested | 15/15 passing | RehydrationEngine class — views instead of external tables (D45) |

### Features

| Feature | Feature ID | Status | Tests | Depends On |
|---------|-----------|--------|-------|------------|
| Exception hierarchy | F13 | Tested | 14/14 passing | — |
| Package setup (pyproject.toml) | F14 | Tested | — | — |
| Shared utilities + RunContext + logging | F1 | Tested | 20/20 passing | F13 |
| Audit logging (AuditLogger) + concurrency | F2 | Tested | 23/23 passing | F13 |
| Config loading from Delta tables, validation | F3 | Tested | 19/19 passing | F1 |
| Condition SQL builder | F4 | Tested | 17/17 passing | F3 |
| Dry-run mode | F5 | Tested | 22/22 (in archiver) | F4, F6, F2 |
| Archive engine + concurrency + NULL handling | F6 | Tested | 22/22 passing | F1, F2, F3, F4 |
| Rehydration engine (RehydrationEngine) | F7 | Tested | 15/15 passing | F1, F2 |
| Archive notebook + job | F8 | Code Complete | — | F6, F3 |
| Rehydrate notebook + job | F9 | Code Complete | — | F7 |
| Test suite | F10 | Tested | 208/209 unit (1 pre-existing scanner failure) | All |
| Schema scanner (size threshold + two-stage scan) | F11 | Tested | 23/23 passing | F3 |
| Scanner notebook (with force flag) | F12 | Code Complete | — | F11 |
| Config Delta table setup | F15 | Code Complete | — | — |

### Notebooks

| Notebook | Feature | Status | Notes |
|----------|---------|--------|-------|
| notebooks/setup_config_tables.py | F15 | Code Complete | One-time per env: create config Delta tables + optional seed data |
| notebooks/generate_parameters.py | F8 | Code Complete | Config Delta table → ForEach values + archive_run_id |
| notebooks/run_archive.py | F8 | Code Complete | Thin wrapper → ArchiveEngine |
| notebooks/run_rehydrate.py | F9 | Code Complete | Thin wrapper → RehydrationEngine |
| notebooks/run_scanner.py | F12 | Code Complete | Thin wrapper → scanner functions (with force flag) |
| notebooks/manual/validate_config.py | F8 | Code Complete | Validate all config Delta tables |
| notebooks/manual/validate_archives.py | F8 | Code Complete | Health check: verify archive folders |

### Job Orchestration

| Component | Feature | Status | Notes |
|-----------|---------|--------|-------|
| resources/setup_job.yml | F15 | Code Complete | Setup job: create config Delta tables (one-time per env), DABs targets per env |
| resources/archive_job.yml | F8, F9 | Code Complete | Archive ForEach job + rehydrate on-demand job, DABs targets per env, SP `run_as` for higher envs |
| resources/scanner_job.yml | F12 | Code Complete | Scanner on-demand job: discover tables, match date columns, MERGE to table_configs. SP `run_as` for higher envs |

### Test Infrastructure

| Component | Feature | Status | Notes |
|-----------|---------|--------|-------|
| tests/conftest.py | F10 | Tested | Shared fixtures: mock_spark, mock_run_context, mock_audit |

### Unit Tests

| Test File | Feature | Status | Derives From | Notes |
|-----------|---------|--------|-------------|-------|
| tests/unit/test_exceptions.py | F10, F13 | Tested | ERR-01 to ERR-04 | 14 tests — Exception hierarchy + structured messages |
| tests/unit/test_utils.py | F10, F1 | Tested | F1.1-F1.11, LOG-01-03 | 20 tests — RunContext, path formatting, logging, secrets |
| tests/unit/test_audit.py | F10, F2 | Tested | AUD-01-08, CONC-02 | 30 tests — AuditLogger class, concurrency check, watermark tracking |
| tests/unit/test_config.py | F10, F3 | Tested | CFG-01-10 | 19 tests — Config loading, validation, two-tier merge |
| tests/unit/test_conditions.py | F10, F4 | Tested | EXC-01-05 | 17 tests — Operator SQL generation, custom_sql placeholders |
| tests/unit/test_scanner.py | F10, F11 | Tested | SCN-01-16, CFG-10 | 23 tests — Pattern matching, size check, staging, MERGE, scanner log |
| tests/unit/test_archiver.py | F10, F5, F6 | Tested | ARC-01-13, DRY-01-05, CONC-01-04, EDGE-01-03, WM-01-07 | 33 tests — Archive engine, watermark CREATE/APPEND/SKIP, dry-run, concurrency, NULL handling, count drift, safety checks |
| tests/unit/test_rehydrator.py | F10, F7 | Tested | RHY-01-06, ROLL-01 | 7 tests — Rehydration engine, external tables, unified view |

### Integration Tests (requires Databricks cluster)

| Test File | Feature | Status | Derives From | Notes |
|-----------|---------|--------|-------------|-------|
| tests/integration/test_archive_flow.py | F10.5 | Not Started | ARC-01-11, DRY-01-05, CONC-01-04, EDGE-01-03 | End-to-end archive flow with real Spark + Delta |
| tests/integration/test_rehydrate_flow.py | F10.6 | Not Started | RHY-01-06, ROLL-01 | End-to-end rehydration with real Spark |
| tests/integration/test_audit_flow.py | F10.7 | Not Started | AUD-01-08 | Audit table creation, status writes, correlation |

### Interactive Scripts (manual validation, not CI)

| Script | Feature | Status | Notes |
|--------|---------|--------|-------|
| tests/interactive/try_config_load.py | F10.9 | Not Started | Load and display config from real Delta tables |
| tests/interactive/try_archive_single_table.py | F10.9 | Not Started | Archive one table, inspect results |
| tests/interactive/try_rehydrate.py | F10.9 | Not Started | Rehydrate archived years, verify views |
| tests/interactive/try_scanner.py | F10.9 | Not Started | Scan a schema, inspect staging + merged results |

---

## Backlog

| Item | Scope | Notes |
|------|-------|-------|
| Refactor archiver INSERT-SELECT into shared SQL helper | `src/archiver.py` | Category C from INSERT consolidation brainstorm — `INSERT INTO delta.\`{path}\` SELECT ...` in `_archive_year` is structurally different (INSERT-SELECT, not INSERT-VALUES) and context-specific. Tackle after audit + scanner SQL helpers are unified |

---

## Change Log

| Date | Change | By |
|------|--------|-----|
| 2026-03-25 | Initial documentation created: requirements, design, features, prompt, tracker | Brainstorming session |
| 2026-03-25 | Three-tier config architecture: global settings + schema templates + table configs (user-organized). Added scanner (F11, F12) | Brainstorming session |
| 2026-03-26 | Added: selective OOP (RunContext, AuditLogger, ArchiveEngine, RehydrationEngine), enumerated audit statuses, Databricks job context columns, archive_run_id, archive metadata, config validation notebook, resumable archive, schema-level defaults, archive health check | Design review |
| 2026-03-26 | Added: requirements-first testing (NFR-07, D12). Tests must be written BEFORE implementation, derived from requirement IDs, not from code. Per-module workflow: read requirements → write tests → tests fail → write code → tests pass | Testing strategy |
| 2026-03-26 | Added: development-rules.md — 11 rules covering test-first, SDK usage, code quality, modularity, progress tracking, dependency graph. Restructured tests/ into unit/, integration/, interactive/. Added test_audit.py (F10.7) and interactive scripts (F10.8) | Development rules |
| 2026-03-26 | Operational design brainstorm: added 8 new areas — error handling (ERR-01–04, F13), package management (NFR-08, F14), NULL/edge cases (EDGE-01–05), concurrency guards (CONC-01–04), environment management (NFR-09, F15), CI/CD pipeline (NFR-10), application logging (LOG-01–05), rollback/recovery (ROLL-01–04). Updated audit statuses with STARTED + SKIPPED_CONCURRENT (D8). Added decisions D13–D20. Updated all docs | Brainstorming session |
| 2026-03-26 | Added: service principal execution for higher environments (JOB-05, JOB-06, NFR-11, D21, F8.8, A6). DABs `run_as` per target, SP permission matrix, `current_user()` captures SP identity in audit. SP provisioning is out of scope (CareSource platform team) | Brainstorming session |
| 2026-03-26 | Added: M2M OAuth for CI/CD deployment (JOB-07, D22, A7, F8.9) and Databricks secret scopes for runtime credentials (JOB-08, JOB-09, D23, A8, F1.11, F8.10, F15.5). Warehouse IDs and credentials in scopes, non-sensitive config in JSON files. Per-env scope naming `archive-{env}`. Updated RunContext to include secrets dict. Added `load_secrets()` to utils. Added dev rules 15-16 (secrets in scopes, never log secrets) | Brainstorming session |
| 2026-03-26 | Added: prerequisites.md — infrastructure needs, admin personnel requirements, Day 1 / Week 1 / pre-QA / pre-Prod checklist. Covers UC catalogs, storage, credentials, SPs, secret scopes, CI/CD, and business stakeholder inputs | Prerequisites |
| 2026-03-27 | Added: scanner size threshold (CFG-10, SCN-08–SCN-13, D24–D27) and two-stage scan architecture (intermediate JSON → diff/merge → final config). Updated requirements.md, features.md (F11 rewritten with 10 tasks), design.md (new scanner flowchart + architecture diagram), requirements-summary.md, tracker.md. Clarified NFR-06 (classes vs pure functions for spark/dbutils). Added `config/scanner_output/` directory. Spec at `docs/superpowers/specs/2026-03-27-scanner-size-threshold-two-stage-design.md` | Brainstorming session |
| 2026-03-27 | Simplified NULL date handling: removed `null_date_policy` config field and `archive_to_unknown`/`year_0000/` concept. NULL date is now a required value error — records with NULL dates are always excluded, count logged as ERROR in audit and application logs. Updated D14. Collapsed EDGE-01/02/03 into single EDGE-01, renumbered EDGE-04→02 (schema evolution), EDGE-05→03 (timezone). Removed `null_date_policy` from SCN-11 diff fields. Updated all docs: requirements.md, requirements-summary.md, design.md, features.md, development-rules.md, prerequisites.md | Design simplification |
| 2026-03-30 | Added: archive path validation via UC external locations (SCN-14, D28, F11.11). Scanner validates `archive_base_path` against `SHOW EXTERNAL LOCATIONS` before scanning tables — raises `ArchiveConfigError` if path not covered. Fail-fast at onboarding time. Updated: requirements.md, requirements-summary.md, design.md (scanner flowchart + validation section), features.md (F11.11, F10.2), tracker.md | Path validation |
| 2026-03-30 | Added: ambiguous date column detection (CFG-06, SCN-02, SCN-07, SCN-13 updated, D29). When a single `date_column_patterns` entry matches multiple columns in a table, scanner flags it `is_active: false` with reason listing the pattern and all matched columns. Operator resolves by adding a specific pattern or manually setting `date_column`. Added: scanner log Delta table (SCN-15, SCN-16, D30, F11.12, F11.13, F12.3). Separate from archive audit — per-table detail rows per scan capturing pattern matching results, ambiguity details, sizes, and merge actions. Queryable via SQL, exportable to CSV/Excel. Updated: requirements.md, requirements-summary.md, design.md (scanner flowchart with ambiguity branch + scanner_log table schema + architecture diagram + example queries), features.md (F11.2, F11.4, F11.10, F11.12, F11.13, F10.2, F12.2, F12.3), settings.json (scanner_log_catalog/schema), tracker.md | Ambiguity handling + scanner log |
| 2026-03-30 | Doc sync: removed `scanner_log_catalog`/`scanner_log_schema` from `settings.json` — code will default to `audit_catalog`/`audit_schema` per design doc (fixes env overlay mismatch). Added `12-week-plan.md` to tracker. Updated tracker config notes to reflect sample files created. Clarified TDD per phase in 12-week plan (unit tests written alongside each feature, Phase 5 is integration/E2E only) | Doc sync review |
| 2026-03-31 | **Major: Replaced all JSON config files with Delta tables.** Deleted `config/` directory (settings.json, environments/, schemas/, tables/). All configuration now stored in 4 Delta tables: `global_settings` (1 row, bootstrap entry point), `schema_templates` (1 row/schema), `table_configs` (1 row/table), `table_configs_staging` (scanner output). Eliminated environment overlay concept — each workspace has its own config tables. Scanner uses staging table + MERGE instead of intermediate JSON files. DDL and seed data in `notebooks/setup_config_tables.py`. Updated design.md (Sections 1, 2, 9, 10, 11, 12, 15, 16), requirements-summary.md (Section 5, 7), features.md (F3, F8, F10, F11, F12, F15), tracker.md | Delta config migration |
| 2026-03-31 | Added setup job for config table creation (JOB-10, F15 rewritten). `notebooks/setup_config_tables.py` creates all 4 config Delta tables via `CREATE TABLE IF NOT EXISTS` + optional seed. `resources/setup_job.yml` DABs job definition. Idempotent. Updated: design.md (Section 10 + project structure), features.md (F15), requirements.md (JOB-10), tracker.md | Design update |
| 2026-03-31 | **`[PENDING CUSTOMER DISCUSSION]` Incremental archive for existing year folders (ARC-12, ARC-13, D31, A10, F6.15, F6.16).** When archiving the same table multiple times and a year folder already exists: `delete_after_archive=true` → APPEND newly-eligible records (source only has leftovers, no dedup needed); `delete_after_archive=false` → SKIP by default, use `year_override` for full re-archive. Sub-folders within year folders rejected (breaks one-Delta-table-per-year model, complicates rehydration, doesn't solve dedup). Added `mode` field to `_archive_metadata.json` (`"write"` or `"append"`). Rehydration unaffected. Updated: requirements-summary.md (archiving section + A10), requirements.md (ARC-12, ARC-13, D31), design.md (Section 3 flowchart + incremental archive subsection + metadata schema + audit state machine), features.md (F6.4, F6.8, F6.15, F6.16), tracker.md | Brainstorming session |
| 2026-03-31 | **Added `uv` as sole package manager.** Replaced all `pip` references with `uv sync`, `uv run pytest`, `uv build`. Added `.python-version` (pin 3.10) and `uv.lock` to tracked files. Renamed Package section to Package & Infrastructure. Added new tracker sections: Test Infrastructure (conftest.py), Unit Tests (8 files with requirement traceability), Integration Tests (3 files, requires cluster), Interactive Scripts (4 files, manual validation). Total tracked files: 37. Updated: tracker.md, plan | Planning session |
| 2026-03-31 | **Implementation: All core modules + notebooks complete.** Scaffolding → F13 exceptions (14) → F1 utils (20) → F2 audit (23) → F3 config (19) → F7 rehydrator (7) → F4 conditions (17) → F11 scanner (23) → F6+F5 archiver (22). Total: 145/145 unit tests. All notebooks (setup, generate_parameters, run_archive, run_rehydrate, run_scanner, validate_config, validate_archives) and DABs jobs (setup_job.yml, archive_job.yml, databricks.yml) created. Doc updates for uv. Tracker, progress-report, code-dependency-graph current | Build session |
| 2026-04-02 | **Schema ID & scanner targeting (D32).** Added `schema_id` as human-readable PK to `schema_templates` (replaces composite `(source_catalog, source_schema)` PK; uniqueness enforced in code). `load_schema_templates` now accepts optional `schema_id` parameter and always filters `is_active = true`. Scanner job accepts optional `schema_id` parameter for single-template targeting. Zero active templates → early return (no staging/merge). Removed dead `dbutils` parameter from `run_scanner`. Uses `sql_quote()` for safe SQL construction. Updated: DDL in `setup_config_tables.py`, `src/config.py`, `src/scanner.py`, `notebooks/run_scanner.py`, `resources/scanner_job*.yml`, tests. Spec: `docs/superpowers/specs/2026-04-02-schema-id-scanner-targeting-design.md`. Updated all docs: requirements.md (CFG-02, SCN-01, D32), requirements-summary.md, features.md (F3.3, F10.1, F10.2, F11.10, F12.1), design.md (schema_templates table, scanner flow), progress-report.md, tracker.md | Brainstorming session |
| 2026-04-02 | **INSERT SQL consolidation (Rule 23).** Extracted 6 SQL quoting helpers (`sql_quote`, `sql_str_or_null`, `sql_int`, `sql_int_or_null`, `sql_bool`, `sql_expr`) and 2 INSERT builders (`build_insert_values_sql`, `build_multi_insert_values_sql`) into `src/utils.py`. Deleted 6 duplicated private helpers from `audit.py` (4) and `scanner.py` (2). Refactored `log_archive`, `log_rehydrate`, `write_staging`, `write_scanner_log` to use shared builders. Added column constants `ARCHIVE_AUDIT_COLUMNS` / `REHYDRATION_AUDIT_COLUMNS` and `_job_context_values` helper. Mechanical rename of `_sql_quote`/`_sql_str` → shared imports in `check_resume_state`, `check_concurrent`, `merge_staging_to_final`. Added 21 new tests. Total: 186/186 unit tests passing. Added development Rule 23 (DRY SQL statements). Archiver INSERT-SELECT tracked in backlog (Category C). Spec: `docs/superpowers/specs/2026-04-02-insert-sql-consolidation-design.md` | Refactoring session |
| 2026-04-06 | **Incremental archive runs — watermark-driven rewrite.** Implemented design from `docs/superpowers/specs/2026-04-06-rerun-force-design-DRAFT.md`. Phase 1: Renamed `date_column` → `watermark_column` across codebase. Phase 2: Added `watermark_value`, `source_year_count`, `archive_mode` to audit DDL; added `get_last_run_state()` to audit module. Phase 3: Rewrote `_process_year_live` with watermark CREATE/APPEND/SKIP; removed `year_override`/`replace` mode; added safety checks (missing folder after delete, count drift); `sql_quote()` for watermark values; Delta fallback for missing audit watermark. Phase 4: Removed `year_override` from notebooks + job YAMLs. Updated docs. Total: 208/209 unit tests (1 pre-existing scanner failure). Branch: `feat/delta_config_build_v2_reruns` | Implementation session |
| 2026-04-16 | **Rehydrator: views instead of external tables (D45).** UC blocks `CREATE TABLE LOCATION` on Volume-governed paths. Replaced `_create_external_table` with `_create_archive_view` using `CREATE OR REPLACE VIEW ... AS SELECT * FROM delta.\`path\``. Per-year views + unified view, zero-copy. Removed `sql_quote` import. Updated 4 tests (SQL pattern matching now distinguishes per-year views from unified view). 15/15 tests passing. Live run succeeded on `fe-sandbox-manocha` dev-serverless for providers (6 years). Spec: `docs/superpowers/specs/2026-04-16-rehydrator-views-design.md` | Implementation session |

---

## Iteration Tracking

### Iteration 1: Incremental Archive Runs (2026-04-06)

**Branch:** `feat/delta_config_build_v2_reruns`
**Spec:** `docs/superpowers/specs/2026-04-06-rerun-force-design-DRAFT.md` (FINAL)

| Phase | Description | Status | Commit |
|-------|-------------|--------|--------|
| 1 | Rename `date_column` → `watermark_column` across codebase | Complete | `a6d37dd` |
| 2 | DDL new columns + Audit module TDD (`get_last_run_state`, `log_archive`) | Complete | `660ee50` |
| 3 | Archiver rewrite: watermark CREATE/APPEND/SKIP, remove year_override/replace, 7 new tests | Complete | `87778b6` |
| 4 | Remove year_override from notebooks + jobs, update docs | Complete | — |
| 5 | Final harsh review, fix issues, final commit | Pending | — |

**Harsh review findings addressed (Phase 5):**

| # | Severity | Finding | Resolution |
|---|----------|---------|------------|
| 1 | CRITICAL | `get_last_run_state` returns SKIPPED rows with NULL watermark | Fixed: filter to `status IN ('ARCHIVED', 'ARCHIVED_AND_DELETED')` |
| 2 | HIGH | Watermark interpolated without `sql_quote()` — SQL injection risk | Fixed: use `sql_quote(last_watermark)` |
| 3 | HIGH | `year_override` still in job/generate_parameters | Fixed in Phase 4 |
| 4 | MEDIUM | Dead `dbutils` param on `_archive_year` (Rule 26) | Fixed: removed param and call sites |
| 5 | MEDIUM | Folder exists + `last_wm` None (no Delta fallback) | Fixed: read `MAX(wm_col)` from Delta |
| 6 | INFO | Count verification reads full archive, not just appended batch | Accepted: self-consistency check |

### Iteration 2: Phase 1 Code Reduction (2026-04-07)

**Branch:** `feat/delta_config_build_v3_code_reduce`
**Spec:** `docs/reconciliation-reports/code_reduction_report_20260407.md`
**Baseline:** 6,645 lines (src + tests + conftest) | 224 passed, 1 pre-existing failure

| Phase | Description | Status | Lines Saved |
|-------|-------------|--------|-------------|
| 1 | Batch 1: Parametrize test_conditions, test_utils, test_rehydrator, test_exceptions | Complete | -85 lines |
| 2 | Batch 2: scanner.py config builder, archiver.py helpers, dead loggers | Complete | -10 lines |
| 3 | Batch 3: conftest factory, test_audit parametrize, test_scanner parametrize | Complete | -126 lines |
| 4 | Final validation: line counts, test counts, Databricks deploy + run | Complete | -220 total |

**Final line counts (before → after):**

| File | Before | After | Delta |
|------|--------|-------|-------|
| src/scanner.py | 611 | 600 | -11 |
| src/archiver.py | 683 | 690 | +7 |
| src/utils.py | 236 | 234 | -2 |
| src/conditions.py | 65 | 61 | -4 |
| tests/unit/test_conditions.py | 191 | 138 | -53 |
| tests/unit/test_rehydrator.py | 206 | 176 | -30 |
| tests/unit/test_exceptions.py | 132 | 127 | -5 |
| tests/unit/test_audit.py | 525 | 449 | -76 |
| tests/unit/test_scanner.py | 793 | 744 | -49 |
| tests/unit/test_utils.py | 374 | 377 | +3 |
| **Total** | **6,645** | **6,425** | **-220** |

**Tests:** 228 passed (was 224 — parametrization expanded test count), same 1 pre-existing failure.

**Harsh review findings addressed:**

| # | Severity | File | Finding | Resolution |
|---|----------|------|---------|------------|
| 1 | HIGH | test_utils.py | Weak assertion in test_returns_nulls_when_tags_empty | Fixed: restored strict dict equality |
| 2 | HIGH | test_exceptions.py | Line count increased (+3) due to verbose parametrize | Fixed: compacted pair test, merged class (-5 now) |
| 3 | MEDIUM | test_scanner.py | Ambiguous case asserts list order instead of set | Fixed: use sorted() comparison |
| 4 | MEDIUM | test_scanner.py | Unused `import json` | Fixed: removed |
| 5 | MEDIUM | scanner.py | docs/features.md has stale flag_unmatched signature | Deferred to Phase 2 |
| 6 | MEDIUM | archiver.py | Centralized count alias assumption (pre-existing) | Accepted: inherited behavior |

**Databricks validation (2026-04-07, profile DEFAULT, target dev):**

| Job | Status | Notes |
|-----|--------|-------|
| Scanner | SUCCESS | 14 tables scanned: 6 matched, 8 unmatched, 0 ambiguous. Same results as prior runs. |
| Archive dry-run | SUCCESS | generate_parameters + for_each completed. |
| Archive real (dry_run=false) | SUCCESS | STARTED/SKIPPED pairs for claims, members, providers across 2021-2025. Same behavior as prior. |

**Iteration 2 status: COMPLETE**

### Iteration 3: Scanner Code Reduction (2026-04-07)

**Branch:** `feat/delta_config_build_v3_code_reduce`
**Spec:** `.cursor/plans/scanner_code_reduction_bc914747.plan.md`
**Detail tracker:** `docs/reconciliation-reports/scanner_reduction_tracker.md`
**Baseline:** 6,425 lines (src + tests + conftest) | 228 passed, 1 pre-existing failure

| Batch | Description | Status | Lines Saved |
|-------|-------------|--------|-------------|
| 1 | Scanner quick wins (S5+S6+S9+S11) + features.md fix | Done | -10 |
| 2 | Scanner log factory (S7+S4) | Done | -31 |
| 3 | Utils extraction + dead code (S2+U3+U1+C6+C7+NEW) | Done | -38 src, +16 tests |
| 4 | Scanner readability (S3+S8, optional) | Skipped | 0 |
| 5 | Propagate utils to audit, config, archiver | Done | -9 |
| — | Databricks validation | Done | — |

**Final:** 6,425 → 6,353 lines (**-72 net**, -88 src, +16 tests) | 237 passed (+9 new), 1 pre-existing failure

**Harsh review findings addressed:**

| # | Severity | File | Finding | Resolution |
|---|----------|------|---------|------------|
| 1 | MEDIUM | scanner.py | S11: non-dict asDict() edge case | Accepted: Spark Row.asDict() always returns dict |
| 2 | MEDIUM | scanner.py | S7: open-ended **overrides | Accepted: private function, 4 call sites |
| 3 | HIGH | scanner.py | ensure_scanner_log_table: lost exception chaining | Fixed: added `from e` |
| 4 | HIGH | scanner.py | SQL changed from information_schema to DESCRIBE TABLE | Accepted: intentional per plan |
| 5 | MEDIUM | utils.py | row_value: asDict-first fallback change | Accepted: real Spark rows always have asDict |
| 6 | HIGH | config.py | row_to_dict returns {} for non-Row | Accepted: only affects pathological mocks |
| 7 | MEDIUM | audit.py | ensure_table_exists messaging conflates failures | Accepted: same behavior as original |

**Databricks validation (2026-04-07, profile DEFAULT, target dev):**

| Job | Status | Notes |
|-----|--------|-------|
| Scanner | SUCCESS | 14 tables scanned: 6 matched, 8 unmatched, 0 ambiguous. Identical to Iteration 2. |
| Archive dry-run | SUCCESS | 25 DRY_RUN entries across 6 tables. Same as prior runs. |
| Archive real (dry_run=false) | SUCCESS | 25 STARTED + 25 SKIPPED across 6 tables. Same STARTED/SKIPPED pattern as Iteration 2. |

**Iteration 3 status: COMPLETE**

### Diagnostic Messaging (2026-04-09)

**Branch:** `feat/delta_config_build_v3_code_reduce`
**Spec:** `docs/superpowers/specs/2026-04-09-diagnostic-messaging-design.md`

| Phase | Description | Status | Notes |
|-------|-------------|--------|-------|
| 1 | `exceptions.py`: `_DIAGNOSTIC_TEMPLATES` + `diagnostic_message()` static method on `ArchiveError`; `ArchiveOperationError` and `ArchiveVerificationError` `msg` required, attrs stored | Done | 6 template keys across 2 statuses |
| 2a | `archiver.py`: 10 call sites migrated to `diagnostic_message()` | Done | SKIPPED_CONCURRENT, 2x ownership, missing_folder, orphan_folder, verify, generic catch, archive_year, delete_archived, ERROR action |
| 2b | `rehydrator.py`: 1 call site migrated | Done | Added structured kwargs (table, year, operation, reason) |
| 3a | `test_exceptions.py`: 11 new tests (diagnostic_message + attribute storage) | Done | 31 total tests |
| 3b | `test_archiver.py`: 4 assertion updates | Done | orphan_folder x3, positional msg x1 |
| 4 | Docs: `status_affects.md` operator messages section, `tracker.md` progress log | Done | — |

**Harsh review findings addressed:**

| # | Severity | File | Finding | Resolution |
|---|----------|------|---------|------------|
| 1 | HIGH | exceptions.py | `diagnostic_message` only caught `KeyError`; `str.format` can also raise `TypeError`/`ValueError` (violates D6 "never raises") | Fixed: broadened to `except (KeyError, TypeError, ValueError)` |
| 2 | LOW | rehydrator.py | `ArchiveOperationError(msg)` dropped structured kwargs | Fixed: added `table=`, `year=`, `operation=`, `reason=` |
| 3 | MEDIUM | test_exceptions.py | No test for `TypeError` in template formatting | Fixed: added `test_type_error_in_format_returns_fallback` |
| 4 | MEDIUM | archiver.py | Audit `error_message` on generic catch uses `str(exc)` while raised exception uses diagnostic message | Accepted: audit keeps raw root cause for debugging; raised exception adds context |
| 5 | LOW | archiver.py | `stale_threshold_hours` renders as `4.0h` not `4h` | Accepted: cosmetic only |

**Final:** 271 passed (+9 new), 1 pre-existing failure (`test_scanner.py::test_scn16_merge_insert_includes_scan_run_id`)

**Diagnostic Messaging status: COMPLETE**

### Prerequisites Restructure + Secret Scope Removal (2026-04-12)

**Branch:** `feat/delta_config_build_v3_code_reduce`
**Commit:** `04d8fcb`
**Revert point:** `8db712a` (commit immediately before this changeset)
**To revert:** `git revert 04d8fcb` (safe — creates a new undo commit) or `git reset --hard 8db712a` (destructive — discards this commit entirely)

#### Part 1 — Prerequisites split into smaller files

Broke `docs/prerequisites.md` (568 lines) into 4 focused runbooks:

| File | Lines | Contents |
|------|-------|----------|
| `docs/prerequisites.md` | ~114 | Slim checklist + per-env infra + source table info + who does what + runbook index |
| `docs/runbooks/service-principals.md` | 79 | SP creation (UI + CLI), add to workspace |
| `docs/runbooks/uc-permissions.md` | 73 | Required grants table + SQL + verification |
| `docs/runbooks/qa-environment.md` | 110 | End-to-end QA walkthrough |

**Fixes applied during the split:**
- "Audit catalog + schema" → "Config catalog + schema" with names matching `databricks.yml` (`qa_archive_operations.config`, etc.)
- Grant SQL updated to reference config catalog, not `qa_archive.audit`
- "NULL date column" → "NULL watermark column"
- QA runbook notebook parameters: `audit_catalog`/`audit_schema` → `config_catalog`/`config_schema`
- Added note that setup creates 7 tables (4 config + 3 audit/log)

#### Part 2 — OAuth M2M credential removal from docs

Removed Step 3 (Generate OAuth M2M credentials) from service-principals runbook and all M2M references from prerequisites. Jobs authenticate via DABs `run_as`, not M2M secrets.

#### Part 3 — Full secret scope removal (code + docs)

**Rationale:** `load_secrets()` loaded `warehouse_id`, `client_id`, `client_secret` from Databricks secret scopes, but none were ever used downstream. `spark.sql()` executes on whatever compute the job/notebook is attached to (classic cluster or serverless) — no warehouse ID needed. SP authentication is handled by DABs `run_as`. The entire secrets pipeline was dead code.

**Code removed:**
- `src/utils.py`: `load_secrets()`, `_KNOWN_SECRET_KEYS`, `secrets` field from `RunContext` dataclass
- `src/config.py`: `"secret_scope"` from `_REQUIRED_SETTINGS_KEYS`
- `notebooks/setup_config_tables.py`: `secret_scope` column from `global_settings` DDL
- `notebooks/seed_config.py`: `secret_scope` from seed INSERT
- `notebooks/run_archive.py`, `run_rehydrate.py`, `manual/validate_archives.py`: `load_secrets` import/call, `secrets=` from `RunContext`

**Tests removed/updated:**
- `test_utils.py`: Entire `TestLoadSecrets` class (4 tests), `secrets` from `RunContext` tests
- `test_config.py`: `secret_scope` from fixtures and required-keys assertion
- `test_archiver.py`: `secrets={}` from 3 `RunContext` constructors, `secret_scope` from settings fixture
- `test_rehydrator.py`: `secrets={}` from `RunContext`
- `conftest.py`: `secret_scope` from settings, `secrets=` from `RunContext`
- `test_audit.py`: `secrets` field from `_TestRunContext` and `_make_ctx`

**Docs removed/updated:**
- Deleted `docs/runbooks/secret-scopes.md`
- `prerequisites.md`: removed all secret scope checklist items, scope from per-env table, scope from admin roles
- `runbooks/uc-permissions.md`: removed secret scope ACL row
- `runbooks/qa-environment.md`: removed step 5 (secret scope creation)

**Known remaining doc debt:** 8 other doc files (`design.md`, `requirements.md`, `requirements-summary.md`, `features.md`, `development-rules.md`, `high-level-architecture.md`, `images/architecture.dot`, `reconciliation-reports/source_code_summary.md`) still reference `secret_scope`/`load_secrets`. These are historical design docs; will clean in a future pass.

**Tests:** 267 passed, 1 pre-existing failure (unchanged). Net: -4 tests from `TestLoadSecrets` removal, offset by prior additions.

**Prerequisites Restructure + Secret Scope Removal status: COMPLETE**
