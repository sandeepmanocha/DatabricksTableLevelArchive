# CareSource Archive — Reconciliation Report

**Date:** 2026-04-02  
**Branch:** feat/delta_config_build  
**Scope:** Full coverage review against requirements.md, requirements-summary.md, design.md, features.md, and build plans (caresource_modular_build + multi-archive_year_behavior)

---

## 1. Source Modules (`src/*.py`)

### `src/utils.py`
| Issue | Detail |
|-------|--------|
| **F1.10 — configure_logging signature gap** | Spec says `configure_logging(settings, archive_run_id)` — implementation takes only `archive_run_id`. No `settings` param. |
| **F1.10 — log format missing `{table}` field** | Formatter uses `[%(name)s]` (logger name), not a per-event `{table}` field. design.md Section 12 requires `[archive_run_id][table]`. |
| **F1.11 — silent `None` on missing secrets** | After finding the scope, individual missing keys silently return `None`. Spec says missing keys return `None` (optional credentials) — by design, but worth noting. |

### `src/exceptions.py`
| Issue | Detail |
|-------|--------|
| **ERR-04 — structured messages not guaranteed** | When callers pass an explicit `msg=` string, the structured fields are bypassed. Callers must use kwargs for structured messages to work. |

### `src/archiver.py`
| Issue | Detail |
|-------|--------|
| **F6.8 — metadata `mode` vocabulary mismatch** | `_write_metadata` writes `"create"`, `"append"`, `"replace"`, `"resume_delete"`, `"*_deleted"`. Spec requires exactly `"write"` (initial) and `"append"` (incremental). |
| **F6.13 — custom_sql not specifically wrapped** | No dedicated try/except around custom_sql-only execution — surfaces via the general `spark.sql` try/except but without the SQL text in the message specifically for custom conditions. |
| **DRY-03 — per-condition counts not dynamic columns** | Dry-run results are serialized as JSON in `conditions_applied` audit column, not as dynamic physical columns named per condition. Spec says "dry-run report columns are dynamic." |

### `src/scanner.py`
| Issue | Detail |
|-------|--------|
| **F11.13 / SCN-15 — `workspace_id` and `scanned_by` missing** | The `scanner_log` DDL and INSERT in `write_scanner_log()` do not include `workspace_id` or `scanned_by` columns. design.md Section 9 and SCN-15 require both. |

### `src/rehydrator.py`
| Issue | Detail |
|-------|--------|
| **RHY-06 — per-year skip, not upfront fail-fast** | Spec says "verify archive folder exists before creating external table." Code checks per-year in the loop and skips missing years with a warning. Not a hard upfront failure. Minor behavioral delta. |

---

## 2. Notebooks & DABs Jobs

### `notebooks/generate_parameters.py`
| Issue | Detail |
|-------|--------|
| **F8.1 — wrong exception type for missing `config_table`** | Empty `config_table` raises `ValueError`, not `ArchiveConfigError`. ERR-02 and F8.1 require `ArchiveConfigError` from config failures. |
| **F8.5 — concurrency not read from `global_settings` at runtime** | ForEach concurrency uses `${var.archive_foreach_concurrency}` (a bundle variable), not a runtime read from `global_settings.concurrency`. Must be manually aligned — not automatic. |

### `notebooks/setup_config_tables.py`
| Issue | Detail |
|-------|--------|
| **F15.1 — catalog is NOT created, only verified** | Spec says "creates catalog and schema if they don't exist." Code validates the catalog exists and errors if not. Only schema gets `CREATE IF NOT EXISTS`. |
| **F15.4 — schema not actually validated** | Post-creation validation only does `SELECT 1` + `COUNT`. Does not confirm schema matches expected columns/types. |

### `notebooks/manual/validate_config.py`
| Issue | Detail |
|-------|--------|
| **Raises `ValueError` instead of `ArchiveConfigError`** | Final failure raises `ValueError`. Should use `ArchiveConfigError` for consistency with the error hierarchy. |

### `resources/setup_job.yml`
| Issue | Detail |
|-------|--------|
| **JOB-10 / F15.5 — `seed_data` not a runtime job parameter** | `seed_data` is hardcoded as `"false"` in `base_parameters`. Operators cannot toggle seeding without editing YAML. Spec requires it as a job parameter. |

### `databricks.yml`
| Issue | Detail |
|-------|--------|
| **Non-serverless YAMLs commented out** | Default `bundle deploy` uses only `*_serverless.yml` files. `archive_job.yml`, `setup_job.yml`, `scanner_job.yml` are commented out. This is an intentional compute choice but means the classic-cluster versions are not active. |

---

## 3. Test Coverage

### Missing / Not Yet Written
| Gap | Detail |
|-----|--------|
| **No integration tests** | `tests/integration/` directory is empty. Plan required `test_archive_flow.py`, `test_rehydrate_flow.py`, `test_audit_flow.py`. Step 7 is **not complete**. |
| **No interactive scripts** | `tests/interactive/` is empty. Plan required `try_config_load.py`, `try_archive_single_table.py`, `try_rehydrate.py`, `try_scanner.py`. |

### Test-to-Requirement Gaps (Unit Tests)
| ID | Gap |
|----|-----|
| **EDGE-02** | No test for schema evolution. |
| **DRY-03, DRY-04** | No tests — only DRY-01, DRY-02, DRY-05 covered. |
| **CONC-04** | Delta `ConcurrentAppendException` as safety net — no test. |
| **ARC-06** | No test that `run()` *omits* the delete step when `delete_after_archive=false` while archiving succeeds. |
| **write_scanner_log content** | No unit test asserting `ambiguity_detail`, `all_matched_columns`, `merge_action` in the INSERT SQL for `write_scanner_log`. |
| **SCN-13 — `tables_ambiguous > 0`** | Only tests `tables_ambiguous == 0`. No test with an ambiguous table in the summary. |
| **AUD-06 / AUD-07 values** | DDL has the columns; INSERT tests do not assert that `archive_run_id`, `workspace_id`, `job_id`, etc., are bound with correct values from context. |
| **RHY-05 — schema creation in rehydration** | `create_schema_if_not_exists` is called but not asserted in any test. `test_rhy05` covers the wrong thing (missing archive folder). |
| **LOG-02 — `{table}` in log format** | No test for the `{table}` field in log messages (because it is not in the implementation either). |
| **Merge — all 5 SCN-11 behavioral cases** | Tests verify SQL shape only. No test runs all five MERGE scenarios (insert / scanner-update / preserve-manual / mark-dropped / preserve-manual-dropped) against staged data. |

### Test Count Verification
All unit test counts match the progress report exactly:

| File | Claimed | Verified |
|------|---------|----------|
| `test_exceptions.py` | 14 | 14 ✓ |
| `test_utils.py` | 20 | 20 ✓ |
| `test_audit.py` | 23 | 23 ✓ |
| `test_config.py` | 19 | 19 ✓ |
| `test_conditions.py` | 17 | 17 ✓ |
| `test_rehydrator.py` | 7 | 7 ✓ |
| `test_scanner.py` | 23 | 23 ✓ |
| `test_archiver.py` | 22 | 22 ✓ |

---

## 4. Infrastructure & Documentation

### `delta-config-ddl.md`
| Issue | Detail |
|-------|--------|
| **`archive_audit_log`, `rehydration_audit_log`, `scanner_log` DDL absent** | File only contains config-table DDL. Audit and scanner log DDL lives only in source code (`src/audit.py`, `src/scanner.py`). Likely intentional, but design.md Section 10 references "See `docs/delta-config-ddl.sql`" which is wrong on both the filename and the expectation. |
| **`design.md` references wrong filename** | design.md Section 10 says "See `docs/delta-config-ddl.sql`" — actual file is `delta-config-ddl.md`. |

### `tracker.md`
| Issue | Detail |
|-------|--------|
| **Several items still "Not Started" or "In Progress"** | Config Delta tables DDL/seed: Not Started. F10 integration tests: Not Started. Documentation Progress Report / Code Dependency Graph: In Progress. |

### `code-dependency-graph.md`
| Issue | Detail |
|-------|--------|
| **Does not list `scanner_job.yml` or `*_serverless.yml` variants** | Bundle now includes scanner job resources and serverless variants — dependency graph not updated. |

### `development-rules.md` vs `tracker.md`
| Issue | Detail |
|-------|--------|
| **Integration test filenames inconsistent** | `development-rules.md` says `test_archive_flow.py`, `test_rehydrate_flow.py`, `test_audit_flow.py`; `tracker.md` says `test_archiver.py`, `test_rehydrator.py`, `test_audit.py`. Neither matches disk (both directories empty). |

### `requirements.md` vs actual tooling
| Issue | Detail |
|-------|--------|
| **NFR-08 still says `pip install -e .`** | All operational docs (`prerequisites.md`, `development-rules.md`, `tracker.md`) standardize on **uv**. `requirements.md` NFR-08 has not been updated to reflect this. |

---

## 5. Summary: Complete vs Needs Attention

### Fully Complete (no gaps found)
- All core `src/` modules implemented and passing unit tests (145+ tests)
- Exception hierarchy (F13 / ERR-01–04)
- Audit logging with all 8 statuses (F2 / AUD-*)
- Config loading with all validations (F3 / CFG-*)
- Condition builder with all 7 operators (F4 / EXC-*)
- Dry-run mode (F5 / DRY-01, 02, 05)
- Rehydration engine core (F7 / RHY-01–04, 06)
- Scanner core (F11 / SCN-01–14)
- All notebooks exist and are wired correctly
- DABs bundle is deployable (serverless-first)
- Multi-archive year behavior (D31 / ARC-12 / ARC-13) — implemented and documented
- Rehydration job defined in `archive_job.yml` alongside archive job (JOB-03 / JOB-04)
- Scanner job defined in `scanner_job.yml` / `scanner_job_serverless.yml`
- Environment targets `dev`, `qa`, `stage`, `prod` match requirements (NFR-09)

### Gaps Grouped by Effort

**Small fixes (< 1 hour each):**
- `generate_parameters.py`: change `ValueError` → `ArchiveConfigError` for missing `config_table`
- `validate_config.py`: change `ValueError` → `ArchiveConfigError`
- `setup_job.yml`: add `seed_data` as a proper runtime job parameter
- `design.md`: fix `delta-config-ddl.sql` → `delta-config-ddl.md`
- `archiver.py` F6.8: rename metadata `mode` values from `"create"` → `"write"` to match spec
- `setup_config_tables.py`: fix misleading "Create catalog" comment

**Medium fixes (a few hours each):**
- `scanner.py` / scanner_log DDL: add `workspace_id` and `scanned_by` columns (SCN-15)
- `utils.py` F1.10: add `{table}` field to log format (or decide to update spec if intentional)
- Write missing unit tests: `EDGE-02`, `DRY-03/04`, `CONC-04`, `ARC-06`, `write_scanner_log` content, `SCN-13 ambiguous > 0`, `RHY-05` schema creation, `AUD-06/07` value binding
- `tracker.md` and `code-dependency-graph.md`: update to reflect actual completion state
- `requirements.md` NFR-08: update to reference uv instead of `pip install -e .`

**Larger work (Step 7 — not started):**
- Integration tests (`tests/integration/`) — `test_archive_flow.py`, `test_rehydrate_flow.py`, `test_audit_flow.py`
- Interactive scripts (`tests/interactive/`) — `try_config_load.py`, `try_archive_single_table.py`, `try_rehydrate.py`, `try_scanner.py`
- Align integration test filenames across `development-rules.md` and `tracker.md`

---

*Generated by automated coverage review on 2026-04-02. No code changes were made during this review.*
