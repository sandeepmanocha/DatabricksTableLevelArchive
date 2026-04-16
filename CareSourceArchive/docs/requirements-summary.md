# CareSource Delta Table Archive — Requirements Summary

## 1. Problem Statement

Active Databricks tables accumulate years of historical data, increasing storage costs and slowing query performance. CareSource needs a way to move older records to cost-effective storage while ensuring operationally relevant data stays accessible — and historical data can be restored when needed.

---

## 2. Requirements

What users should be able to do without writing code or making system changes:

- Add new tables for archiving through configuration only — no code changes
- Onboard an entire catalog or schema at once using a scanning tool that checks table sizes and skips tables below a configurable threshold [Discussed March 25, 2026]
- Turn archiving on or off per table with a simple flag
- Define business rules that keep certain records from being archived, even if they're old enough *
  - Based on flags within the table
  - Based on conditions from other tables
  - Data Row-level lineage - Not Covered, as this could be covered by the above two, and it will be a business rule rather than a technical requirement. The goal is to get the archive framework ready and enhance it as more use cases or exceptions become norms.
- Organize table configurations however they prefer — by catalog, by schema, by domain, or all in one file
- Preview exactly what would be archived and which rules are keeping records — before anything moves
- Restore historical data to any catalog and schema they choose, independently of other users
- Query current and restored data together through a single unified view
- Review all past archives and restore activity through audit logs
- Correlate audit entries with Databricks job runs for operational monitoring

---

## 3. Functional Requirements

### Archiving

- Automatically archive records older than a configurable retention period per table
- Organize archived data into independent year folders — each year is a self-contained dataset
- Write `_archive_metadata.json` alongside each year folder for provenance
- Evaluate exclusion conditions before archiving:
  - **Simple checks** on the same table: "Don't archive if `Status_Flag` = Active"
  - **Complex checks** via custom SQL: "Don't archive a member if they have a claim within the last 2 years" *
  - **Safety-first logic**: If *any* condition matches, the record stays
- Support archive-only mode (backup without deleting) or archive-and-delete mode per table
- Resume incomplete operations — if a previous run archived but didn't delete, pick up where it left off
- Health check to verify all expected archive folders exist in storage
- **Incremental archive via watermark** — every run uses the same logic regardless of `delete_after_archive` mode:
  - No folder → **CREATE**: write all eligible records for the year as a new Delta External table at the External Volume path
  - Folder exists + new data above watermark → **APPEND**: archive only new records (`wm_col > last_watermark`) to the existing year's Delta External table
  - Folder exists + no new data above watermark → **SKIP**: no work needed
  - No REPLACE mode — archive is strictly append-only; there is no `year_override` override
  - Watermark (`MAX(watermark_column)`) stored in audit on every write; used on the next run to detect new records
  - Rehydration is unaffected — external tables point to the year folder, and Delta reads all transactions including appends
  - **Monotonically increasing watermark column required** — the `date_column` must only increase over time (never back-filled or updated). This is a hard constraint operators must verify before configuring a table

### Restoring (Rehydration)

- Restore archived data on-demand to any catalog and schema — specified at runtime
- Multiple users can restore the same table to different destinations simultaneously
- Zero-copy restoration — rehydrated tables point to archived data, no duplication
- Optionally create a unified view combining current active data with restored historical data

### Safety and Observability

- **Dry-run mode (on by default)**: Shows what would happen per year, per table, per business rule — without moving or deleting any data
- **Verify before delete**: After writing to archive storage, confirm record counts match before deleting from source
- **Concurrency guards**: Before archiving a table+year, write a `STARTED` status to the audit table. If another job is already processing the same table+year, skip with `SKIPPED_CONCURRENT`. Verify the archive was written by this run before deleting from source
- **Audit logging**: Every archive, restore, and dry-run logged with:
  - Timestamp, record counts, conditions applied, who ran it, outcome
  - Enumerated status values: `STARTED`, `DRY_RUN`, `ARCHIVED`, `ARCHIVED_AND_DELETED`, `FAILED`, `SKIPPED`, `SKIPPED_CONCURRENT`, `NO_DATA` (archive) and `COMPLETED`, `PARTIAL_COMPLETED`, `FAILED` (rehydration)
  - `archive_run_id` (UUID) as the **durable correlation key** across audit rows, year folders, and `_archive_metadata.json` — survives after Lakeflow job history ages out of system tables
  - Databricks job context (workspace_id, job_id, job_run_id, task_run_id) for joining with `system.lakeflow.job_run_timeline` while retained; `archive_run_id` is the permanent key
- **Application logging**: Python `logging` module with structured format `[run_id][table]` at DEBUG/INFO/WARNING/ERROR levels. Visible in Databricks job run output and driver logs
- **Config validation**: Standalone notebook to validate all configuration before deploying changes
- **Ambiguous date column detection**: If a date column pattern matches more than one column in a table, the scanner flags it as ambiguous (`is_active: false`) rather than guessing — the operator adds a more specific pattern or manually sets the column
- **Scanner log table**: Every scan writes per-table detail rows to a `scanner_log` Delta table (separate from archive audit). Captures pattern matching results, ambiguity details, table sizes, and merge actions. Queryable via SQL and exportable to CSV/Excel for analysis across scan runs
- **Two-stage scanning**: Scanner writes discovered tables to a `table_configs_staging` Delta table, then MERGEs against the main `table_configs` table to detect manual edits, new tables, and dropped tables. Delta time travel on the staging table provides version history of schema evolution

### Error Handling

- **Config errors stop the job** — bad config (missing fields, invalid operators, duplicate table IDs, broken custom_sql) prevents any archiving from starting
- **Runtime errors fail one table** — if archiving fails for one table, that ForEach task fails but all other tables continue processing
- **Verification errors prevent data loss** — count mismatches or concurrent run detection abort the delete step for that table. Never silenced
- **Actionable error messages** — all errors include table identifier, year, operation, and root cause. Visible in audit table and job logs

### NULL and Edge Case Handling

- **NULL watermark columns**: The watermark column is a required value for archiving. Records with NULL watermark columns are excluded from archiving (`AND {watermark_column} IS NOT NULL`), remain in the source table, and the count is logged as an ERROR in both application logs and the audit table (`null_date_count` column). No configuration — NULL watermark values are always an error condition
- **Schema evolution**: Handled natively by Delta (merge-on-read for column additions). Type changes that break Delta compatibility surface as clear Spark errors
- **Timezone**: All date comparisons use Spark session timezone. Optional `timezone` field in settings (default: UTC)

### Rollback and Recovery

- **Rehydrate-as-rollback** — rehydration is the rollback mechanism. Restore year(s) back to the original catalog/schema
- **Delta time travel** — emergency backstop. After source deletion, records recoverable via `VERSION AS OF` for up to 30 days
- **Archive folder immutability** — year folders are append-only after creation (no REPLACE mode, no `year_override` override). If a folder must be corrected, the operator manually removes it and re-runs
- **Recommended onboarding** — run new tables with `delete_after_archive: false` for initial cycles, then switch to `true`
- **Safety layers in order**: dry-run first → archive-only mode → verify-before-delete → Delta time travel → rehydrate to restore

---

## 4. How It Works

### Archive Process

1. The system reads configuration to determine which tables are active for archiving
2. For each table, it calculates which years fall outside the retention window
3. It checks if any previous run needs to be resumed (archived but not deleted)
4. It evaluates exclusion conditions to identify records that should stay despite being old enough
5. In dry-run mode, it reports the results — in live mode, it writes eligible records to year-based archive folders with metadata
6. If configured to delete, it verifies the archive is complete before removing records from the source table
7. All activity is logged to the audit table with status, job context, and run correlation ID

### Rehydration Process

1. User provides runtime parameters: which table, which years, and where to restore
2. The system creates external tables (zero-copy pointers) to the archive folders
3. Optionally creates a unified view that combines the main table with restored years
4. All activity is logged to the audit table

---

## 5. Configuration (Delta Tables)

- **Stored in Delta tables**: All configuration is stored in Delta tables within the target environment's Unity Catalog. No JSON config files. Each workspace has its own config tables with the correct values for that environment.

- **Three config tiers** (values inherit downward — table configs can omit fields they get from higher tiers):

  1. **Global settings** (`global_settings` Delta table): Single-row table containing audit table location, default retention, concurrency, dry-run default, secret scope, archive path prefix, and pointers to the other config tables. The job receives this table's full name as a parameter — it is the bootstrap entry point.

  2. **Schema templates** (`schema_templates` Delta table): One row per schema, identified by a human-readable `schema_id` (PK). Each row also has a unique `(source_catalog, source_schema)` pair (enforced in code). Defines date column patterns (`ARRAY<STRING>`), default retention, archive paths (must be External Volume `/Volumes/` paths — see A12), tables to exclude (`ARRAY<STRING>`), and `min_table_size_gb` (required — minimum table size in GB for the scanner to mark a table as active; `0` disables size filtering). Scanner reads this table to discover and onboard tables — can target a single `schema_id` or scan all active templates.

  3. **Table configs** (`table_configs` Delta table): One row per table with archive settings. The archive job reads this table as primary input. Supports optional filter expressions for subset processing (e.g., `source_schema = 'claims'`). Exclusion conditions are stored as `ARRAY<STRUCT<name, scope, column, operator, value, sql>>`.

- **Change tracking**: Every config table has audit columns (`modified_by`, `modified_at`, `change_reason`) plus Delta time travel for full versioned history and rollback.

- **Initial setup (once per environment)**: Run the setup job (`resources/setup_job.yml`) to create the 4 config Delta tables and optionally seed initial data. Idempotent — safe to re-run. Required because DABs does not support declarative table creation. Parameters: `config_catalog`, `config_schema`, `seed_data` (bool).

- **Onboarding workflow**: To add a new schema with hundreds of tables:
  1. Add a row to the `schema_templates` Delta table with a `schema_id`, date column patterns, `min_table_size_gb`, and archive path
  2. Run the scanner — pass `schema_id` to target just the new template, or omit to scan all active. The scanner first validates that `archive_base_path` is covered by a Unity Catalog external location (fails immediately if not), then discovers tables, checks sizes via `DESCRIBE DETAIL`, and writes results to the `table_configs_staging` Delta table
  3. The scanner MERGEs staging into `table_configs`: adds new tables, updates scanner-managed entries, preserves manually edited rows (detected via `modified_by != 'scanner'`), marks dropped tables inactive
  4. Review the output, add exclusion conditions for tables that need them
  5. Tables below the size threshold, with unknown size, without a matching date column, or with an **ambiguous** date column match (same pattern matches multiple columns) are flagged `is_active = false` with a descriptive reason
  6. Tables that existed in the previous scan but are no longer in the schema are marked `is_active = false` with a reason noting the scan timestamp
  7. On re-scan, the process repeats — new staging rows are written, MERGEd against the final table. Manual edits (exclusion conditions, custom retention, etc.) are preserved because `modified_by` differs from `'scanner'`. Use `force=true` to overwrite the final config entirely

---

## 6. Assumptions (To Be Confirmed)

Captured during brainstorming. These shape the operational design (error handling, logging, rollback, CI/CD). Update as CareSource confirms or corrects.

| # | Assumption | Status |
|---|-----------|--------|
| A1 | **Operating team**: A small CareSource team (2-5 data engineers) will own the system after handoff. They run jobs, manage configs, and troubleshoot failures. They are technical but need clear error messages and logging to self-diagnose. | Discussed |
| A2 | **Scale**: Medium — 50-500 tables, some with 50-100M+ rows per year. Jobs could run 1-4 hours. | Assumed |
| A3 | **Compute**: Classic Spark cluster on Databricks (serverless is not enabled in this workspace). Auto-scaling handled by the platform. | Discussed |
| A4 | **Environments**: Standard stack is `dev`, `int`, `cert`, `prod`. An additional `dev2` exploratory environment will be used for initial development. A new catalog will be created for this project (`new_dev_catalog`). DABs targets map to environments. DABs manages Lakeflow Jobs, notebooks, and other code artifacts. Permissions are configured manually (not managed by DABs). | Discussed |
| A5 | **No restore capability built-in**: No custom restore/rollback mechanism is built into this implementation. For recovering deleted data, rely on Databricks-native features (Delta time travel, CLONE) and custom backup procedures. | Discussed |
| A6 | **Service principals**: Jobs in QA, Stage, and Prod run as service principals (not user accounts). Dev may run as user for interactive testing. SP provisioning and UC permission grants are handled by the CareSource platform team, not this project. CareSource has a portal for creating service principals. | Discussed |
| A7 | **M2M OAuth authentication**: CI/CD deploys to higher environments authenticate via M2M OAuth (`client_id` + `client_secret`) using the service principal. Credentials stored in CI pipeline secrets, not in the repository. | Discussed |
| A8 | **Databricks secret scopes**: Warehouse IDs, external credentials, and any sensitive runtime values are stored in Databricks secret scopes per environment (`archive-dev`, `archive-prod`, etc.). Secret scope creation and population are handled by the CareSource platform team. Code accesses via `dbutils.secrets.get()`. | Discussed |
| A9 | **Unity Catalog namespaces**: All table references use the three-level UC namespace: `catalog.schema.table`. | Discussed |
| A10 | **Incremental archive behavior**: Every run uses watermark-driven logic: CREATE (no folder), APPEND (new data above watermark), or SKIP (no new data). Both `delete_after_archive` modes use the same archive logic; the only difference is whether source records are deleted after archiving. No REPLACE mode — archive is append-only. No `year_override` override — if a folder must be corrected, manual operator intervention is required. | Confirmed |
| A11 | **Monotonically increasing watermark column** (Hard Constraint): The column configured as `date_column` MUST be monotonically increasing — new records always have a higher value than previously archived records. The column must never be back-filled, updated in place, or set to a value lower than existing entries. Tables that violate this constraint will silently under-archive during incremental append runs. Operators MUST verify this property before configuring a table for archiving. | **Hard Constraint** |
| A12 | **External Volumes as archive locations** (Hard Constraint): All archive storage uses Unity Catalog External Volumes exclusively. The `archive_base_path` in `schema_templates` must be a `/Volumes/{catalog}/{schema}/{volume_name}/` path. Direct cloud storage URLs (`s3://`, `abfss://`) are not supported. External Volumes are the Databricks best practice for external storage — they provide UC-governed access, consistent auditing, and no direct cloud credential exposure. The CareSource platform team provisions External Volumes per environment. | **Hard Constraint** |
| A13 | **Per-year watermark** (Needs Confirmation): The watermark is tracked per table **per year** — not globally per table. Each year's archive stores `MAX(watermark_column)` independently. On re-run, only rows where `watermark_column > that year's last MAX` are appended. **Implication:** If a record arrives in source with a date older than the year's archived MAX (e.g., a `2022-06-01` claim when the 2022 archive already has data through `2022-11-30`), **it will not be picked up**. It stays in source, unarchived. This is fine when the watermark column is always-increasing (`etl_load_date`), but matters if the column is a business date (`event_date`) where late arrivals are possible. **Question for CareSource:** Do your tables receive late-arriving records with backdated business dates? If so, should the watermark column be `etl_load_date` instead of the business date? | **Needs Confirmation** |

---

## 7. Technical Approach

- **Requirements-first tests**: For each module, write tests BEFORE writing implementation code. Tests are derived from the requirements (Section 2-3 above), not from the implementation. This prevents bias — tests verify what the system *should* do, not what it *happens* to do.
- **Selective OOP**: Classes for components with shared state (`AuditLogger`, `ArchiveEngine`); pure functions for stateless logic (config loading, condition SQL generation, schema scanning). Classes receive `spark` as a constructor parameter — the only platform dependency they need. Notebooks are the boundary layer: they obtain `spark` and `dbutils` from the platform, extract values from `dbutils` into plain dicts via `RunContext`, and pass `spark` + `RunContext` to class constructors. Classes never see `dbutils`. This enables unit testing with a mocked `spark`.
- **RunContext dataclass** bundles execution identity: settings, job context, and archive_run_id. Constructed once per notebook, passed to classes.
- **Custom exception hierarchy**: `ArchiveConfigError` (hard stop), `ArchiveOperationError` (fail one table), `ArchiveVerificationError` (prevent data loss). Defined in `src/exceptions.py`.
- **Package management**: `pyproject.toml` at project root with `setuptools`. No external dependencies. DABs deploys `src/` alongside notebooks via workspace file sync. Local dev uses `pip install -e .` + `pytest`.
- **Environment management**: DABs targets (`dev2`, `dev`, `int`, `cert`, `prod`). `dev2` is the exploratory environment used for initial development. Each environment has its own config Delta tables with the correct values — no overlay or merge logic. The `config_table` job parameter (pointing to `global_settings`) determines the environment.
- **CI/CD pipeline**: Three stages — (1) PR validation: unit tests + bundle validate (no Databricks needed); (2) Deploy to dev on merge; (3) Promote to int/cert/prod via manual trigger. Higher-environment deploys authenticate as service principal. Config validation happens at runtime against Delta tables.
- **Service principals**: In Int/Cert/Prod, jobs run as a service principal via DABs `run_as`. `current_user()` captures the SP identity in audit columns — no code changes needed between user and SP execution. SP provisioning via CareSource's portal; UC grants and storage ACLs provisioned outside this project.
- **M2M OAuth for CI/CD**: Higher-environment deployments authenticate via OAuth M2M (`DATABRICKS_CLIENT_ID` / `DATABRICKS_CLIENT_SECRET` env vars in CI). Same SP used for deployment and `run_as`.
- **Secret scopes for runtime credentials**: Warehouse IDs, external credentials, and sensitive runtime values stored in Databricks secret scopes (one per environment: `archive-{env}`). Code reads via `dbutils.secrets.get()`. Non-sensitive settings (catalogs, paths, retention) are stored in config Delta tables. The `global_settings` table includes a `secret_scope` field.
- Thin Databricks notebook wrappers as entry points — business logic stays in the modules
- Archive job runs on a schedule, processing all active tables in parallel (Classic Spark cluster)
