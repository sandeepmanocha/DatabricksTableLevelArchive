# CareSource Archive — Code Reduction Report

**Date:** 2026-04-07  
**Analysis branch:** feat/delta_config_build_v2_reruns  
**Implementation branch:** feat/delta_config_build_v3_code_reduce (to be created from analysis branch)  
**Scope:** Structural analysis of all `src/`, `tests/unit/`, and `notebooks/` for duplication, simplification, and dead code  
**Baseline:** 7,662 total lines (2,289 src + 4,317 tests + 1,056 notebooks)

---

## Summary

| Area | Current Lines | Estimated Reducible | Reduction % |
|------|--------------|--------------------:|------------:|
| `src/` (8 files) | 2,289 | 150–230 | 7–10% |
| `tests/unit/` (8 files + conftest) | 4,317 | 850–1,350 | 20–31% |
| `notebooks/` (9 files) | 1,056 | 24–30 | 2–3% |
| **Total** | **7,662** | **1,024–1,610** | **13–21%** |

---

## 1. Source Code — `src/archiver.py` (683 lines)

### Duplication

| # | What | Where | How | Lines Saved | Risk |
|---|------|-------|-----|------------|------|
| A1 | `exclusion_clause if exclusion_clause else "1=1"` repeated in 4 methods | `_eligible_where_sql`, `_count_new_records`, `_watermark_where_sql`, `_dry_run_year` | Extract `_exc_sql(clause)` helper | 6–10 | Low |
| A2 | `SELECT COUNT(*) ... first()['count']` repeated 7+ times | `_count_source_year`, `_count_new_records`, `_count_archive_delta`, `_count_nulls`, `_dry_run_year` (×3) | Extract `_spark_count(sql) -> int` | 15–25 | Low |
| A3 | Year + watermark IS NOT NULL predicate in 6 forms | `_count_source_year`, `_eligible_where_sql`, `_count_new_records`, `_watermark_where_sql`, `_count_archive_delta`, `_delete_archived` | Extract `_year_wm_predicate(col, year, alias=None)` | 12–20 | Medium |
| A4 | Append-to-Delta INSERT duplicated between `_archive_year` and `_process_year_live` | `_archive_year` ~175–179, `_process_year_live` ~524–533 | Route both through `_archive_year` or extract `_insert_from_source(path, fq, where)` | 8–15 | Medium |
| A5 | try/except wrapping `spark.sql` → `ArchiveOperationError` duplicated | `_archive_year` ~185–195, `_delete_archived` ~259–269 | Extract `_run_sql_raise_op(sql, table_config, year, operation)` | 8–12 | Low |
| A6 | Ownership check + delete duplicated in two branches | `_process_year_live` ~484–491 and ~554–561 | Extract `_delete_with_ownership_check(...)` | 10–14 | Low |
| A7 | `audit.log_archive(ARCHIVED_AND_DELETED, ...)` call in two branches | `_process_year_live` ~492–499 and ~562–570 | Extract `_log_archived_and_deleted(...)` | 6–10 | Low |
| A8 | `build_archive_path(merged[...], merged[...], year)` repeated in 6+ methods | Throughout class | Extract `_archive_path(table_config, year)` instance method | 8–15 | Low |
| A9 | Exclusion clause building in `run()` duplicates `_exclusion_clause_for_config` | `run()` ~611–616 | Call `_exclusion_clause_for_config(merged)` | 4–6 | Low |
| A10 | `_dry_run_year` recomputes NULL-date count instead of calling `_count_nulls` | `_dry_run_year` ~338–342 | Call `self._count_nulls(table_config, year)` | 4–5 | Low |

### Simplification

| # | What | Where | How | Lines Saved | Risk |
|---|------|-------|-----|------------|------|
| A11 | `_process_year_live` is ~200+ lines mixing 6 phases | ~364–602 | Decompose into `_preflight_year_audit`, `_validate_folder_vs_audit`, `_archive_or_resume_delete`, `_finalize_archive_and_optional_delete` | 20–40 (net) | Medium |
| A12 | `_coerce_year_cell` mixes dict/getattr/row[key] with overlapping paths | ~23–35 | Normalize once: dict → `.get()`, else → `getattr()`, single coercion | 5–8 | Medium |

### Dead Code

| # | What | Where | Lines Saved | Risk |
|---|------|-------|------------|------|
| A13 | `_process_year_live` annotated `-> None` but returns `dict` | Signature ~371 | 0 (typing fix only) | Low |

**Subtotal archiver.py: ~70–120 lines reducible**

---

## 2. Source Code — `src/scanner.py` (611 lines)

### Duplication

| # | What | Where | How | Lines Saved | Risk |
|---|------|-------|-----|------------|------|
| S1 | Three config builders repeat same base fields | `generate_table_config`, `flag_unmatched`, `flag_ambiguous` (64–119) | Extract `_scanner_table_config(template, table_name, **overrides)` | 25–35 | Low |
| S2 | Spark row → string field extraction repeated across 5 methods | `scan_schema`, `validate_archive_path`, `_list_table_columns`, `get_table_size_gb`, `_validate_volume_is_external` | Extract `_row_str(row, *keys)` helper | 15–25 | Low–Med |
| S3 | Exact-match vs regex-match branches duplicate hit-count decision structure | `match_watermark_column` (43–60) | Helper `_resolve_pattern_hits(candidates, pattern)` | 8–15 | Low |
| S4 | Ambiguous-case text built twice with same join pattern | `flag_ambiguous` 105–117, `run_scanner` 544–545 | Derive `ambiguity_detail` once and store on config | 4–8 | Low |
| S5 | `ensure_scanner_log_table` raises same error message from two branches | 328–339 | Extract `_scanner_log_missing_error(fq)` | 4–6 | Low |
| S6 | Early-return summary dict duplicates main summary dict keys | `run_scanner` 443–451 vs 463–471 | Extract `_empty_scan_summary(scan_run_id)` | 8–12 | Low |
| S7 | Four `table_results.append({...})` literals repeat same key set | `run_scanner` excluded/matched/ambiguous/unmatched branches | Extract `_log_row(template, match_status, **fields)` | 25–45 | Medium |

### Simplification

| # | What | Where | How | Lines Saved | Risk |
|---|------|-------|-----|------------|------|
| S8 | `run_scanner` is a long orchestration function | 436–611 | Extract `_scan_single_table(...)` and `_annotate_merge_actions(...)` | 0–20 (readability gain) | Medium |
| S9 | `merge_staging_to_final` repeats `sql_quote(scan_run_id)` and CONCAT messages | 213–250 | Assign `scan_id_sql` once | 3–6 | Low |

### Dead Code

| # | What | Where | Lines Saved | Risk |
|---|------|-------|------------|------|
| S10 | `flag_unmatched` accepts `available_columns` but never uses it | Signature (82) | 1 + cleaner API | Low |
| S11 | `get_table_size_gb` defensive `d = {}` branch after `asDict()` | 135–137 | 2–3 | Low |

**Subtotal scanner.py: ~90–140 lines reducible**

---

## 3. Source Code — `src/audit.py` (323 lines)

### Duplication

| # | What | Where | How | Lines Saved | Risk |
|---|------|-------|-----|------------|------|
| U1 | `ensure_archive_audit_table` / `ensure_rehydration_audit_table` are near-identical | 120–142 | Extract `_ensure_table_exists(fq, kind)` | 8–10 | Low |
| U2 | `table_name = ... AND year = ...` filter repeated in 4 query methods | `check_resume_state`, `get_last_run_state`, `get_latest_status`, `check_concurrent` | Extract `_where_table_year(table, year)` | 4–8 | Low |
| U3 | `r.get("k") if isinstance(r, dict) else getattr(r, "k", None)` in 2 methods | `get_latest_status` (272–273), `check_concurrent` (303–304) | Extract `_spark_row_get(row, name)` | 4–6 | Low |

### Simplification

| # | What | Where | How | Lines Saved | Risk |
|---|------|-------|-----|------------|------|
| U4 | `_archive_table` / `_rehydration_table` are 1-line wrappers differing only by table name | 99–107 | Single `_audit_log_table(name)` method | 3–5 | Low |
| U5 | `_audit_table_fq` duplicates backtick FQN logic vs `utils.build_full_table_name` | 77–78 | Add `build_uc_quoted_table_name` to utils, use from both audit and archiver | 0–2 (consolidation) | Medium |

### Dead Code

None found — all columns, helpers, and imports are used.

**Subtotal audit.py: ~15–25 lines reducible**

---

## 4. Source Code — `src/config.py` (228 lines) & `src/utils.py` (236 lines)

### Duplication

| # | What | Where | How | Lines Saved | Risk |
|---|------|-------|-----|------------|------|
| C1 | "Real number, not bool" validation duplicated | `config.py` load_settings (150–153) and `_validate_template_row` (164–165) | Extract `_require_real_not_bool(value, field, table_id=None)` | 4–6 | Low |
| C2 | `get_distinct_years()` in utils duplicated by `archiver._calculate_eligible_years()` | `utils.py` 113–123 vs `archiver.py` 83–87 | Have archiver call `get_distinct_years` | 5–8 | Medium |

### Simplification

| # | What | Where | How | Lines Saved | Risk |
|---|------|-------|-----|------------|------|
| C3 | `validate_exclusion_conditions_json` raises same `ArchiveConfigError(field=..., table_id=...)` many times | `config.py` 63–98 | Factory `_exclusion_err(table_id)` | 10–18 | Low |
| C4 | `build_insert_values_sql` and `build_multi_insert_values_sql` share preamble | `utils.py` 196–236 | Extract `_insert_prefix(table, columns)` | 6–10 | Low |

### Dead Code

| # | What | Where | Lines Saved | Risk |
|---|------|-------|------------|------|
| C5 | `LOGGER` in utils.py defined but never referenced | `utils.py` line 19 | 2 | Low |
| C6 | `list_existing_archive_years()` has no production callers (tests only) | `utils.py` 97–110 | 14 (or wire a real caller) | Medium |
| C7 | `get_distinct_years()` has no production callers (tests only) | `utils.py` 113–123 | 11 (or have archiver use it) | Medium |
| C8 | `logger` in `conditions.py` defined but unused | `conditions.py` | 2 | Low |

**Subtotal config.py + utils.py: ~30–50 lines reducible**

---

## 5. Cross-Module Patterns

### Already Centralized (no action needed)

| Pattern | Status |
|---------|--------|
| SQL quoting helpers (`sql_quote`, `sql_str_or_null`, etc.) | Defined in utils.py, used by all modules |
| VALUES INSERT builders | audit uses `build_insert_values_sql`, scanner uses `build_multi_insert_values_sql` |

### Needs Consolidation

| # | Pattern | Files Affected | Instances | Lines Saved | Risk |
|---|---------|---------------|-----------|------------|------|
| X1 | Delta `INSERT ... SELECT` hand-built | `archiver.py` (2 places) | 2 | 8–15 | Low |
| X2 | Exception-wrapping around `spark.sql` | `archiver.py` (3), `audit.py` (2), `rehydrator.py` (1) | 6 | 15–25 | Medium |
| X3 | Backtick FQN construction (`\`cat\`.\`sch\`.\`name\``) | `audit._audit_table_fq`, `archiver._audit_archive_fq` | 2 functions | 5–12 | Medium |
| X4 | Unquoted FQN f-string instead of `build_full_table_name` | `rehydrator.py` (3 spots), `scanner.py` (1 volume path) | 4 | 3–6 | Low |
| X5 | Dead module-level loggers | `config.py`, `conditions.py` | 2 | 4 | Low |
| X6 | Notebook `sys.path` bootstrap (identical 6-line block) | 6 notebooks | 6 copies × 6 lines | 24–30 | Low |

---

## 6. Test Code Reduction

### `test_archiver.py` (1,656 lines) — Largest Savings

| # | What | How | Lines Saved | Risk |
|---|------|-----|------------|------|
| T1 | Spark stub for `YEAR(current_date())` repeated ~32 times | Extract `_spark_mock_current_year(mock, year=2026)` and `_spark_mock_distinct_years(mock, years)` | 150–250 | Medium |
| T2 | `spark.conf = MagicMock()` + `patch("archive_folder_exists")` + `eng.run(...)` boilerplate | Fixture `archiver_run_context(eng, spark, folder_exists=...)` | 60–120 | Low |
| T3 | `_claims_count_sql_handler` only partially shared; wm/uc tests still duplicate branches | Extend shared helpers for wm test scenarios | 80–150 | Medium |
| T4 | `test_arc05_verify_count_match/mismatch` share same setup | `@pytest.mark.parametrize` on count + expect_raises | 6–10 | Low |

### `test_scanner.py` (793 lines)

| # | What | How | Lines Saved | Risk |
|---|------|-----|------------|------|
| T5 | `TestMatchDateColumn` repeats same unpacking pattern | Single `@pytest.mark.parametrize` test | 45–60 | Low |
| T6 | `sql_side_effect` for `run_scanner` near-identical in 3 test classes | Shared `build_run_scanner_sql_side_effect(...)` | 60–90 | Medium |
| T7 | Scanner template dicts with small field differences | Module helper `_scanner_template(**overrides)` | 80–110 | Low |
| T8 | `TestWriteScannerLog` large inline `table_results` dicts | Factory `scanner_log_table_result(**kwargs)` | 40–55 | Low |

### `test_audit.py` (525 lines)

| # | What | How | Lines Saved | Risk |
|---|------|-----|------------|------|
| T9 | `ensure_*_table` success/failure are parallel pairs | `@pytest.mark.parametrize` over method name | 25–35 | Low |
| T10 | `mock_spark` fixture duplicated from conftest | Remove local copy | 4 | Low |
| T11 | Every test repeats `ctx = _make_ctx(); log = AuditLogger(ctx, mock_spark)` | Fixture returning `(ctx, log)` | 40–60 | Low |
| T12 | Three `check_concurrent` stale-threshold tests share setup | Parametrize `(delta_hours, threshold, expected)` | 30–45 | Medium |

### `test_conditions.py` (191 lines)

| # | What | How | Lines Saved | Risk |
|---|------|-----|------------|------|
| T13 | Seven nearly identical condition dicts in `TestSameTableOperators` | `@pytest.mark.parametrize` with `(operator, column, value, alias, expected_sql)` | 55–75 | Low |
| T14 | Three `TestCustomSqlPlaceholders` tests differ by placeholder | Parametrize `(value_template, cat, sch, alias, expected)` | 18–28 | Low |

### `test_utils.py` (374 lines)

| # | What | How | Lines Saved | Risk |
|---|------|-----|------------|------|
| T15 | `TestSqlHelpers` is many one-liner assertions | `@pytest.mark.parametrize("fn,arg,expected", [...])` | 35–50 | Low |
| T16 | `TestGetJobContext` repeats dbutils construction | Fixture `_make_dbutils_with_tags(tags)` | 25–35 | Low |

### `test_rehydrator.py` (206 lines)

| # | What | How | Lines Saved | Risk |
|---|------|-----|------------|------|
| T17 | Every test repeats `ctx, audit, spark, eng` construction | `@pytest.fixture` yielding `(eng, spark, audit)` | 55–75 | Low |
| T18 | `_ctx()` overlaps `conftest.mock_run_context` | Use conftest factory | 8–15 | Low |

### `test_config.py` (440 lines)

| # | What | How | Lines Saved | Risk |
|---|------|-----|------------|------|
| T19 | `from src import config` repeated inside every test method | Move to module-level import | 20–30 | Low |
| T20 | `_mock_spark_with_rows` mirrors patterns in other test files | Move to conftest as shared factory | 10–20 | Medium |

### `test_exceptions.py` (132 lines)

| # | What | How | Lines Saved | Risk |
|---|------|-----|------------|------|
| T21 | `test_catch_base_catches_all` repeats 3 `pytest.raises` blocks | Parametrize over `exc_class` | 6–10 | Low |
| T22 | `test_plain_string_message` identical across 3 classes | Single parametrized test | 12–18 | Low |

---

## 7. Development Rules Compliance Check

| Rule | Status | Notes |
|------|--------|-------|
| **#8 — No code duplication** | Partial | INSERT SQL uses shared builders (good). Archiver has duplication in year/watermark predicates, count queries, and archive paths. Scanner has config-builder duplication. |
| **#13 — Remove dead parameters** | Violation | `scanner.flag_unmatched` accepts `available_columns` but never uses it (S10). |
| **#23 — DRY SQL statements** | Mostly compliant | INSERT paths use utils builders. SELECT/DESCRIBE paths in audit.py still hand-build SQL with repeated fragments. Delta `INSERT SELECT` in archiver is hand-built (2 copies). |
| **#7 — No comments during dev** | Compliant | No comment-related reduction needed. |
| **#3 — Code quality / short functions** | Partial | `_process_year_live` (200+ lines) and `run_scanner` (175+ lines) violate "short functions" principle. |
| **#6 — Modular and independently testable** | Compliant | Each module is independently testable. |
| **#22 — No DDL in core code** | Compliant | No DDL in src/. |
| **#11 — Use Python logging, not print** | Partial | Dead loggers in `config.py` and `conditions.py`. Logger naming inconsistent (`logger` vs `LOGGER`). |

---

## 8. Prioritized Reduction Plan

### Phase 1 — Low-Risk, High-Impact (est. ~400–650 lines)

| ID | Target | Action | Lines |
|----|--------|--------|------:|
| T13+T14 | test_conditions.py | Parametrize operator and placeholder tests | 73–103 |
| T15+T16 | test_utils.py | Parametrize SQL helpers and job context tests | 60–85 |
| T17+T18 | test_rehydrator.py | Extract fixture, use conftest factory | 63–90 |
| T5+T7 | test_scanner.py | Parametrize match tests, extract template factory | 125–170 |
| T9+T10+T11 | test_audit.py | Parametrize ensure_*, extract logger fixture | 69–99 |
| S1 | scanner.py | Unified config builder | 25–35 |
| A1+A2 | archiver.py | `_exc_sql` + `_spark_count` helpers | 21–35 |
| C5+C8 | utils.py, conditions.py | Remove dead loggers | 4 |
| S10 | scanner.py | Remove dead `available_columns` parameter | 1 |

### Phase 2 — Medium-Risk, High-Impact (est. ~350–550 lines)

| ID | Target | Action | Lines |
|----|--------|--------|------:|
| T1+T2+T3 | test_archiver.py | Spark mock helpers + run context fixture | 290–520 |
| T6+T8 | test_scanner.py | Shared sql_side_effect + scanner_log factory | 100–145 |
| A3+A4 | archiver.py | Year/watermark predicate builder, unify append INSERT | 20–35 |
| A11 | archiver.py | Decompose `_process_year_live` | 20–40 |
| S7+S8 | scanner.py | Log-row builder, split `run_scanner` | 25–65 |
| X2 | Cross-module | Shared SQL error wrapper in utils | 15–25 |
| X6 | Notebooks | Shared `sys.path` bootstrap | 24–30 |

### Phase 3 — Consolidation (est. ~50–80 lines)

| ID | Target | Action | Lines |
|----|--------|--------|------:|
| C2+C6+C7 | utils.py + archiver.py | Wire or remove orphaned year-listing helpers | 16–30 |
| X3 | utils.py + audit.py + archiver.py | Shared backtick FQN builder | 5–12 |
| U1+U2 | audit.py | Merge ensure_* methods, extract table/year WHERE | 12–18 |
| C3+C4 | config.py + utils.py | Error factory, INSERT prefix extraction | 16–28 |

---

## 9. Risk Mitigation

- **All phases require passing unit tests before and after.** Run `uv run pytest tests/unit/ -v` as the gate.
- **Phase 1** items are pure consolidation — behavior is unchanged, only structure moves.
- **Phase 2** items touch SQL generation paths — verify with `pytest -k "sql"` and spot-check generated SQL strings.
- **Phase 3** items cross module boundaries — verify with full test suite + manual review of generated SQL.
- **Notebook changes (X6)** cannot be unit-tested — validate via `databricks bundle validate` and a dry-run deploy.

---

*Generated by automated structural analysis on 2026-04-07. No code changes were made during this review.*
