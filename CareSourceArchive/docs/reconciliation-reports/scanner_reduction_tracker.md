# Iteration 3: Scanner Code Reduction Tracker

**Branch:** `feat/delta_config_build_v3_code_reduce`
**Predecessor:** Iteration 2 (saved 220 lines)
**Plan:** `.cursor/plans/scanner_code_reduction_bc914747.plan.md`

---

## Baseline (pre-Iteration 3)

| File | Lines |
|------|------:|
| src/scanner.py | 600 |
| src/utils.py | 234 |
| src/archiver.py | 690 |
| src/audit.py | 323 |
| src/config.py | 228 |
| src/conditions.py | 61 |
| src/exceptions.py | 47 |
| src/rehydrator.py | 96 |
| tests/unit/test_scanner.py | 744 |
| tests/unit/test_utils.py | 377 |
| tests/unit/test_archiver.py | 1656 |
| tests/unit/test_audit.py | 449 |
| tests/conftest.py | 39 |
| **Total** | **6,425** |
| **Tests** | **228 passed, 1 pre-existing failure** |

---

## Item Status

### Scanner-Internal (Batches 1-2)

| ID | Item | Batch | Status | Lines Saved |
|----|------|-------|--------|------------:|
| S5 | Error message dedup in `ensure_scanner_log_table` | 1 | Done | -4 |
| S6 | Extract `_empty_scan_summary` | 1 | Done | -2 |
| S9 | `scan_id_sql` assignment in `merge_staging_to_final` | 1 | Done | -0 |
| S11 | Remove defensive `d = {}` in `get_table_size_gb` | 1 | Done | -2 |
| S7 | Extract `_make_log_entry` factory (4 dict literals) | 2 | Done | -28 |
| S4 | Ambiguity detail dedup | 2 | Done | -3 |

### Shared Utility Extraction (Batch 3)

| ID | Item | Batch | Status | Lines Saved |
|----|------|-------|--------|------------:|
| S2+U3 | `row_value` + `row_to_dict` to utils.py | 3 | Done | +14 (new) |
| NEW | `collect_column` to utils.py | 3 | Done | +5 (new) |
| U1+S5 | `ensure_table_exists` to utils.py | 3 | Done | +8 (new) |
| C6 | Remove dead `list_existing_archive_years` + cascade | 3 | Done | -24 |
| C7 | Remove dead `get_distinct_years` | 3 | Done | -11 |

### Scanner Readability (Batch 4, optional)

| ID | Item | Batch | Status | Lines Saved |
|----|------|-------|--------|------------:|
| S3 | Extract `_resolve_pattern_hits` | 4 | Skipped | 0 |
| S8 | Extract `_scan_single_table` | 4 | Skipped | 0 |

### Propagate Utils (Batch 5)

| ID | Item | Batch | Status | Lines Saved |
|----|------|-------|--------|------------:|
| U1 | audit.py: replace ensure_* with `ensure_table_exists` | 5 | Done | 0 |
| U3 | audit.py: replace row access with `row_value` | 5 | Done | -2 |
| NEW | config.py: replace `r.asDict()` with `row_to_dict` | 5 | Done | 0 |
| NEW | config.py: remove dead `LOGGER` + `import logging` | 5 | Done | -3 |
| A12 | archiver.py: simplify `_coerce_year_cell` (no row_value, direct) | 5 | Done | -6 |
| A13 | archiver.py: fix `_process_year_live` return type `-> None` to `-> dict` | 5 | Done | 0 |

### Deferred from Iteration 2

| ID | Item | Batch | Status |
|----|------|-------|--------|
| — | Fix stale `flag_unmatched` signature in docs/features.md | 1 | Done |

---

## Dead Code Inventory

### Final Verification (post-Batch 5)

All `src/` files re-scanned for orphaned functions, unused imports, and unreferenced private helpers. **No dead code found.** Two public API functions (`get_active_tables`, `check_resume_state`) are only called from tests but are documented public API (features.md, design.md) — retained intentionally.

### Dead Functions (no production callers, all removed)

| ID | Function | File | Lines | Cascade |
|----|----------|------|------:|---------|
| C6 | `list_existing_archive_years()` | utils.py:95-108 | 14 | `_ls_entry_name` (8), `_YEAR_DIR` (1), `import re` (1) = 24 total |
| C7 | `get_distinct_years()` | utils.py:111-121 | 11 | None |
| — | `_ls_entry_name()` | utils.py:85-92 | 8 | Orphaned by C6 |

### Dead Variables/Imports

| ID | What | File | Lines |
|----|------|------|------:|
| NEW | `LOGGER` + `import logging` | config.py:2,9 | 2 |
| C6 | `_YEAR_DIR = re.compile(...)` | utils.py:8 | 1 |
| C6 | `import re` | utils.py:2 | 1 |

### Typing Issues

| ID | What | File |
|----|------|------|
| A13 | `_process_year_live -> None` returns `dict` | archiver.py:371 |

---

## Batch Progress

| Batch | Description | Status | Lines Saved (src) | Lines Saved (tests) |
|-------|-------------|--------|------------------:|--------------------:|
| 1 | Scanner quick wins (S5+S6+S9+S11) | **Done** | -10 | 0 |
| 2 | Scanner log factory (S7+S4) | **Done** | -31 | 0 |
| 3 | Utils extraction + dead code (S2+U3+U1+C6+C7+NEW) | **Done** | -38 (scanner -33, utils -5) | +20 (net) |
| 4 | Scanner readability (S3+S8, optional) | **Skipped** | 0 | 0 |
| 5 | Propagate utils (U1+U3+A12+A13+LOGGER) | **Done** | -9 (audit 0, config -3, archiver -6) | 0 |
| **Total** | | | **-88** | **+16** (+20 utils, -4 scanner) |

### Final Line Counts

| File | Baseline | Final | Delta |
|------|----------|-------|------:|
| src/scanner.py | 600 | 526 | -74 |
| src/utils.py | 234 | 229 | -5 |
| src/archiver.py | 690 | 684 | -6 |
| src/audit.py | 323 | 323 | 0 |
| src/config.py | 228 | 225 | -3 |
| src/conditions.py | 61 | 61 | 0 |
| src/exceptions.py | 47 | 47 | 0 |
| src/rehydrator.py | 96 | 96 | 0 |
| tests/unit/test_scanner.py | 744 | 740 | -4 |
| tests/unit/test_utils.py | 377 | 397 | +20 |
| tests/unit/test_archiver.py | 1656 | 1656 | 0 |
| tests/unit/test_audit.py | 449 | 449 | 0 |
| tests/conftest.py | 39 | 39 | 0 |
| **Grand Total** | **6,425** | **6,353** | **-72** |
| **Tests** | 228 passed, 1 failure | 237 passed, 1 failure | +9 new tests |

---

## Harsh Review Findings

| Batch | Severity | File | Finding | Resolution |
|-------|----------|------|---------|------------|
| 1 | MEDIUM | scanner.py | S11: non-dict asDict() edge case after removing isinstance guard | Accepted: Spark Row.asDict() always returns dict |
| 2 | MEDIUM | scanner.py | S7: open-ended **overrides in _make_log_entry could allow typos | Accepted: private function with 4 call sites, low risk |
| 3 | HIGH | scanner.py | ensure_scanner_log_table: lost exception chaining on re-raise | Fixed: added `from e` |
| 3 | HIGH | scanner.py | ensure_scanner_log_table: SQL changed from information_schema to DESCRIBE TABLE | Accepted: intentional per plan, tested |
| 3 | MEDIUM | utils.py | row_value: asDict-first doesn't try row[field] fallback like old code | Accepted: real Spark rows always have asDict |
| 5 | HIGH | config.py | row_to_dict returns {} for non-Row → possible KeyError downstream | Accepted: only affects pathological mocks, not prod |
| 5 | MEDIUM | audit.py | ensure_table_exists messaging conflates all failures | Accepted: same behavior as original code |
