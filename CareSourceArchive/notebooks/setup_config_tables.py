# Databricks notebook source

# COMMAND ----------
# Parameters
dbutils.widgets.text("config_catalog", "", "Config Catalog")
dbutils.widgets.text("config_schema", "", "Config Schema")

config_catalog = dbutils.widgets.get("config_catalog").strip()
config_schema = dbutils.widgets.get("config_schema").strip()

# COMMAND ----------
if not config_catalog or not config_schema:
    raise ValueError("config_catalog and config_schema are required (non-empty).")

def _qual(cat: str, sch: str, table: str) -> str:
    return f"`{cat}`.`{sch}`.`{table}`"


def _apply_ns(sql: str) -> str:
    return sql.replace("{config_catalog}", config_catalog).replace("{config_schema}", config_schema)


# COMMAND ----------
# Verify catalog exists (created by platform team, not this job)
_cat_check = spark.sql(
    f"SELECT 1 FROM system.information_schema.catalogs WHERE catalog_name = '{config_catalog}'"
).collect()
if not _cat_check:
    raise ValueError(
        f"Catalog '{config_catalog}' does not exist. "
        "Contact the platform team to create it before running this job."
    )

# Create schema (idempotent)
spark.sql(_apply_ns("CREATE SCHEMA IF NOT EXISTS {config_catalog}.{config_schema}"))

# COMMAND ----------
DDL_GLOBAL_SETTINGS = """
CREATE TABLE IF NOT EXISTS {config_catalog}.{config_schema}.global_settings (
  audit_catalog            STRING    NOT NULL  COMMENT 'Unity Catalog for audit tables',
  audit_schema             STRING    NOT NULL  COMMENT 'Schema for audit tables',
  default_retention_years  INT       NOT NULL  COMMENT 'Default retention when table config omits it',
  dry_run_default          BOOLEAN   NOT NULL  COMMENT 'Default dry-run flag',
  timezone                 STRING    NOT NULL  COMMENT 'Spark session timezone for date comparisons',
  secret_scope             STRING    NOT NULL  COMMENT 'Databricks secret scope name for this environment',
  schema_templates_table   STRING    NOT NULL  COMMENT 'Full 3-level UC name of schema_templates table',
  table_configs_table      STRING    NOT NULL  COMMENT 'Full 3-level UC name of table_configs table',
  archive_base_path_prefix STRING    NOT NULL  COMMENT 'Cloud storage prefix for this environment',
  modified_by              STRING    NOT NULL  COMMENT 'Who last modified this row',
  modified_at              TIMESTAMP NOT NULL  COMMENT 'When last modified',
  change_reason            STRING              COMMENT 'Why the change was made'
)
COMMENT 'Archive system global settings — one row per environment. Bootstrap entry point for all jobs.'
"""

DDL_SCHEMA_TEMPLATES = """
CREATE TABLE IF NOT EXISTS {config_catalog}.{config_schema}.schema_templates (
  schema_id                STRING         NOT NULL  COMMENT 'Human-readable unique identifier for this template',
  source_catalog           STRING         NOT NULL  COMMENT 'Unity Catalog catalog name',
  source_schema            STRING         NOT NULL  COMMENT 'Unity Catalog schema name',
  watermark_column_patterns ARRAY<STRING>  NOT NULL  COMMENT 'Ordered list of regex patterns for watermark column matching',
  default_retention_years  INT            NOT NULL  COMMENT 'Default retention for tables in this schema',
  archive_base_path        STRING         NOT NULL  COMMENT 'Base archive storage path for this schema',
  delete_after_archive     BOOLEAN        NOT NULL  COMMENT 'Default delete behavior for tables in this schema',
  min_table_size_gb        DOUBLE         NOT NULL  COMMENT 'Minimum table size in GB for scanner to mark active (0 = no filtering)',
  exclude_tables           ARRAY<STRING>            COMMENT 'Table names to skip during scanning',
  description              STRING                   COMMENT 'Human-readable description of this schema group',
  is_active                BOOLEAN        NOT NULL  COMMENT 'Whether to include in scanner runs',
  modified_by              STRING         NOT NULL  COMMENT 'Who last modified this row',
  modified_at              TIMESTAMP      NOT NULL  COMMENT 'When last modified',
  change_reason            STRING                   COMMENT 'Why the change was made',
  CONSTRAINT pk_schema_templates PRIMARY KEY (schema_id)
)
COMMENT 'Scanner onboarding templates — one row per Unity Catalog schema to be scanned for archivable tables.'
"""

DDL_TABLE_CONFIGS = """
CREATE TABLE IF NOT EXISTS {config_catalog}.{config_schema}.table_configs (
  table_id                 STRING    NOT NULL  COMMENT 'Logical ID: catalog.schema.table',
  source_catalog           STRING    NOT NULL  COMMENT 'Unity Catalog catalog name',
  source_schema            STRING    NOT NULL  COMMENT 'Unity Catalog schema name',
  source_table             STRING    NOT NULL  COMMENT 'Table name',
  watermark_column         STRING              COMMENT 'Resolved watermark column for retention calculation (NULL if unmatched/ambiguous)',
  retention_years          INT                 COMMENT 'Table-specific retention (NULL = inherit from global_settings)',
  archive_base_path        STRING    NOT NULL  COMMENT 'Full archive storage path for this table',
  delete_after_archive     BOOLEAN   NOT NULL  COMMENT 'Whether to delete from source after successful archive',
  is_active                BOOLEAN   NOT NULL  COMMENT 'Include in archive runs',
  reason                   STRING              COMMENT 'Reason when inactive (scanner or manual)',
  exclusion_conditions     ARRAY<STRUCT<
    name:     STRING,
    scope:    STRING,
    column:   STRING,
    operator: STRING,
    value:    STRING,
    sql:      STRING
  >>                                           COMMENT 'Business rules to protect records from archiving',
  modified_by              STRING    NOT NULL  COMMENT 'Who last modified — scanner uses "scanner"',
  modified_at              TIMESTAMP NOT NULL  COMMENT 'When last modified',
  change_reason            STRING              COMMENT 'Why the change was made',
  scan_run_id              STRING              COMMENT 'UUID of the scanner run that last inserted or updated this row',

  CONSTRAINT pk_table_configs PRIMARY KEY (table_id)
)
COMMENT 'Per-table archive configuration — primary input for archive and dry-run jobs.'
"""

DDL_TABLE_CONFIGS_STAGING = """
CREATE TABLE IF NOT EXISTS {config_catalog}.{config_schema}.table_configs_staging (
  table_id                 STRING    NOT NULL  COMMENT 'Logical ID: catalog.schema.table',
  source_catalog           STRING    NOT NULL  COMMENT 'Unity Catalog catalog name',
  source_schema            STRING    NOT NULL  COMMENT 'Unity Catalog schema name',
  source_table             STRING    NOT NULL  COMMENT 'Table name',
  watermark_column         STRING              COMMENT 'Resolved watermark column (NULL if unmatched/ambiguous)',
  retention_years          INT                 COMMENT 'From schema template default',
  archive_base_path        STRING    NOT NULL  COMMENT 'From schema template',
  delete_after_archive     BOOLEAN   NOT NULL  COMMENT 'From schema template',
  is_active                BOOLEAN   NOT NULL  COMMENT 'Scanner determination',
  reason                   STRING              COMMENT 'Reason when inactive',
  exclusion_conditions     ARRAY<STRUCT<
    name:     STRING,
    scope:    STRING,
    column:   STRING,
    operator: STRING,
    value:    STRING,
    sql:      STRING
  >>                                           COMMENT 'Empty for scanner-generated rows',
  scan_run_id              STRING    NOT NULL  COMMENT 'UUID for this scanner invocation',
  scan_timestamp           TIMESTAMP NOT NULL  COMMENT 'When this scan ran'
)
COMMENT 'Scanner staging table — intermediate results before MERGE into table_configs.'
"""

DDL_SCANNER_LOG = """
CREATE TABLE IF NOT EXISTS {config_catalog}.{config_schema}.scanner_log (
  log_id                   STRING    NOT NULL  COMMENT 'Unique log entry ID',
  scan_run_id              STRING    NOT NULL  COMMENT 'UUID for this scanner invocation',
  table_id                 STRING    NOT NULL  COMMENT 'Logical ID: catalog.schema.table',
  source_catalog           STRING    NOT NULL  COMMENT 'Unity Catalog catalog name',
  source_schema            STRING    NOT NULL  COMMENT 'Unity Catalog schema name',
  source_table             STRING    NOT NULL  COMMENT 'Table name',
  match_status             STRING    NOT NULL  COMMENT 'matched | ambiguous | unmatched | excluded',
  matched_column           STRING              COMMENT 'Resolved date column (NULL if unmatched/ambiguous)',
  matched_pattern          STRING              COMMENT 'Pattern that produced the match',
  all_matched_columns      STRING              COMMENT 'JSON array of all columns matched by the pattern',
  ambiguity_detail         STRING              COMMENT 'Detail when match_status is ambiguous',
  table_size_gb            DOUBLE              COMMENT 'Table size from DESCRIBE DETAIL',
  size_check_passed        BOOLEAN             COMMENT 'Whether table passed min_table_size_gb threshold',
  is_active                BOOLEAN   NOT NULL  COMMENT 'Whether the scanner marked this table active',
  inactive_reason          STRING              COMMENT 'Reason when is_active is false',
  merge_action             STRING              COMMENT 'added | updated | preserved',
  workspace_id             STRING              COMMENT 'Databricks workspace ID',
  scanned_by               STRING              COMMENT 'User who ran the scan',
  created_at               TIMESTAMP NOT NULL  COMMENT 'When this log entry was created'
)
CLUSTER BY (source_table)
TBLPROPERTIES (
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
)
COMMENT 'Scanner audit log — one row per table per scan run.'
"""

DDL_ARCHIVE_AUDIT_LOG = """
CREATE TABLE IF NOT EXISTS {config_catalog}.{config_schema}.archive_audit_log (
  audit_id                 STRING              COMMENT 'Unique audit entry ID',
  archive_run_id           STRING              COMMENT 'UUID for the archive job invocation',
  table_name               STRING              COMMENT 'Fully qualified source table name',
  year                     INT                 COMMENT 'Year partition being archived',
  status                   STRING              COMMENT 'STARTED | ARCHIVED | ARCHIVED_AND_DELETED | FAILED | SKIPPED | SKIPPED_CONCURRENT | NO_DATA | DRY_RUN',
  record_count             BIGINT              COMMENT 'Number of records affected',
  conditions_applied       STRING              COMMENT 'JSON payload of exclusion conditions and counts',
  null_date_count          BIGINT              COMMENT 'Records with NULL in the date column',
  error_message            STRING              COMMENT 'Error details when status is FAILED',
  watermark_value          DATE                COMMENT 'MAX(watermark_column) from archived records this run',
  source_year_count        BIGINT              COMMENT 'COUNT(*) from source for this year at time of run',
  archive_mode             STRING              COMMENT 'How the write was performed: CREATE | APPEND | SKIP',
  archived_by              STRING              COMMENT 'User who ran the archive (current_user())',
  workspace_id             STRING              COMMENT 'Databricks workspace ID',
  job_id                   STRING              COMMENT 'Job ID from job context',
  job_run_id               STRING              COMMENT 'Job run ID from job context',
  task_run_id              STRING              COMMENT 'Task run ID from job context',
  created_at               TIMESTAMP           COMMENT 'When this audit entry was created'
)
CLUSTER BY (table_name, year)
TBLPROPERTIES (
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
)
COMMENT 'Archive operation audit log — one row per table per year per run.'
"""

DDL_REHYDRATION_AUDIT_LOG = """
CREATE TABLE IF NOT EXISTS {config_catalog}.{config_schema}.rehydration_audit_log (
  audit_id                 STRING              COMMENT 'Unique audit entry ID',
  archive_run_id           STRING              COMMENT 'UUID for the rehydration job invocation',
  archive_path             STRING              COMMENT 'Archive storage path being rehydrated',
  source_table             STRING              COMMENT 'Original source table name',
  target_catalog           STRING              COMMENT 'Destination catalog for rehydrated tables',
  target_schema            STRING              COMMENT 'Destination schema for rehydrated tables',
  years                    STRING              COMMENT 'Comma-separated list of years rehydrated',
  tables_created           INT                 COMMENT 'Number of external/clone tables created',
  status                   STRING              COMMENT 'COMPLETED | FAILED',
  error_message            STRING              COMMENT 'Error details when status is FAILED',
  rehydrated_by            STRING              COMMENT 'User who ran the rehydration (current_user())',
  workspace_id             STRING              COMMENT 'Databricks workspace ID',
  job_id                   STRING              COMMENT 'Job ID from job context',
  job_run_id               STRING              COMMENT 'Job run ID from job context',
  task_run_id              STRING              COMMENT 'Task run ID from job context',
  created_at               TIMESTAMP           COMMENT 'When this audit entry was created'
)
CLUSTER BY (source_table)
TBLPROPERTIES (
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
)
COMMENT 'Rehydration operation audit log — one row per rehydration request.'
"""

for _ddl in (
    DDL_GLOBAL_SETTINGS,
    DDL_SCHEMA_TEMPLATES,
    DDL_TABLE_CONFIGS,
    DDL_TABLE_CONFIGS_STAGING,
    DDL_SCANNER_LOG,
    DDL_ARCHIVE_AUDIT_LOG,
    DDL_REHYDRATION_AUDIT_LOG,
):
    spark.sql(_apply_ns(_ddl))

# COMMAND ----------
# Post-creation validation: confirm tables exist and summarize row counts
TABLE_NAMES = (
    "global_settings",
    "schema_templates",
    "table_configs",
    "table_configs_staging",
    "scanner_log",
    "archive_audit_log",
    "rehydration_audit_log",
)

summary_rows = []
for name in TABLE_NAMES:
    fq = _qual(config_catalog, config_schema, name)
    spark.sql(f"SELECT 1 FROM {fq} LIMIT 1")
    cnt = spark.sql(f"SELECT COUNT(1) AS c FROM {fq}").collect()[0]["c"]
    summary_rows.append((name, fq, int(cnt)))

html = [
    "<h3>CareSource Archive — config tables</h3>",
    f"<p><b>Catalog</b> <code>{config_catalog}</code> &nbsp; <b>Schema</b> <code>{config_schema}</code> &nbsp; <i>DDL only — run <code>seed_config</code> for sample config if needed.</i></p>",
    "<table border='1' cellpadding='6' cellspacing='0' style='border-collapse:collapse;font-family:monospace'>",
    "<tr><th>Table</th><th>Fully qualified name</th><th>Row count</th></tr>",
]
for name, fq, cnt in summary_rows:
    html.append(f"<tr><td>{name}</td><td>{fq}</td><td>{cnt}</td></tr>")
html.append("</table>")
displayHTML("".join(html))
