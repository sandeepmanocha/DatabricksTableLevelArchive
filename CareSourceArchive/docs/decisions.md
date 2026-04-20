# CareSource Delta Table Archive — Decisions Log

Single source of truth for all architectural, design, and implementation decisions.

**Sources:** `docs/requirements.md` (D1–D35), `docs/tracker.md` (iteration reviews), code review sessions.

---

## Architecture & Configuration

| # | Date | Decision | Rationale |
|---|------|----------|-----------|
| D1 | 2026-03-25 | Three-tier Delta table config: global_settings + schema_templates + table_configs | Stored in Unity Catalog, scalable to 10,000+ tables, auditable via Delta time travel + audit columns (modified_by, modified_at, change_reason). Each environment has its own tables — no overlay logic |
| D6 | 2026-03-25 | Modular package + thin wrappers | Testable, parallel-developable, no code duplication |
| D11 | 2026-03-26 | Selective OOP: `RunContext` dataclass + `AuditLogger`, `ArchiveEngine`, `RehydrationEngine` classes. Pure functions for config, conditions, scanner | Eliminates parameter threading for shared state while keeping stateless modules simple |
| D17 | 2026-03-26 | `pyproject.toml` + workspace file sync via DABs | Enables local `pytest`, clean notebook imports, version tracking. `src/` deployed alongside notebooks via DABs workspace sync — no wheel build needed |
| D18 | 2026-03-26 | DABs targets + per-environment config Delta tables | Each environment has its own config tables with the correct values. The `config_table` job parameter determines the environment — no overlay logic needed |
| D33 | 2026-04-06 | Delta External tables for archive storage | Archive year folders are Delta External tables (LOCATION points to the External Volume path). Not managed Delta tables. Ensures: (a) data persists if catalog entry is dropped; (b) rehydration is zero-copy; (c) multiple environments can read the same folder independently. **Partially superseded by D45** — rehydration uses views instead of external tables due to UC Volume governance constraint |
| D35 | 2026-04-06 | Unity Catalog External Volumes as the only supported archive storage mechanism | Direct S3/ABFS paths are rejected. External Volumes provide UC-governed access control, path stability, and consistent auditing. Databricks best practice for managed external storage |
| D36 | 2026-03-31 | Replace all JSON config files with Delta tables | Deleted `config/` directory. Eliminated environment overlay concept — each workspace has its own config tables. Scanner uses staging table + MERGE instead of intermediate JSON files. Simplifies deployment and eliminates config drift between environments |
| D37 | 2026-04-12 | Remove secret scope pipeline (`load_secrets`, `_KNOWN_SECRET_KEYS`, `secrets` field from `RunContext`) | `load_secrets()` loaded `warehouse_id`, `client_id`, `client_secret` from Databricks secret scopes, but none were ever used downstream. `spark.sql()` runs on attached compute — no warehouse ID needed. SP auth via DABs `run_as`. Entire secrets pipeline was dead code |

## Archiving Logic

| # | Date | Decision | Rationale |
|---|------|----------|-----------|
| D2 | 2026-03-25 | OR exclusion logic | Healthcare safety — better to keep a record than lose one |
| D3 | 2026-03-25 | Rolling window + year folders | Automated cutoff + clean self-contained archives per year |
| D4 | 2026-03-25 | `same_table` + `custom_sql` scopes only | Start simple, add structured `cross_table` later when patterns emerge |
| D14 | 2026-03-26 | NULL date = required value error: always exclude, log as ERROR, count in audit | Date column is required for archiving. NULL means the record can't be dated — it stays in source and is flagged as a data quality error. No configuration needed, no `year_0000/` folder. Simple and safe |
| D31 | 2026-03-31 | Universal watermark-driven append — same logic for both `delete_after_archive` modes | When a year folder already exists: always check the last watermark from audit and APPEND records above it. Works because: (1) `delete_after_archive=true` source has only leftovers; (2) `delete_after_archive=false` watermark still correctly identifies what is new. No REPLACE mode — archive is append-only. Sub-folders rejected — break one-Delta-table-per-year model |
| D34 | 2026-04-06 | Fail rather than override on bad signal | No in-process force flags for overwriting archives, forcing scanner resets, or bypassing verification. Unexpected state → fail with clear error → operator investigates → manual corrective action → re-run. Prevents automated processes from silently correcting data integrity issues the wrong way |

## Safety & Observability

| # | Date | Decision | Rationale |
|---|------|----------|-----------|
| D5 | 2026-03-25 | Dry-run defaults to true | Safety first — see blast radius before executing |
| D7 | 2026-03-25 | Rehydration is runtime-only | Supports multiple concurrent rehydrations to different targets |
| D8 | 2026-03-26 | Enumerated audit statuses: Archive = `STARTED`, `DRY_RUN`, `ARCHIVED`, `ARCHIVED_AND_DELETED`, `FAILED`, `SKIPPED`, `SKIPPED_CONCURRENT`, `NO_DATA`. Rehydration = `COMPLETED`, `PARTIAL_COMPLETED`, `FAILED` | Clear, queryable values. `STARTED` enables concurrency detection. `ARCHIVED` vs `ARCHIVED_AND_DELETED` enables resumability. `SKIPPED_CONCURRENT` distinguishes intentional skips from conflict skips |
| D9 | 2026-03-26 | Store Databricks job context (workspace_id, job_id, job_run_id, task_run_id) in audit tables | Enables joining audit data with `system.lakeflow.job_run_timeline` for operational monitoring |
| D10 | 2026-03-26 | Write `_archive_metadata.json` per year folder | Human-readable provenance for disaster recovery — no code reads it at runtime |
| D13 | 2026-03-26 | Custom exception hierarchy: `ArchiveConfigError`, `ArchiveOperationError`, `ArchiveVerificationError` | Config errors stop the job. Operation errors fail one table, others continue. Verification errors prevent data loss. 3 classes, minimal code, big diagnostic payoff |
| D15 | 2026-03-26 | Audit-table-based optimistic lock for concurrency | No external infrastructure. `STARTED` row + `archive_run_id` check prevents two jobs from archiving the same table+year. Delta ACID is the safety net for writes |
| D16 | 2026-03-26 | Python `logging` module with `[run_id][table]` structured format | Standard library, team already knows it, Databricks captures driver logs. Audit table handles structured outcomes — logging handles diagnostic detail |
| D20 | 2026-03-26 | Rehydrate-as-rollback + Delta time travel as safety net | No separate rollback code path. Rehydration already restores data. Delta time travel provides 30-day emergency backstop. Recommended: `delete_after_archive: false` for initial cycles |

## Scanner & Onboarding

| # | Date | Decision | Rationale |
|---|------|----------|-----------|
| D24 | 2026-03-27 | Two-stage scan: staging Delta table → MERGE → table_configs | Staging table is the scanner's view; MERGE uses `modified_by` to detect manual edits without field-by-field diff. Delta time travel on staging provides schema evolution history |
| D25 | 2026-03-27 | `min_table_size_gb` required in schema templates, `0` disables | Explicit intent — no silent defaults. Size filtering is a conscious choice per schema |
| D26 | 2026-03-27 | `DESCRIBE DETAIL` for table size, unknown size defaults to inactive | Reliable for Delta (managed and external). Conservative default — unknown means skip, not include |
| D27 | 2026-03-30 | **REMOVED** — Scanner force flag removed | Forceful overwrite of table_configs bypasses manual-edit detection and creates risk of losing operator-curated overrides. Safer to fail than to provide an in-process override |
| D28 | 2026-03-30 | Validate `archive_base_path` against UC external locations at onboarding time | Fail-fast: catch missing storage infrastructure before scanning tables. External locations are the UC-governed way to access cloud storage |
| D29 | 2026-03-30 | Ambiguous date column match → flag as inactive, don't guess | For data archival, silently picking the wrong date column could archive the wrong records. Flagging as `is_active: false` with ambiguity detail forces the operator to make a conscious choice |
| D30 | 2026-03-30 | Scanner log as a separate Delta table, not part of archive audit | Scanner is an onboarding tool with different cardinality (per-table-per-scan) than archive audit (per-table-per-year-per-run). Separate tables keep queries clean |
| D32 | 2026-04-02 | `schema_id` as schema template PK with targeted scanning | Human-readable `schema_id` replaces composite `(source_catalog, source_schema)` as PK. Both paths always filter `is_active = true`. `(source_catalog, source_schema)` uniqueness enforced in code |

## CI/CD & Deployment

| # | Date | Decision | Rationale |
|---|------|----------|-----------|
| D12 | 2026-03-26 | Write tests before implementation, derived from requirements | If tests are written after seeing the code, they test what the code does — not what it should do. Requirements-first tests catch design bugs that post-hoc tests miss |
| D19 | 2026-03-26 | Three-stage CI/CD: PR validation → deploy dev → promote | Unit tests run without Databricks (fast, free). Config validation happens at runtime against Delta tables. Same bundle promotes through environments |
| D21 | 2026-03-26 | Jobs run as service principal in higher environments | Dev runs as user (interactive testing). QA/Stage/Prod use `run_as` with a service principal. `current_user()` captures SP identity in audit — no code changes needed |
| D22 | 2026-03-26 | M2M OAuth for CI/CD deployment to higher environments | CI pipeline authenticates via `DATABRICKS_CLIENT_ID` / `DATABRICKS_CLIENT_SECRET` environment variables (stored in CI secrets, not repo) |
| D23 | 2026-03-26 | Databricks secret scopes for runtime credentials, not config tables | Warehouse IDs and external credentials change per workspace and are sensitive — they belong in secret scopes. Config Delta tables hold non-sensitive settings. **Note:** secret scope pipeline was later removed (D37) as dead code |

## Implementation Decisions (from code reviews)

Decisions made during implementation that aren't in the original requirements.

| # | Date | Decision | Rationale |
|---|------|----------|-----------|
| D38 | 2026-04-02 | Extract SQL quoting and INSERT builders into shared `utils.py` helpers (Rule 23) | 6 SQL quoting helpers + 2 INSERT builders extracted from `audit.py` and `scanner.py`. Archiver INSERT-SELECT left in place — structurally different (INSERT-SELECT, not INSERT-VALUES) and context-specific |
| D39 | 2026-04-06 | `get_last_run_state` filters to `ARCHIVED` and `ARCHIVED_AND_DELETED` only | Prior implementation returned `SKIPPED` rows with NULL watermark, causing incorrect append behavior. Only success statuses carry meaningful watermark values |
| D40 | 2026-04-06 | Archive count verification reads full archive, not just appended batch | After APPEND, verify total archive count (not just newly-appended rows). Self-consistency check — ensures the entire year folder is coherent, not just the latest write |
| D41 | 2026-04-09 | Diagnostic message templates on `ArchiveError` base class with graceful fallback | `diagnostic_message()` uses `str.format` with broad exception handling (`KeyError`, `TypeError`, `ValueError`) — never raises, always returns a usable string. Audit `error_message` keeps raw `str(exc)` for debugging; raised exception uses the formatted diagnostic |
| D42 | 2026-04-09 | `_audit_table_fq` uses backtick quoting; `build_full_table_name` does not | Audit table names use `` `catalog`.`schema`.`table` `` with backticks for safety with special characters. Other FQ names use plain dot notation. Intentional divergence — not duplication |
| D43 | 2026-04-16 | Keep `archiver._parse_conditions` and `config.validate_exclusion_conditions` as separate functions | Both handle `str/list/None` input with `json.loads` + `normalize_condition`, but serve different purposes: `_parse_conditions` is a tolerant deserializer (returns `[]` on bad input — archiver must keep running), while `validate_exclusion_conditions` is a strict validator (raises `ArchiveConfigError` — config loading must reject garbage early). Merging behind a `strict: bool` flag would create a function with two personalities, coupling different layers for ~5 lines of shared logic |
| D44 | 2026-04-16 | Extract shared `normalize_condition` into `conditions.py` | `_normalize_condition` (archiver) and `_to_condition_dict` (config) were identical — same branches, same key renames (`scope` → `type`), same `sql` pop. Extracted to `conditions.py` where it belongs alongside other condition-handling functions |
| D45 | 2026-04-16 | Rehydrator creates views instead of external tables | Unity Catalog blocks `CREATE TABLE LOCATION` on paths governed by UC Volumes. Views using `delta.\`path\`` syntax bypass this constraint and work with any path scheme. Acceptable for rehydration (occasional read-only access). Supersedes D33 for rehydration; archiving still writes via `CREATE TABLE delta.\`path\`` |
